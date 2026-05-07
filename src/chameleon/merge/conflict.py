"""Typed conflict record (a record we couldn't resolve consensually)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from chameleon.merge.changeset import ChangeRecord
from chameleon.schema.neutral import Resolution


class Conflict(BaseModel):
    """A neutral key whose four sources cannot be reconciled automatically.

    ``prior_decision`` carries the operator's previously-stored
    ``Resolution`` for this path (resolution-memory spec §1) when the
    engine looked one up but the invalidation hash had drifted. The
    interactive resolver (W15-B) renders it as a default; non-interactive
    resolvers ignore it. ``None`` when no prior decision exists or the
    hash matched (in which case the engine auto-applied the decision and
    never emitted a Conflict).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    record: ChangeRecord
    prior_decision: Resolution | None = None


__all__ = ["Conflict"]
