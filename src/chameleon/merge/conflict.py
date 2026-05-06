"""Typed conflict record (a record we couldn't resolve consensually)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from chameleon.merge.changeset import ChangeRecord


class Conflict(BaseModel):
    """A neutral key whose four sources cannot be reconciled automatically."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    record: ChangeRecord


__all__ = ["Conflict"]
