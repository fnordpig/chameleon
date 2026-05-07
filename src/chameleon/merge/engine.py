"""Merge engine — the round-trip orchestrator (§4.3 pipeline).

V0 implements a simplified version of the full §4.3 pipeline:
  - Sampling, disassemble, drift detect, classify, resolve, compose,
    re-derive, write live, commit state-repos, update neutral.
  - Interactive resolution is deferred; V0 accepts only Strategy
    (non-interactive).
  - Change classification (P2-1) is per-FieldPath via
    ``walk_changes``: each leaf in the neutral schema becomes its own
    ``ChangeRecord``, and ``dict[TargetId, V]`` fields split into one
    record per ``TargetId`` key — see ``merge/changeset.py``.
"""

from __future__ import annotations

import hashlib
import os
import types
import typing
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter

from chameleon._types import FileOwnership, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge._diffs import FileDiff
from chameleon.merge.changeset import (
    ChangeOutcome,
    ChangeRecord,
    _serialize,
    classify_change,
    walk_changes,
)
from chameleon.merge.conflict import Conflict
from chameleon.merge.resolutions import (
    compute_decision_hash,
    parse_resolution_key,
    render_change_path,
)
from chameleon.merge.resolve import (
    NonInteractiveResolver,
    Resolver,
    Strategy,
)
from chameleon.schema._constants import Domains
from chameleon.schema.neutral import (
    Neutral,
    Resolution,
    ResolutionDecisionKind,
    Resolutions,
)
from chameleon.schema.passthrough import PassThroughBag
from chameleon.state.git import GitRepo
from chameleon.state.paths import StatePaths
from chameleon.state.transaction import (
    MergeTransaction,
    TransactionStore,
    transaction_id,
)
from chameleon.targets._protocol import Target
from chameleon.targets._registry import TargetRegistry


def _resolve_parent(root: BaseModel, segments: tuple[str, ...]) -> BaseModel | None:
    """Walk to the parent node of the leaf identified by ``segments``.

    Each intermediate segment names a Pydantic field. If any intermediate
    field's value is ``None`` (e.g. an ``Optional[BaseModel]`` that was
    never authored — like ``interface.voice`` when the operator hasn't
    set up voice configuration), this returns ``None``. Callers must
    handle that case: ``_read_leaf`` returns ``None``; ``_write_leaf``
    materializes the default-constructed intermediate model in place,
    then re-resolves.
    """
    node: BaseModel | None = root
    for seg in segments[:-1]:
        if node is None:
            return None
        node = getattr(node, seg)
    return node


def _read_leaf(
    root: BaseModel,
    segments: tuple[str, ...],
    target_key: TargetId | None,
    dict_key: str | None = None,
) -> object:
    """Read a leaf value, optionally indexed by ``target_key`` or ``dict_key``.

    ``target_key`` indexes into a ``dict[TargetId, V]``; ``dict_key`` indexes
    into a ``dict[str, V]`` (issue #44 — see ``merge/changeset.py`` module
    docstring). The two are mutually exclusive — the walker emits exactly
    one of them per record. An unset Optional[BaseModel] anywhere on the
    path resolves to ``None`` for the leaf.
    """
    parent = _resolve_parent(root, segments)
    if parent is None:
        return None
    leaf = getattr(parent, segments[-1])
    if target_key is not None:
        if not isinstance(leaf, dict):
            return None
        return leaf.get(target_key)
    if dict_key is not None:
        if not isinstance(leaf, dict):
            return None
        return leaf.get(dict_key)
    return leaf


def _materialize_intermediate_models(root: BaseModel, segments: tuple[str, ...]) -> BaseModel:
    """Walk ``segments`` (excluding leaf) and instantiate any ``None``
    intermediate ``Optional[BaseModel]`` field with its default-constructed
    submodel, in place. Returns the parent of the leaf, now non-None.

    Used by ``_write_leaf`` so we can write under previously-unset nested
    models (e.g. set ``interface.voice.enabled`` when ``interface.voice``
    was ``None``). Each materialization uses the field's annotation, walking
    through ``X | None`` unions to find the first ``BaseModel`` subclass.
    """
    node: BaseModel = root
    for seg in segments[:-1]:
        next_node = getattr(node, seg)
        if next_node is None:
            field = type(node).model_fields[seg]
            anno = field.annotation
            inner_cls: type[BaseModel] | None = None
            if isinstance(anno, type) and issubclass(anno, BaseModel):
                inner_cls = anno
            else:
                # Walk Optional[X] / X | None / Union[X, ...] to find a BaseModel.
                for arg in getattr(anno, "__args__", ()):
                    if isinstance(arg, type) and issubclass(arg, BaseModel):
                        inner_cls = arg
                        break
            if inner_cls is None:
                msg = (
                    f"cannot materialize intermediate {seg!r}: annotation "
                    f"{anno!r} has no BaseModel arm"
                )
                raise TypeError(msg)
            next_node = inner_cls()
            setattr(node, seg, next_node)
        node = next_node
    return node


