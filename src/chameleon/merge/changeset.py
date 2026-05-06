"""Four-source change model (§4.3) with typed classification."""

from __future__ import annotations

from enum import Enum
from typing import Any

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
    """The four sources for a single neutral key.

    `n0` is the last-known-good value; `n1` is the current neutral; the
    `per_target` mapping has each target's value derived from its live
    files. `Any` here is genuinely arbitrary — values are scalars, lists,
    or nested dicts depending on the schema field's shape.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    domain: Domains
    path: FieldPath
    n0: Any
    n1: Any
    per_target: dict[TargetId, Any]


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


__all__ = [
    "ChangeClassification",
    "ChangeOutcome",
    "ChangeRecord",
    "ChangeSource",
    "classify_change",
]
