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

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from chameleon._types import TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge.changeset import (
    ChangeOutcome,
    classify_change,
    walk_changes,
)
from chameleon.merge.conflict import Conflict
from chameleon.merge.resolve import NonInteractiveResolver, Resolver, Strategy
from chameleon.schema._constants import Domains
from chameleon.schema.neutral import Neutral
from chameleon.schema.passthrough import PassThroughBag
from chameleon.state.git import GitRepo
from chameleon.state.paths import StatePaths
from chameleon.state.transaction import (
    TransactionStore,
    transaction_id,
)
from chameleon.targets._protocol import Target
from chameleon.targets._registry import TargetRegistry


def _resolve_parent(root: BaseModel, segments: tuple[str, ...]) -> BaseModel:
    """Walk to the parent node of the leaf identified by ``segments``.

    Each intermediate segment must name a Pydantic field whose value is
    itself a ``BaseModel``; raises ``AttributeError`` otherwise. The
    leaf segment is *not* descended into — caller does the final get/set.
    """
    node: BaseModel = root
    for seg in segments[:-1]:
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
    one of them per record.
    """
    parent = _resolve_parent(root, segments)
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
    """
    parent = _resolve_parent(root, segments)
    leaf_name = segments[-1]
    if target_key is None and dict_key is None:
        setattr(parent, leaf_name, value)
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


class MergeRequest(BaseModel):
    """Inputs to a merge run beyond what the engine already knows."""

    model_config = ConfigDict(frozen=True)

    profile_name: str | None = None
    dry_run: bool = False


class MergeResult(BaseModel):
    """Outcome of a merge run."""

    model_config = ConfigDict(frozen=True)

    exit_code: int
    summary: str
    merge_id: str | None = None


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
            domains, passthrough = target_cls.assembler.disassemble(live)
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
                conflicts.append(Conflict(record=rec))
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
        # returns the chosen leaf value (or None to skip); we apply it
        # at the same path/target_key. Pydantic models in resolved
        # values pass through `_write_leaf` as-is.
        for c in conflicts:
            resolved = self._resolver.resolve(c)
            if resolved is None:
                continue
            _write_leaf(
                composed,
                c.record.path.segments,
                c.record.target_key,
                resolved,
                c.record.dict_key,
            )
            # An explicit resolution overrides per-target preservation —
            # the operator (or strategy) made a choice, propagate it
            # everywhere.
            for overlay in per_target_overlay.values():
                _write_leaf(
                    overlay,
                    c.record.path.segments,
                    c.record.target_key,
                    resolved,
                    c.record.dict_key,
                )

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
            composed.targets[tid] = PassThroughBag.model_validate({"items": merged_items})

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

            existing = self._read_live_files(target_cls) if request.dry_run is False else None
            target_bag = composed.targets.get(tid, PassThroughBag())
            files = target_cls.assembler.assemble(
                per_domain=per_domain_sections,
                passthrough=dict(target_bag.items),
                existing=existing,
            )
            target_outputs[tid] = dict(files)

        # 8. Write live + commit state-repos (skipped on dry_run)
        if request.dry_run:
            return MergeResult(exit_code=0, summary="dry run — no files written", merge_id=merge_id)

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
        composed_dict = composed.model_dump(mode="json", exclude_none=False)
        composed_yaml = dump_yaml(composed_dict)
        if not self.paths.lkg.exists() or self.paths.lkg.read_text() != composed_yaml:
            any_changed = True
            self.paths.lkg.parent.mkdir(parents=True, exist_ok=True)
            self.paths.lkg.write_text(composed_yaml, encoding="utf-8")
        if not self.paths.neutral.exists() or self.paths.neutral.read_text() != composed_yaml:
            any_changed = True
            self.paths.neutral.parent.mkdir(parents=True, exist_ok=True)
            self.paths.neutral.write_text(composed_yaml, encoding="utf-8")

        if not any_changed:
            return MergeResult(exit_code=0, summary="merge: nothing to do", merge_id=merge_id)

        return MergeResult(
            exit_code=0,
            summary=f"merge: applied across {len(target_outputs)} target(s)",
            merge_id=merge_id,
        )


__all__ = ["MergeEngine", "MergeRequest", "MergeResult"]