def _strip_optional(annotation: object) -> object:
    """Return ``annotation`` with any ``| None`` arm removed.

    Schema fields are routinely typed ``X | None``; the field's
    annotation comes back as a ``Union[X, None]`` (PEP 604 ``X | None``
    surfaces as ``types.UnionType``). For coercion we want the inner
    ``X`` (e.g. ``dict[TargetId, str]``), so the dict-arm-detection code
    can pull V out of it. If the union has no None arm or isn't a union
    at all, the annotation is returned unchanged.
    """
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = tuple(a for a in typing.get_args(annotation) if a is not type(None))
        if len(non_none) == 1:
            return non_none[0]
        # Pathological multi-arm union — leave for TypeAdapter to handle.
    return annotation


def _coerce_through_annotation(annotation: object, value: object) -> object:
    """Run ``value`` through ``TypeAdapter(annotation).validate_python``.

    Pydantic's ``TypeAdapter`` handles ``X | None``, ``Enum``, ``Literal``,
    ``list[X]``, ``dict[X, Y]``, and nested ``BaseModel`` subclasses
    uniformly — accepting the already-coerced object as a no-op or
    coercing a raw scalar / dict into the schema-appropriate type. The
    engine relies on that uniformity rather than special-casing each
    annotation kind.
    """
    return TypeAdapter(annotation).validate_python(value)


def _write_leaf(
    root: BaseModel,
    segments: tuple[str, ...],
    target_key: TargetId | None,
    value: object,
    dict_key: str | None = None,
) -> None:
    """Write ``value`` at the leaf, preserving keyed-dict siblings.

    For target-keyed leaves we mutate (or initialize) the inner dict so
    other ``TargetId`` keys present on ``composed`` are preserved. The
    same rule applies to ``dict_key`` (issue #44): we mutate just that
    str-keyed entry, leaving sibling keys untouched.

    Schema-aware coercion (B3): ``setattr`` bypasses Pydantic's
    field-level validators, so the resolver's raw return values
    (``str`` for an ``Enum`` field, ``dict`` for a nested model, etc.)
    must be funneled through ``TypeAdapter(annotation).validate_python``
    before assignment. Without that, downstream codecs that call
    ``.value`` on what they assume is an ``Enum`` member crash on the
    raw ``str``.

    For keyed-dict leaves the coerced annotation is the dict's value
    type (extracted via ``typing.get_args``), not the dict annotation
    itself — we're writing one V at a time, not the whole dict.
    """
    parent = _materialize_intermediate_models(root, segments)
    leaf_name = segments[-1]
    field_annotation = type(parent).model_fields[leaf_name].annotation

    if target_key is None and dict_key is None:
        # Scalar leaf: coerce through the field's full annotation
        # (which already includes any ``| None`` arm, so ``value=None``
        # round-trips cleanly for Optional fields).
        coerced = _coerce_through_annotation(field_annotation, value)
        setattr(parent, leaf_name, coerced)
        return

    current = getattr(parent, leaf_name)
    if not isinstance(current, dict):
        current = {}
    # Mutually exclusive by walker contract: exactly one of target_key /
    # dict_key is set when this branch runs. The narrowing here proves it
    # to the type checker without a leaking type-ignore comment.
    key: TargetId | str
    if target_key is not None:
        key = target_key
    else:
        assert dict_key is not None, "_write_leaf called without target_key or dict_key"
        key = dict_key
    if value is None:
        # Resolver returned no value for this key — drop it rather than
        # writing None, which would break round-trip equality.
        current.pop(key, None)
    else:
        # The dict-keyed leaf's annotation is e.g. ``dict[TargetId, str]``
        # or ``dict[TargetId, str] | None`` — strip Optional, then pull
        # V out of the dict's type args so we coerce one entry at a time.
        dict_annotation = _strip_optional(field_annotation)
        type_args = typing.get_args(dict_annotation)
        if len(type_args) == 2:  # ``dict[K, V]`` always exposes exactly 2 type args.
            value_annotation = type_args[1]
            current[key] = _coerce_through_annotation(value_annotation, value)
        else:
            # Annotation isn't a recognizable ``dict[K, V]`` — fall back
            # to the raw write and let Pydantic catch a type mismatch on
            # the final ``setattr``.
            current[key] = value
    # ``dict[TargetId, V]`` is typed ``dict | None`` on most schemas
    # (e.g. ``identity.model``); collapse to ``None`` when empty so the
    # field's nullability invariant holds. ``dict[str, V]`` schema fields
    # default to ``{}`` (e.g. ``capabilities.plugins``); keep the empty
    # dict rather than ``None`` for those, otherwise Pydantic validation
    # rejects the model on the next deep-copy or model_validate.
    if not current and target_key is not None:
        setattr(parent, leaf_name, None)
    else:
        setattr(parent, leaf_name, current)


