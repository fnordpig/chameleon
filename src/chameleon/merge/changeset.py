"""Four-source change model with typed classification.

Per-FieldPath classification (P2-1)
-----------------------------------

The merge engine doesn't classify whole domains as opaque blobs — it
walks the neutral schema field-by-field via :func:`walk_changes` and
emits one :class:`ChangeRecord` per leaf. The walker special-cases
``dict[TargetId, V]``-shaped fields (the ``Mapping[TargetId, V]``
pattern from: ``identity.model``, ``identity.endpoint.base_url``,
…). For those, each ``TargetId`` key becomes its own record with
``target_key`` set, and only that target's reverse-codec evidence
contributes to the per-target side: other targets' codecs cannot
speak for a key they don't own, so they're omitted from
``per_target`` rather than treated as drift.

This eliminates the false-conflict where on a re-merge each target's
reverse codec produces only its own entry (``{claude: ...}`` from
Claude side, ``{codex: ...}`` from Codex side), each of which differs
from the composed neutral's multi-target dict and from each other.

Per-key decomposition for ``dict[str, V]`` (issue #44)
------------------------------------------------------

The same "silence ≠ drift" principle applies to ordinary
``dict[str, V]`` fields like ``capabilities.plugins`` (keyed by
``<plugin>@<marketplace>``) and ``capabilities.plugin_marketplaces``.
Two targets enumerating disjoint key sets — e.g. Claude knows
``superpowers@claude-plugins-official`` while Codex knows
``ripvec@example-user-plugins`` — are not in conflict; they're
talking about different keys. Treating the whole dict as one leaf
mis-classified this as CONFLICT, which under ``--on-conflict=keep``
silently dropped *all* claimed-but-unauthored data on re-derive.

The walker therefore decomposes ``dict[str, V]`` fields the same way
it decomposes ``dict[TargetId, V]``: one ``ChangeRecord`` per dict
key (``dict_key``), with ``per_target`` containing only the targets
that actually enumerate that key. A target's silence on a key
contributes nothing — exactly as for ``dict[TargetId, V]``.

Operator omission ≠ explicit deletion (issue #44)
-------------------------------------------------

Pydantic's ``model_validate`` can't tell whether a field was
explicitly written as ``{}`` / ``[]`` / ``null`` or simply omitted
from the YAML — both produce the same defaulted value. But the
classifier MUST tell them apart: omission means "operator has no
opinion, preserve N₀"; an explicit empty means "operator deleted
the previous value." Treating omission as deletion was the second
half of issue #44 — after ``init`` absorbed live data into N₀, an
operator authoring partial neutral.yaml would see every defaulted
field re-classified as a NEUTRAL-source clear and dropped on
re-derive.

The walker uses two complementary signals to recover the
distinction:

1. ``n1_authored`` — the raw parsed YAML dict (when supplied by the
   engine). At each descent step the walker narrows it by key; a
   sub-path absent from the raw dict marks the subtree unauthored.
2. ``n1_defaults`` — the schema default tree (always available, computed
   from ``Neutral(schema_version=1)``). A leaf whose ``n1`` value
   matches the default at that path is also treated as unauthored.

This handles three cases uniformly: operator omission (raw YAML
absence), the post-``chameleon init`` starter neutral (raw YAML
present but every leaf equal to its schema default), and a hand-
written partial YAML where only the changed keys appear. The
defaults-equality test makes the rule robust against the starter-
neutral shape without depending on cli internals.

For a leaf to count as an explicit assignment by the operator the
value must (a) appear in the raw YAML's path AND (b) differ from
the schema default at that path. To delete a previously-tracked
entry the operator authors its key explicitly with a new value
(``null``, removed, replaced with a placeholder, etc.), or runs
``chameleon discard``.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Mapping
from enum import Enum
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.schema._constants import Domains


class ChangeSource(Enum):
    NEUTRAL = "neutral"
    TARGET = "target"


class ChangeOutcome(Enum):
    UNCHANGED = "unchanged"
    CONSENSUAL = "consensual"
    CONFLICT = "conflict"


class ChangeRecord(BaseModel):
    """The four sources for a single neutral leaf.

    ``n0`` is the last-known-good value; ``n1`` is the current neutral;
    ``per_target`` has each contributing target's value derived from its
    live files. ``target_key`` is set when this record represents a
    single key inside a ``dict[TargetId, V]`` field (``identity.model``
    etc.); the path then renders as e.g. ``identity.model.claude`` and
    ``per_target`` carries only the owning target's evidence.

    ``Any`` here is genuinely arbitrary — values are scalars, lists, or
    nested dicts depending on the schema field's shape.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    domain: Domains
    path: FieldPath
    n0: Any
    n1: Any
    per_target: dict[TargetId, Any]
    target_key: TargetId | None = None
    dict_key: str | None = None
    neutral_mtime_ns: int | None = None
    per_target_mtime_ns: dict[TargetId, int] = Field(default_factory=dict)
    authored: bool = True
    """Whether this leaf path was explicitly authored in N₁'s raw YAML.

    Defaults to ``True`` for the in-process construction path (no raw
    YAML supplied; see ``walk_changes``). When ``False`` the walker has
    substituted ``n0`` for ``n1`` per the operator-omission rule (issue
    #44 — see module docstring), and the engine knows that an UNCHANGED
    classification still requires writing ``n0``'s value into
    ``composed`` (because ``composed`` is seeded from ``n1`` which has
    only the operator's authored values)."""

    def render_path(self) -> str:
        """Human-readable path including the per-key discriminator.

        For ordinary leaves this is equivalent to ``path.render()``. For
        ``dict[TargetId, V]`` leaves the owning key is appended:
        ``identity.model[claude]``. For ``dict[str, V]`` leaves
        (issue #44 — see module docstring) the key is appended verbatim:
        ``capabilities.plugins[ripvec@example-user-plugins]``.
        """
        base = self.path.render()
        if self.target_key is not None:
            return f"{base}[{self.target_key.value}]"
        if self.dict_key is not None:
            return f"{base}[{self.dict_key}]"
        return base


