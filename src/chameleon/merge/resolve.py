"""Conflict resolution: interactive (TTY) and non-interactive (CLI flag)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from chameleon._types import TargetId
from chameleon.merge.conflict import Conflict
from chameleon.schema._constants import OnConflict


class Strategy(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: OnConflict
    target: TargetId | None = None


def on_conflict_to_strategy(raw: str) -> Strategy:
    """Parse the CLI's --on-conflict argument into a typed Strategy."""
    if raw.startswith("prefer="):
        target_name = raw.removeprefix("prefer=")
        if target_name == "neutral":
            return Strategy(kind=OnConflict.PREFER_NEUTRAL)
        if target_name == "lkg":
            return Strategy(kind=OnConflict.PREFER_LKG)
        return Strategy(kind=OnConflict.PREFER_TARGET, target=TargetId(value=target_name))

    mapping = {
        "fail": OnConflict.FAIL,
        "keep": OnConflict.KEEP,
        "prefer-neutral": OnConflict.PREFER_NEUTRAL,
        "prefer-lkg": OnConflict.PREFER_LKG,
    }
    return Strategy(kind=mapping[raw])


class NonInteractiveResolver:
    """Resolve conflicts according to a CLI-supplied Strategy."""

    def __init__(self, strategy: Strategy) -> None:
        self.strategy = strategy

    def resolve(self, conflict: Conflict) -> Any:
        """Return the chosen value, None to skip, or raise on FAIL."""
        record = conflict.record
        kind = self.strategy.kind
        if kind is OnConflict.FAIL:
            msg = (
                f"conflict on {record.domain.value}.{record.path.render()}: "
                f"neutral={record.n1!r} per_target={record.per_target!r}"
            )
            raise RuntimeError(msg)
        if kind is OnConflict.KEEP:
            return None
        if kind is OnConflict.PREFER_NEUTRAL:
            return record.n1
        if kind is OnConflict.PREFER_LKG:
            return record.n0
        if kind is OnConflict.PREFER_TARGET:
            assert self.strategy.target is not None
            return record.per_target.get(self.strategy.target)
        msg = f"unknown OnConflict {kind!r}"
        raise RuntimeError(msg)


__all__ = ["NonInteractiveResolver", "Strategy", "on_conflict_to_strategy"]