def _resolved_value_from_resolution(
    resolution: Resolution,
    record: ChangeRecord,
    per_target_neutral: dict[TargetId, Neutral],
) -> object:
    """Compute the leaf value to apply when replaying a non-TARGET_SPECIFIC decision.

    Mirrors what the resolver would have returned for the same decision
    kind, but reads in-Python values from ``per_target_neutral`` /
    ``record`` rather than re-running the resolver. ``TARGET_SPECIFIC``
    is handled by ``_apply_target_specific`` instead. ``SKIP`` is never
    replayed (the engine bypasses this helper for SKIP).
    """
    kind = resolution.decision
    if kind is ResolutionDecisionKind.TAKE_NEUTRAL:
        return record.n1
    if kind is ResolutionDecisionKind.TAKE_LKG:
        return record.n0
    if kind is ResolutionDecisionKind.TAKE_TARGET:
        tid = resolution.decision_target
        if tid is None:
            msg = (
                f"resolution at {record.render_path()!r} declares TAKE_TARGET "
                "but no decision_target is set"
            )
            raise ValueError(msg)
        # Pull the in-Python leaf out of the target's neutral so Pydantic
        # types (Enums, nested models) survive into the write step.
        return _read_leaf(
            per_target_neutral[tid],
            record.path.segments,
            record.target_key,
            record.dict_key,
        )
    msg = f"cannot replay resolution kind {kind!r} via _resolved_value_from_resolution"
    raise ValueError(msg)


def _apply_target_specific(
    record: ChangeRecord,
    composed: Neutral,
    per_target_overlay: dict[TargetId, Neutral],
    per_target_neutral: dict[TargetId, Neutral],
) -> None:
    """Plumb a TARGET_SPECIFIC resolution through composed + overlays.

    Per resolution-memory spec §2.1 + §2.2: for a TARGET_SPECIFIC
    decision, harvest each target's current per-target value into
    ``composed.targets[tid].target_specific[<path>]`` (so it survives
    into next merge), and patch each target's overlay at the unified
    path so its codec sees the target-namespaced value while the unified
    composed path stays unset.

    The unified leaf in ``composed`` is left at whatever ``n1`` carried
    (typically the schema default for an unauthored leaf). Each target's
    overlay receives just that target's own per-target value — never any
    other target's — so cross-target propagation is genuinely disabled.
    """
    path_str = render_change_path(record)
    for tid, value in record.per_target.items():
        # Persist into composed.targets[tid].target_specific so the
        # operator and the next merge can see what we recorded.
        existing_bag = composed.targets.get(tid, PassThroughBag())
        ts = dict(existing_bag.target_specific)
        ts[path_str] = value
        composed.targets[tid] = PassThroughBag.model_validate(
            {"items": existing_bag.items, "target_specific": ts}
        )
        # Patch this target's overlay so its codec sees the target-
        # namespaced value at the unified slot.
        if tid in per_target_overlay:
            # Use the in-Python value from per_target_neutral (preserves
            # Pydantic types) rather than the JSON-serialized form on
            # ``record.per_target``.
            live_leaf = _read_leaf(
                per_target_neutral[tid],
                record.path.segments,
                record.target_key,
                record.dict_key,
            )
            if live_leaf is None:
                # Fall back to the serialized record value when the
                # in-Python leaf isn't available (defensive — record-only
                # callsites such as resolution replay).
                live_leaf = value
            _write_leaf(
                per_target_overlay[tid],
                record.path.segments,
                record.target_key,
                live_leaf,
                record.dict_key,
            )


def _apply_resolution_value(
    outcome_value: object,
    record: ChangeRecord,
    composed: Neutral,
    per_target_overlay: dict[TargetId, Neutral],
) -> None:
    """Write a single resolved value to composed + every per-target overlay."""
    _write_leaf(
        composed,
        record.path.segments,
        record.target_key,
        outcome_value,
        record.dict_key,
    )
    for overlay in per_target_overlay.values():
        _write_leaf(
            overlay,
            record.path.segments,
            record.target_key,
            outcome_value,
            record.dict_key,
        )