class ChangeClassification(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    outcome: ChangeOutcome
    resolved_value: Any = None
    winning_source: ChangeSource | None = None
    winning_target: TargetId | None = None


def classify_change(record: ChangeRecord) -> ChangeClassification:
    """Apply's classification table."""
    n0 = record.n0
    n1 = record.n1

    sources_with_change: list[tuple[ChangeSource, TargetId | None, Any]] = []
    if n1 != n0:
        sources_with_change.append((ChangeSource.NEUTRAL, None, n1))
    for tid, val in record.per_target.items():
        if val != n0:
            sources_with_change.append((ChangeSource.TARGET, tid, val))

    if not sources_with_change:
        return ChangeClassification(outcome=ChangeOutcome.UNCHANGED)

    distinct_values = {repr(v) for _, _, v in sources_with_change}
    if len(distinct_values) == 1:
        src, tid, val = sources_with_change[0]
        return ChangeClassification(
            outcome=ChangeOutcome.CONSENSUAL,
            resolved_value=val,
            winning_source=src,
            winning_target=tid,
        )

    return ChangeClassification(outcome=ChangeOutcome.CONFLICT)


# ---------------------------------------------------------------------
# walk_changes — per-FieldPath traversal of the neutral schema
# ---------------------------------------------------------------------


def _strip_optional(annotation: Any) -> Any:
    """Reduce ``T | None`` (or ``Optional[T]``) to ``T``; pass others through."""
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        non_none = tuple(a for a in get_args(annotation) if a is not type(None))
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _is_dict_targetid(annotation: Any) -> bool:
    """True iff `annotation` is ``dict[TargetId, V]`` (possibly inside ``| None``)."""
    inner = _strip_optional(annotation)
    if get_origin(inner) is not dict:
        return False
    args = get_args(inner)
    if len(args) != 2:
        return False
    return args[0] is TargetId


def _is_dict_str(annotation: Any) -> bool:
    """True iff `annotation` is ``dict[str, V]`` (possibly inside ``| None``).

    Used by the walker to decompose claimed dict-shaped fields like
    ``capabilities.plugins`` into one record per key (issue #44 — see
    module docstring). Distinct from :func:`_is_dict_targetid`, which
    treats the targets-as-keys case where each TargetId owns its own key.
    """
    inner = _strip_optional(annotation)
    if get_origin(inner) is not dict:
        return False
    args = get_args(inner)
    if len(args) != 2:
        return False
    return args[0] is str


def _is_basemodel_class(annotation: Any) -> bool:
    """True iff the (non-Optional) annotation is a ``BaseModel`` subclass."""
    inner = _strip_optional(annotation)
    return isinstance(inner, type) and issubclass(inner, BaseModel)


def _serialize(value: Any) -> Any:
    """Normalize a value to its JSON-mode representation for comparison.

    Mirrors the pre-walker engine's ``model_dump(mode="json")`` so
    classification is value-equality on serialized scalars/lists/dicts —
    not on Pydantic model identity, and not on Enum vs str mismatches.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            (k.value if isinstance(k, TargetId) else k): _serialize(v) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_serialize(v) for v in value]
    return value


class _AuthoredAll:
    """Sentinel: this subtree should be treated as fully authored.

    Used when ``walk_changes`` is called without an explicit raw-YAML
    view (the in-process API path: tests, and any future programmatic
    caller). The walker's authored-aware code paths short-circuit on
    ``isinstance(_, _AuthoredAll)`` so every key counts as explicitly
    authored at every level — preserving pre-issue-#44 behaviour for
    callers that legitimately know N₁ down to its defaults.
    """


_AUTHORED_ALL: Any = _AuthoredAll()


def _authored_child(authored: Any, key: str) -> Any:
    """Narrow an authored view by key, propagating ``_AUTHORED_ALL``.

    Returns the child mapping/value if the key exists in a real raw
    dict; the sentinel if ``authored`` is the perpetually-authored
    sentinel; ``None`` if the key is absent (subtree unauthored).
    """
    if isinstance(authored, _AuthoredAll):
        return _AUTHORED_ALL
    if isinstance(authored, dict) and key in authored:
        return authored[key]
    return None


def _is_authored_container(authored: Any) -> bool:
    """True iff ``authored`` is something the walker can descend into.

    A real raw-YAML dict, or the perpetually-authored sentinel. Anything
    else (``None``, a scalar, a list) means the walker can't descend
    further — children of this node are unauthored.
    """
    if isinstance(authored, _AuthoredAll):
        return True
    return isinstance(authored, dict)


def _authored_has_key(authored: Any, key: str) -> bool:
    """True iff ``key`` is explicitly authored under ``authored``.

    The sentinel reports every key as authored; a real dict reports
    only the keys it actually contains.
    """
    if isinstance(authored, _AuthoredAll):
        return True
    return isinstance(authored, dict) and key in authored


def _walk_node(  # noqa: PLR0912, PLR0915 — single recursive switch over schema cases
    *,
    domain: Domains,
    path_segments: tuple[str, ...],
    annotation: Any,
    n0_val: Any,
    n1_val: Any,
    per_target_vals: dict[TargetId, Any],
    out: list[ChangeRecord],
    n1_authored: Any,
    n1_default: Any,
    per_target_claimed_paths: Mapping[TargetId, frozenset[FieldPath]],
) -> None:
    """Recursively emit one ``ChangeRecord`` per leaf under this node.

    ``n1_authored`` is the raw-YAML view of N₁ at this path (or
    ``None``/non-mapping if the operator didn't author this branch);
    ``n1_default`` is the schema-default value at the same path. Both
    feed the "operator omission ≠ explicit deletion" rule — see the
    module docstring's "Operator omission" section.
    """
    # Case 1: dict[TargetId, V] — split into one record per TargetId key.
    if _is_dict_targetid(annotation):
        keys: set[TargetId] = set()
        for src in (n0_val, n1_val):
            if isinstance(src, dict):
                keys.update(k for k in src if isinstance(k, TargetId))
        # Each target's reverse codec only ever populates *its own* key —
        # so we look up `tid` in `per_target_vals[tid]`'s dict, not in
        # other targets' dicts. That's the whole point of the special-case.
        for tid, tv in per_target_vals.items():
            if isinstance(tv, dict) and tid in tv:
                keys.add(tid)

        # The schema default for a `dict[TargetId, V]` field is `None`
        # or `{}`; in either case the per-key default is `None`.
        field_path = FieldPath(segments=path_segments)
        for key_tid in keys:
            n0_key = n0_val.get(key_tid) if isinstance(n0_val, dict) else None
            n1_key = n1_val.get(key_tid) if isinstance(n1_val, dict) else None
            # Operator-omission rule: a key is authored only if the raw
            # YAML carries it AND the parsed value differs from the
            # schema default. Otherwise treat n1 as n0 so the classifier
            # doesn't see a NEUTRAL-source clear.
            raw_present = _authored_has_key(n1_authored, key_tid.value)
            differs_from_default = n1_key is not None  # default for absent keyed entry
            authored_here = raw_present and differs_from_default
            if not authored_here:
                n1_key = n0_key
            owner_val = per_target_vals.get(key_tid)
            owner_key_val: Any
            if isinstance(owner_val, dict) and key_tid in owner_val:
                owner_key_val = owner_val[key_tid]
                key_per_target = {key_tid: _serialize(owner_key_val)}
            elif field_path in per_target_claimed_paths.get(key_tid, frozenset()):
                key_per_target = {key_tid: None}
            else:
                # The owning target had no live evidence for this key —
                # emit no per-target evidence (silence ≠ drift).
                key_per_target = {}

            out.append(
                ChangeRecord(
                    domain=domain,
                    path=FieldPath(segments=path_segments),
                    n0=_serialize(n0_key),
                    n1=_serialize(n1_key),
                    per_target=key_per_target,
                    target_key=key_tid,
                    authored=authored_here,
                )
            )
        return

    # Case 2: dict[str, V] — split into one record per dict key.
    #
    # Each key in such a dict is independently owned: two targets that
    # enumerate disjoint key sets (Claude knows
    # ``superpowers@claude-plugins-official``; Codex knows
    # ``ripvec@example-user-plugins``) are NOT in conflict — they're
    # speaking about different keys. A target's silence on a key
    # contributes nothing to ``per_target`` (silence ≠ drift), exactly
    # mirroring the ``dict[TargetId, V]`` case above. This is the
    # principled fix for issue #44: the prior whole-dict leaf
    # mis-classified disjoint-key dicts as CONFLICT and dropped every
    # claimed-but-unauthored entry under ``--on-conflict=keep``.
    if _is_dict_str(annotation):
        keys: set[str] = set()
        for src in (n0_val, n1_val):
            if isinstance(src, dict):
                keys.update(k for k in src if isinstance(k, str))
        for tv in per_target_vals.values():
            if isinstance(tv, dict):
                keys.update(k for k in tv if isinstance(k, str))

        for str_key in keys:
            n0_key = n0_val.get(str_key) if isinstance(n0_val, dict) else None
            n1_key = n1_val.get(str_key) if isinstance(n1_val, dict) else None
            # Operator-omission rule: a key is authored only if the raw
            # YAML carries it AND the parsed value isn't ``None`` (no
            # default semantic exists for "absent str-keyed entry").
            # Otherwise treat n1 as n0 so the classifier preserves
            # whatever N₀ / per-target evidence had.
            raw_present = _authored_has_key(n1_authored, str_key)
            authored_here = raw_present and n1_key is not None
            if not authored_here:
                n1_key = n0_key
            key_per_target: dict[TargetId, Any] = {}
            for tid, tv in per_target_vals.items():
                if isinstance(tv, dict) and str_key in tv:
                    key_per_target[tid] = _serialize(tv[str_key])

            out.append(
                ChangeRecord(
                    domain=domain,
                    path=FieldPath(segments=path_segments),
                    n0=_serialize(n0_key),
                    n1=_serialize(n1_key),
                    per_target=key_per_target,
                    dict_key=str_key,
                    authored=authored_here,
                )
            )
        return

    # Case 3: nested BaseModel — recurse into its fields.
    if _is_basemodel_class(annotation):
        inner_cls = _strip_optional(annotation)
        # Narrow ``n1_authored`` and ``n1_default`` to this sub-field.
        # Both signals propagate down independently.
        for sub_name, sub_field in inner_cls.model_fields.items():
            sub_anno = sub_field.annotation
            sub_n0 = getattr(n0_val, sub_name, None) if n0_val is not None else None
            sub_n1 = getattr(n1_val, sub_name, None) if n1_val is not None else None
            sub_per_target: dict[TargetId, Any] = {}
            for tid, tv in per_target_vals.items():
                sub_per_target[tid] = getattr(tv, sub_name, None) if tv is not None else None
            sub_authored = _authored_child(n1_authored, sub_name)
            sub_default = getattr(n1_default, sub_name, None) if n1_default is not None else None
            _walk_node(
                domain=domain,
                path_segments=(*path_segments, sub_name),
                annotation=sub_anno,
                n0_val=sub_n0,
                n1_val=sub_n1,
                per_target_vals=sub_per_target,
                out=out,
                n1_authored=sub_authored,
                n1_default=sub_default,
                per_target_claimed_paths=per_target_claimed_paths,
            )
        return

    # Case 4: scalar / list / Enum / unrecognized dict shape — single leaf.
    # Operator-omission rule at the leaf: a leaf counts as authored only
    # if the raw YAML carries it (n1_authored is not None) AND the parsed
    # value differs from the schema default at that path. The defaults-
    # equality test handles the post-``init`` starter neutral, where the
    # raw YAML is present but every leaf equals its default — without
    # this, init would never absorb anything past the first scalar.
    raw_present = n1_authored is not None
    default_serialized = _serialize(n1_default)
    differs_from_default = _serialize(n1_val) != default_serialized
    authored_here = raw_present and differs_from_default
    n1_for_record = n1_val if authored_here else n0_val
    # Per-target silence rule (issue #44): a target codec that doesn't
    # claim this leaf produces a default value in its from_target output
    # (it has no other choice — the neutral domain model is shared).
    # That default isn't "the codec asserts empty"; it's "the codec has
    # nothing to say." Exclude default-equal per-target evidence unless
    # the codec explicitly declares ownership of this neutral path. When
    # it does, the default is real target evidence: the target removed a
    # previously-authored scalar.
    field_path = FieldPath(segments=path_segments)
    pt_serialized: dict[TargetId, Any] = {}
    for tid, tv in per_target_vals.items():
        ser = _serialize(tv)
        if ser != default_serialized or field_path in per_target_claimed_paths.get(
            tid, frozenset()
        ):
            pt_serialized[tid] = ser
    out.append(
        ChangeRecord(
            domain=domain,
            path=FieldPath(segments=path_segments),
            n0=_serialize(n0_val),
            n1=_serialize(n1_for_record),
            per_target=pt_serialized,
            authored=authored_here,
        )
    )


def walk_changes(
    n0: BaseModel,
    n1: BaseModel,
    per_target_neutrals: Mapping[TargetId, BaseModel],
    *,
    n1_authored: dict[str, object] | None = None,
    per_target_claimed_paths: Mapping[TargetId, frozenset[FieldPath]] | None = None,
) -> list[ChangeRecord]:
    """Walk every neutral domain field-by-field, emitting per-leaf records.

    Iterates the eight domains declared on ``Neutral`` and recursively
    walks each domain's nested Pydantic structure. ``dict[TargetId, V]``
    and ``dict[str, V]`` fields produce one record per dict key (see
    module docstring); everything else produces one record per leaf path.

    ``n1_authored`` is the operator's raw-YAML view of N₁ (as returned
    by :func:`chameleon.io.yaml.load_yaml` before validation). When
    present, the walker uses it to honour the "operator omission ≠
    explicit deletion" rule (issue #44 — see module docstring). When
    ``None`` (e.g. neutral.yaml didn't exist on disk, or the caller
    constructed N₁ in-process), the walker treats every path as
    authored: in-process construction is the test/internal-API path
    where the caller already chose every value explicitly.

    Pydantic-only: we use ``model_fields`` and recursive descent, never
    string introspection of attribute names.
    """
    out: list[ChangeRecord] = []
    claimed_paths = per_target_claimed_paths or {}
    treat_all_authored = n1_authored is None
    # The schema default tree is the single source of truth for "what
    # would Pydantic produce from an empty input?" We compute it once
    # per call rather than per-leaf to keep the walker O(n) in field
    # count.
    default_root = n1.__class__(schema_version=1)  # type: ignore[call-arg]
    for domain in Domains:
        field_name = domain.value
        if field_name not in n1.__class__.model_fields:
            continue
        sub_n0 = getattr(n0, field_name, None)
        sub_n1 = getattr(n1, field_name, None)
        sub_per_target: dict[TargetId, Any] = {
            tid: getattr(neut, field_name, None) for tid, neut in per_target_neutrals.items()
        }
        anno = n1.__class__.model_fields[field_name].annotation
        sub_authored = (
            _AUTHORED_ALL if treat_all_authored else _authored_child(n1_authored, field_name)
        )
        sub_default = getattr(default_root, field_name, None)
        _walk_node(
            domain=domain,
            path_segments=(field_name,),
            annotation=anno,
            n0_val=sub_n0,
            n1_val=sub_n1,
            per_target_vals=sub_per_target,
            out=out,
            n1_authored=sub_authored,
            n1_default=sub_default,
            per_target_claimed_paths=claimed_paths,
        )
    return out


__all__ = [
    "ChangeClassification",
    "ChangeOutcome",
    "ChangeRecord",
    "ChangeSource",
    "classify_change",
    "walk_changes",
]
