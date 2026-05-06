"""Four-source change model (§4.3) with typed classification.

Per-FieldPath classification (P2-1)
-----------------------------------

The merge engine doesn't classify whole domains as opaque blobs — it
walks the neutral schema field-by-field via :func:`walk_changes` and
emits one :class:`ChangeRecord` per leaf. The walker special-cases
``dict[TargetId, V]``-shaped fields (the ``Mapping[TargetId, V]``
pattern from §7.1: ``identity.model``, ``identity.endpoint.base_url``,
…). For those, each ``TargetId`` key becomes its own record with
``target_key`` set, and only that target's reverse-codec evidence
contributes to the per-target side: other targets' codecs cannot
speak for a key they don't own, so they're omitted from
``per_target`` rather than treated as drift.

This eliminates the false-conflict where on a re-merge each target's
reverse codec produces only its own entry (``{claude: ...}`` from
Claude side, ``{codex: ...}`` from Codex side), each of which differs
from the composed neutral's multi-target dict and from each other.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Mapping
from enum import Enum
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ConfigDict

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

    def render_path(self) -> str:
        """Human-readable path including the ``target_key`` discriminator.

        For ordinary leaves this is equivalent to ``path.render()``. For
        ``dict[TargetId, V]`` leaves the owning key is appended:
        ``identity.model[claude]``.
        """
        base = self.path.render()
        if self.target_key is None:
            return base
        return f"{base}[{self.target_key.value}]"


class ChangeClassification(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    outcome: ChangeOutcome
    resolved_value: Any = None
    winning_source: ChangeSource | None = None
    winning_target: TargetId | None = None


def classify_change(record: ChangeRecord) -> ChangeClassification:
    """Apply §5.3's classification table."""
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


def _walk_node(
    *,
    domain: Domains,
    path_segments: tuple[str, ...],
    annotation: Any,
    n0_val: Any,
    n1_val: Any,
    per_target_vals: dict[TargetId, Any],
    out: list[ChangeRecord],
) -> None:
    """Recursively emit one ``ChangeRecord`` per leaf under this node."""
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

        for key_tid in keys:
            n0_key = n0_val.get(key_tid) if isinstance(n0_val, dict) else None
            n1_key = n1_val.get(key_tid) if isinstance(n1_val, dict) else None
            owner_val = per_target_vals.get(key_tid)
            owner_key_val: Any
            if isinstance(owner_val, dict) and key_tid in owner_val:
                owner_key_val = owner_val[key_tid]
                key_per_target = {key_tid: _serialize(owner_key_val)}
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
                )
            )
        return

    # Case 2: nested BaseModel — recurse into its fields.
    if _is_basemodel_class(annotation):
        inner_cls = _strip_optional(annotation)
        # If everything's None we have nothing to walk — defaults handle it.
        for sub_name, sub_field in inner_cls.model_fields.items():
            sub_anno = sub_field.annotation
            sub_n0 = getattr(n0_val, sub_name, None) if n0_val is not None else None
            sub_n1 = getattr(n1_val, sub_name, None) if n1_val is not None else None
            sub_per_target: dict[TargetId, Any] = {}
            for tid, tv in per_target_vals.items():
                sub_per_target[tid] = getattr(tv, sub_name, None) if tv is not None else None
            _walk_node(
                domain=domain,
                path_segments=(*path_segments, sub_name),
                annotation=sub_anno,
                n0_val=sub_n0,
                n1_val=sub_n1,
                per_target_vals=sub_per_target,
                out=out,
            )
        return

    # Case 3: scalar / list / dict[str, V] / Enum / etc. — single leaf.
    out.append(
        ChangeRecord(
            domain=domain,
            path=FieldPath(segments=path_segments),
            n0=_serialize(n0_val),
            n1=_serialize(n1_val),
            per_target={tid: _serialize(tv) for tid, tv in per_target_vals.items()},
        )
    )


def walk_changes(
    n0: BaseModel,
    n1: BaseModel,
    per_target_neutrals: Mapping[TargetId, BaseModel],
) -> list[ChangeRecord]:
    """Walk every neutral domain field-by-field, emitting per-leaf records.

    Iterates the eight domains declared on ``Neutral`` and recursively
    walks each domain's nested Pydantic structure. ``dict[TargetId, V]``
    fields produce one record per TargetId key (see module docstring);
    everything else produces one record per leaf path.

    Pydantic-only: we use ``model_fields`` and recursive descent, never
    string introspection of attribute names.
    """
    out: list[ChangeRecord] = []
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
        _walk_node(
            domain=domain,
            path_segments=(field_name,),
            annotation=anno,
            n0_val=sub_n0,
            n1_val=sub_n1,
            per_target_vals=sub_per_target,
            out=out,
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