def _gc_resolutions(composed: Neutral, per_target_neutral: dict[TargetId, Neutral]) -> None:
    """Prune stored resolutions whose disagreement has resolved itself.

    Walks ``composed.resolutions.items`` and removes entries where the
    unified neutral value at the path now equals every per-target value
    (i.e. there is no current cross-target disagreement). Per spec §1
    "GC" — runs only on successful merges; failed merges leave entries
    intact so the operator can retry.

    TARGET_SPECIFIC entries are GC'd when the per-target values have
    converged with each other AND with the unified path; otherwise the
    operator's "preserve separately" intent remains active.
    """
    if not composed.resolutions.items:
        return
    survivors: dict[str, Resolution] = {}
    for path_str, resolution in composed.resolutions.items.items():
        try:
            parsed = parse_resolution_key(path_str)
        except ValueError:
            # Malformed key — preserve rather than silently drop.
            survivors[path_str] = resolution
            continue
        unified_leaf = _read_leaf(
            composed,
            parsed.path.segments,
            parsed.target_key,
            parsed.dict_key,
        )
        unified_serialized = _serialize(unified_leaf)
        all_match = True
        for tn in per_target_neutral.values():
            target_leaf = _read_leaf(
                tn,
                parsed.path.segments,
                parsed.target_key,
                parsed.dict_key,
            )
            if _serialize(target_leaf) != unified_serialized:
                all_match = False
                break
        if all_match:
            # Disagreement is gone — drop the entry.
            continue
        survivors[path_str] = resolution
    composed.resolutions = Resolutions(items=survivors)


class MergeRequest(BaseModel):
    """Inputs to a merge run beyond what the engine already knows."""

    model_config = ConfigDict(frozen=True)

    profile_name: str | None = None
    dry_run: bool = False


class MergeResult(BaseModel):
    """Outcome of a merge run.

    ``warnings`` collects every ``LossWarning`` emitted during the merge —
    both the disassemble fan-out (P0-2: per-domain ``ValidationError``s
    routed to pass-through) and any codec-emitted lossy-encoding warnings.
    The CLI surfaces these to stderr so the operator can see what was
    skipped without having the merge itself fail.
    """

    model_config = ConfigDict(frozen=True)

    exit_code: int
    summary: str
    merge_id: str | None = None
    warnings: list[LossWarning] = []
    # Populated only on dry-run: one entry per live target file the engine
    # would have written, with the bytes-before / bytes-after pair the CLI
    # turns into a unified diff. Empty for non-dry-run callers so they pay
    # no allocation cost.
    diffs: list[FileDiff] = []


class MergeEngine:
    def __init__(
        self,
        targets: TargetRegistry,
        paths: StatePaths,
        strategy: Strategy | None = None,
        *,
        resolver: Resolver | None = None,
    ) -> None:
        if resolver is None and strategy is None:
            msg = "MergeEngine requires either a strategy or a resolver"
            raise ValueError(msg)
        self.targets = targets
        self.paths = paths
        if resolver is not None:
            self._resolver: Resolver = resolver
        else:
            assert strategy is not None  # narrows for the type checker
            self._resolver = NonInteractiveResolver(strategy)
        self.tx_store = TransactionStore(paths.tx_dir)

    def _read_live_files(self, target_cls: type[Target]) -> dict[str, bytes]:
        """Read a target's live config files into a dict keyed by repo_path.

        Files that don't exist are omitted from the result (rather than
        mapped to empty bytes), so disassembler-side `load_json("{}")`
        defaults take effect.
        """
        out: dict[str, bytes] = {}
        for spec in target_cls.assembler.files:
            live = Path(os.path.expanduser(spec.live_path))
            if live.exists():
                out[spec.repo_path] = live.read_bytes()
        return out

    def _ensure_state_repo(self, target_id: TargetId) -> GitRepo:
        repo_path = self.paths.target_repo(target_id)
        if (repo_path / ".git").exists():
            return GitRepo(repo_path)
        return GitRepo.init(repo_path)

    def merge(self, request: MergeRequest) -> MergeResult:  # noqa: PLR0912, PLR0915
        # 1. Load N1 and N0
        #
        # We load the raw YAML view alongside the validated model so the
        # walker can honour the "operator omission ≠ explicit deletion"
        # rule (issue #44 — see merge/changeset.py module docstring):
        # Pydantic's ``model_validate`` collapses missing fields and
        # explicit defaults into identical defaulted values, so the
        # walker needs the raw dict to tell them apart.
        n1_raw: dict[str, object] | None = None
        if self.paths.neutral.exists():
            raw = load_yaml(self.paths.neutral)
            n1 = Neutral.model_validate(raw)
            if isinstance(raw, dict):
                # ruamel returns CommentedMap (a dict subclass) — coerce
                # to a plain ``dict[str, object]`` so the typed
                # ``walk_changes`` parameter accepts it.
                n1_raw = {str(k): v for k, v in raw.items()}
        else:
            n1 = Neutral(schema_version=1)

        if self.paths.lkg.exists():
            n0 = Neutral.model_validate(load_yaml(self.paths.lkg))
        else:
            n0 = Neutral(schema_version=1)

        # 2. Sample + disassemble + reverse-codec per target
        ctx = TranspileCtx(profile_name=request.profile_name)
        per_target_neutral: dict[TargetId, Neutral] = {}
        per_target_passthrough: dict[TargetId, dict[str, object]] = {}

        for tid in self.targets.target_ids():
            target_cls = self.targets.get(tid)
            if target_cls is None:
                continue
            live = self._read_live_files(target_cls)
            # P0-2: pass `ctx` so per-domain ValidationError surfaces as a
            # LossWarning (collected on the same ctx codecs already use)
            # rather than aborting the whole merge.
            domains, passthrough = target_cls.assembler.disassemble(live, ctx=ctx)
            per_target_passthrough[tid] = dict(passthrough)

            target_neutral = Neutral(schema_version=1)
            for codec_cls in target_cls.codecs:
                if codec_cls.domain not in domains:
                    continue
                try:
                    fragment = codec_cls.from_target(domains[codec_cls.domain], ctx)
                except NotImplementedError:
                    continue
                setattr(target_neutral, codec_cls.domain.value, fragment)

            per_target_neutral[tid] = target_neutral

        # 3-5. Classify per FieldPath (P2-1) and gather conflicts.
        #
        # `walk_changes` produces one ChangeRecord per leaf path, with a
        # special-case for `dict[TargetId, V]` and `dict[str, V]` fields
        # (one record per dict key, scoped to that key's evidence). We
        # apply consensual outcomes by writing the chosen leaf value
        # into `composed`, preserving sibling keys on keyed dicts.
        #
        # Per-target preservation overlay (issue #44)
        # -------------------------------------------
        # When the operator didn't author a path AND the per-key
        # classifier finds a CONFLICT (two targets enumerate the same
        # key with different values — common for cross-target codecs
        # that normalize the same source differently, e.g.
        # ``capabilities.plugin_marketplaces`` where Claude renders a
        # GitHub repo as ``kind=github`` and Codex renders the same
        # repo as ``kind=git, url=...``), the engine MUST NOT promote
        # one target's value as the canonical neutral form, but it MUST
        # preserve each target's own value on its own re-derived file.
        # ``per_target_overlay[tid]`` accumulates those per-target leaf
        # values; the re-derive loop layers them on top of ``composed``
        # before invoking each target's codec stack.
        merge_id = transaction_id()
        conflicts: list[Conflict] = []
        composed = n1.model_copy(deep=True)
        per_target_overlay: dict[TargetId, Neutral] = {
            tid: composed.model_copy(deep=True) for tid in per_target_neutral
        }

        records = walk_changes(n0, n1, per_target_neutral, n1_authored=n1_raw)
        # Resolution-memory bookkeeping (Wave-15 §1).
        #
        # ``existing_resolutions`` is a snapshot of the persisted decisions
        # at the start of this merge — we look entries up by the same
        # ``render_path()`` key the walker emits. ``new_resolutions``
        # accumulates outcomes the resolver returns with ``persist=True``
        # during this run; we copy ``existing_resolutions`` into it (less
        # any auto-applied entries that no longer match) and write the
        # union back into ``composed`` at the end. The non-interactive
        # ``persist=False`` rule from spec §3 means batch strategies leave
        # the dict untouched.
        existing_resolutions: dict[str, Resolution] = dict(n1.resolutions.items)
        new_resolutions: dict[str, Resolution] = dict(existing_resolutions)
        for rec in records:
            cl = classify_change(rec)
            if cl.outcome is ChangeOutcome.UNCHANGED:
                # Operator-omission rule (issue #44): if the operator
                # didn't author this leaf in raw YAML, ``composed`` —
                # which is seeded from N₁ — is missing the value the
                # walker substituted (n0). Write n0's in-Python value so
                # composed reflects the unchanged-from-LKG state. This
                # is a no-op for authored leaves (they already match in
                # composed via the n1 deep-copy).
                if not rec.authored:
                    n0_leaf = _read_leaf(n0, rec.path.segments, rec.target_key, rec.dict_key)
                    _write_leaf(
                        composed,
                        rec.path.segments,
                        rec.target_key,
                        n0_leaf,
                        rec.dict_key,
                    )
                    for overlay in per_target_overlay.values():
                        _write_leaf(
                            overlay,
                            rec.path.segments,
                            rec.target_key,
                            n0_leaf,
                            rec.dict_key,
                        )
                continue
            if cl.outcome is ChangeOutcome.CONFLICT:
                # Resolution-memory lookup (Wave-15 §1).
                #
                # Before queueing a CONFLICT for the resolver, check
                # whether the operator has already decided the same
                # disagreement. If the stored decision's hash matches
                # the current record's hash, apply silently. If the hash
                # has drifted, surface the prior decision on the
                # ``Conflict`` so an interactive resolver (W15-B) can
                # render it as a default. ``SKIP`` decisions never
                # auto-apply per spec §1.
                rec_path_str = render_change_path(rec)
                prior = existing_resolutions.get(rec_path_str)
                if prior is not None:
                    rec_hash = compute_decision_hash(rec)
                    if prior.decision_hash == rec_hash:
                        if prior.decision is ResolutionDecisionKind.SKIP:
                            # SKIP is intentionally non-replaying (§1):
                            # the operator left it unresolved last time
                            # and we don't change that decision silently.
                            # Fall through to the per-target preservation
                            # branch below — the resolver will be invoked
                            # again next time too.
                            pass
                        elif prior.decision is ResolutionDecisionKind.TARGET_SPECIFIC:
                            _apply_target_specific(
                                rec, composed, per_target_overlay, per_target_neutral
                            )
                            ctx.warn(
                                LossWarning(
                                    domain=rec.domain,
                                    target=next(iter(rec.per_target.keys())),
                                    message=(
                                        f"{rec.render_path()}: target-specific by operator "
                                        f"decision; not propagating cross-target"
                                    ),
                                    field_path=rec.path,
                                )
                            )
                            continue
                        else:
                            replay_value = _resolved_value_from_resolution(
                                prior, rec, per_target_neutral
                            )
                            _apply_resolution_value(replay_value, rec, composed, per_target_overlay)
                            continue
                # Either no stored decision or hash drifted — queue the
                # conflict, attaching the prior decision when present so
                # an interactive resolver can render it.
                conflicts.append(Conflict(record=rec, prior_decision=prior))
                # Per-target preservation: when neither N₀ nor N₁ has an
                # opinion on this key (both empty / unauthored) but two
                # or more targets disagree, write each target's own value
                # into its overlay so re-derive emits what each target
                # already had on disk. Without this, KEEP wipes both
                # targets' independent state on every init/merge cycle.
                if not rec.authored:
                    for tid_pt, tn in per_target_neutral.items():
                        leaf_pt = _read_leaf(tn, rec.path.segments, rec.target_key, rec.dict_key)
                        if leaf_pt is None:
                            continue
                        _write_leaf(
                            per_target_overlay[tid_pt],
                            rec.path.segments,
                            rec.target_key,
                            leaf_pt,
                            rec.dict_key,
                        )
                continue
            # CONSENSUAL: if the winning side is a target, look up the
            # actual leaf value on that target's neutral (the serialized
            # `cl.resolved_value` is for compare-equality only — we want
            # the in-Python value to keep Pydantic types intact). For a
            # target-keyed leaf, the lookup honours `target_key`.
            if cl.winning_target is not None:
                src_neutral = per_target_neutral[cl.winning_target]
                live_leaf = _read_leaf(src_neutral, rec.path.segments, rec.target_key, rec.dict_key)
                _write_leaf(composed, rec.path.segments, rec.target_key, live_leaf, rec.dict_key)
                for overlay in per_target_overlay.values():
                    _write_leaf(
                        overlay,
                        rec.path.segments,
                        rec.target_key,
                        live_leaf,
                        rec.dict_key,
                    )
            # winning_source is NEUTRAL (or both agree) → composed already
            # carries the value via the n1 deep-copy; nothing to do.

        # 6. Resolve conflicts via the configured resolver. The resolver
        # returns a typed ``ResolverOutcome`` carrying the decision kind,
        # resolved value, and a ``persist`` flag. Outcomes with
        # ``persist=True`` (interactive resolutions) get written back
        # into ``composed.resolutions`` so the next merge can replay
        # them silently. Non-interactive strategies set ``persist=False``
        # (resolution-memory spec §3) so batch one-shot runs don't
        # mutate persisted state.
        for c in conflicts:
            outcome = self._resolver.resolve(c)
            rec_path_str = render_change_path(c.record)
            if outcome.decision is ResolutionDecisionKind.TARGET_SPECIFIC:
                _apply_target_specific(c.record, composed, per_target_overlay, per_target_neutral)
                ctx.warn(
                    LossWarning(
                        domain=c.record.domain,
                        target=next(iter(c.record.per_target.keys())),
                        message=(
                            f"{c.record.render_path()}: target-specific by operator "
                            f"decision; not propagating cross-target"
                        ),
                        field_path=c.record.path,
                    )
                )
            elif outcome.decision is ResolutionDecisionKind.SKIP:
                # Skip leaves composed unchanged at this path — fall
                # through to the persistence step below so the operator's
                # explicit "skip" can still be remembered (won't auto-
                # apply, but the prior_decision context is preserved).
                pass
            else:
                _apply_resolution_value(outcome.value, c.record, composed, per_target_overlay)
            if outcome.persist:
                new_resolutions[rec_path_str] = Resolution(
                    decided_at=datetime.now(tz=UTC),
                    decision=outcome.decision,
                    decision_target=outcome.decision_target,
                    decision_hash=compute_decision_hash(c.record),
                )

        # Write the (possibly-augmented) resolutions back into composed.
        # Only persisted entries appear; non-interactive strategies leave
        # the dict untouched.
        composed.resolutions = Resolutions(items=new_resolutions)

        # 6b. Compose pass-through bags per target.
        #
        # `composed.targets[tid].items` already carries n1's authored bag
        # (via deep-copy of n1 above). Layer the live-disassembled bag on
        # top — the operator edited live most recently, so its values win
        # for keys that exist in both. Keys present only in n1 survive.
        # This is the classify=ADOPT_TARGET equivalent at the bag level;
        # per-key classification with explicit conflict reporting is a
        # P2-1 follow-on.
        for tid, live_bag in per_target_passthrough.items():
            existing_bag = composed.targets.get(tid, PassThroughBag())
            merged_items: dict[str, object] = dict(existing_bag.items)
            for k, v in live_bag.items():
                merged_items[k] = v
            # PassThroughBag.items is `dict[str, JsonValue]`; rely on
            # Pydantic to validate-and-coerce the merged dict (e.g. nested
            # tomlkit Table values normalize to plain dict at this hop).
            # Preserve any ``target_specific`` entries already on the bag —
            # the resolution-memory plumb (Wave-15 §2.2) writes per-target
            # values into ``existing_bag.target_specific`` *before* this
            # composition step; rebuilding the bag from ``items`` only
            # would silently drop them.
            composed.targets[tid] = PassThroughBag.model_validate(
                {
                    "items": merged_items,
                    "target_specific": dict(existing_bag.target_specific),
                }
            )

        # 7. Re-derive each target from `composed`
        ctx2 = TranspileCtx(profile_name=request.profile_name)
        target_outputs: dict[TargetId, dict[str, bytes]] = {}
        for tid in self.targets.target_ids():
            target_cls = self.targets.get(tid)
            if target_cls is None:
                continue
            # Re-derive from this target's per-target overlay rather than
            # the bare ``composed``. The overlay is identical to composed
            # for every authored / consensual leaf, but carries this
            # target's preserved values for unauthored cross-target
            # conflicts (issue #44 — see classification block above).
            target_view = per_target_overlay.get(tid, composed)
            per_domain_sections: dict[Domains, BaseModel] = {}
            for codec_cls in target_cls.codecs:
                neutral_field = getattr(target_view, codec_cls.domain.value)
                try:
                    section = codec_cls.to_target(neutral_field, ctx2)
                except NotImplementedError:
                    continue
                per_domain_sections[codec_cls.domain] = section

            # Always read existing live so partial-ownership assemblers
            # (e.g. ~/.claude.json) layer on the same baseline whether or
            # not this is a dry-run — otherwise the dry-run diff would
            # diverge from what a real merge actually writes.
            existing = self._read_live_files(target_cls)
            target_bag = composed.targets.get(tid, PassThroughBag())
            files = target_cls.assembler.assemble(
                per_domain=per_domain_sections,
                passthrough=dict(target_bag.items),
                existing=existing,
            )
            target_outputs[tid] = dict(files)

        # Aggregate warnings: disassemble (ctx) + re-derive (ctx2). Both
        # surface on MergeResult so the CLI can show them; dedupe on the
        # tuple of (target, domain, message) to keep the operator-facing
        # output noise-free if a single bad section gets re-validated on
        # a follow-up pass.
        all_warnings: list[LossWarning] = []
        seen: set[tuple[TargetId, Domains, str]] = set()
        for w in ctx.warnings + ctx2.warnings:
            key = (w.target, w.domain, w.message)
            if key in seen:
                continue
            seen.add(key)
            all_warnings.append(w)

        # 8. Write live + commit state-repos.
        #
        # On dry_run we run the same compose + per-spec planning the real
        # write loop does, but instead of touching disk we collect a
        # FileDiff per FileSpec and return them on MergeResult. The CLI
        # turns those into a unified diff identical to what `chameleon
        # diff` would print after a real merge. Crucially this skips both
        # the live writes AND the per-target state-repo git commit; LKG
        # and neutral file writes are also skipped.
        if request.dry_run:
            diffs: list[FileDiff] = []
            for tid, files in target_outputs.items():
                target_cls = self.targets.get(tid)
                if target_cls is None:
                    continue
                for spec in target_cls.assembler.files:
                    live_path = Path(os.path.expanduser(spec.live_path))
                    before = live_path.read_bytes() if live_path.exists() else b""
                    after = files.get(spec.repo_path, b"")
                    diffs.append(
                        FileDiff(
                            target=tid,
                            live_path=live_path,
                            repo_path=spec.repo_path,
                            before=before,
                            after=after,
                        )
                    )
            changed_count = sum(1 for d in diffs if d.changed)
            summary = (
                f"dry run: {changed_count} file(s) would change"
                if changed_count
                else "dry run: nothing to do"
            )
            return MergeResult(
                exit_code=0,
                summary=summary,
                merge_id=merge_id,
                warnings=all_warnings,
                diffs=diffs,
            )

        # 7a. GC stale resolutions (Wave-15 §1).
        #
        # Walk ``composed.resolutions.items`` and prune entries whose
        # disagreement has resolved itself (the unified neutral leaf
        # equals every per-target leaf). Per spec §1 GC: runs only on
        # successful merges; failed merges (the dry-run early-return
        # above) leave entries intact so the operator can retry.
        _gc_resolutions(composed, per_target_neutral)

        # 7b. Recovery marker (§4.6).
        #
        # Before mutating any live target file, persist a `MergeTransaction`
        # describing what we are about to do. The marker captures:
        #
        #   - the LKG hash we INTEND to land (the YAML hash of `composed`),
        #     so a recovery path can compare current LKG against intent and
        #     decide "did the LKG update step run?";
        #   - the pre-merge SHAs of every PARTIAL-ownership live file, so a
        #     recovery path can decide "did the partial-owned write actually
        #     land what we intended?" by re-reading and re-hashing.
        #
        # On dry-run we do NOT touch tx_store at all (the early-return above
        # already short-circuits). On any exception during the live-write or
        # state-repo loop, the marker remains on disk and `chameleon doctor`
        # surfaces it. On success the marker — and any pre-existing stale
        # markers from previous interrupted runs — is cleared, because a
        # clean merge means those interruptions have been resolved.
        composed_dict = composed.model_dump(mode="json", exclude_none=False)
        composed_yaml = dump_yaml(composed_dict)
        target_ids_in_play: list[TargetId] = [
            tid for tid in target_outputs if self.targets.get(tid) is not None
        ]
        partial_owned_hashes: dict[str, str] = {}
        for tid in target_ids_in_play:
            target_cls = self.targets.get(tid)
            if target_cls is None:
                continue
            for spec in target_cls.assembler.files:
                if spec.ownership is not FileOwnership.PARTIAL:
                    continue
                live = Path(os.path.expanduser(spec.live_path))
                pre_bytes = live.read_bytes() if live.exists() else b""
                partial_owned_hashes[spec.live_path] = hashlib.sha256(pre_bytes).hexdigest()

        marker = MergeTransaction(
            merge_id=merge_id,
            started_at=datetime.now(tz=UTC),
            target_ids=target_ids_in_play,
            neutral_lkg_hash_after=hashlib.sha256(composed_yaml.encode("utf-8")).hexdigest(),
            partial_owned_hashes=partial_owned_hashes,
        )
        self.tx_store.write(marker)

        any_changed = False
        for tid, files in target_outputs.items():
            target_cls = self.targets.get(tid)
            if target_cls is None:
                continue
            repo = self._ensure_state_repo(tid)

            for spec in target_cls.assembler.files:
                live_path = Path(os.path.expanduser(spec.live_path))
                live_path.parent.mkdir(parents=True, exist_ok=True)
                content = files.get(spec.repo_path, b"")
                repo_file = repo.path / spec.repo_path
                repo_file.parent.mkdir(parents=True, exist_ok=True)
                # The recovery marker we wrote above promises we will write
                # this file. Honor that promise unconditionally so doctor's
                # post-crash inspection (compare on-disk SHA against the
                # marker's `partial_owned_hashes`) is meaningful — skipping
                # an unchanged write would leave the marker indistinguishable
                # from a partial-write failure mode.
                if not live_path.exists() or live_path.read_bytes() != content:
                    any_changed = True
                live_path.write_bytes(content)
                repo_file.write_bytes(content)

            repo.add_all()
            if not repo.is_clean() or repo.head_commit() is None:
                repo.commit(
                    f"merge: {len(conflicts)} conflict(s), {len(target_outputs)} target(s)",
                    trailer={"Merge-Id": merge_id},
                )
                any_changed = True

        # 9. Update LKG and neutral file
        if not self.paths.lkg.exists() or self.paths.lkg.read_text() != composed_yaml:
            any_changed = True
            self.paths.lkg.parent.mkdir(parents=True, exist_ok=True)
            self.paths.lkg.write_text(composed_yaml, encoding="utf-8")
        if not self.paths.neutral.exists() or self.paths.neutral.read_text() != composed_yaml:
            any_changed = True
            self.paths.neutral.parent.mkdir(parents=True, exist_ok=True)
            self.paths.neutral.write_text(composed_yaml, encoding="utf-8")

        # Recovery marker cleanup (§4.6): a clean merge means any prior
        # interruption has been resolved by the new consistent state we just
        # landed. Clear our own marker AND every pre-existing stale marker.
        for stale in self.tx_store.entries():
            self.tx_store.clear(stale.merge_id)

        if not any_changed:
            return MergeResult(
                exit_code=0,
                summary="merge: nothing to do",
                merge_id=merge_id,
                warnings=all_warnings,
            )

        return MergeResult(
            exit_code=0,
            summary=f"merge: applied across {len(target_outputs)} target(s)",
            merge_id=merge_id,
            warnings=all_warnings,
        )


__all__ = ["MergeEngine", "MergeRequest", "MergeResult"]
