"""The composed Neutral schema — eight domains + profiles + pass-through."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import TargetId
from chameleon.schema.authorization import Authorization
from chameleon.schema.capabilities import Capabilities
from chameleon.schema.directives import Directives
from chameleon.schema.environment import Environment
from chameleon.schema.governance import Governance
from chameleon.schema.identity import Identity
from chameleon.schema.interface import Interface
from chameleon.schema.lifecycle import Lifecycle
from chameleon.schema.passthrough import PassThroughBag
from chameleon.schema.profiles import Profile

# Schema version is closed-vocabulary: only known versions parse. Bumping
# requires an explicit migration spec (§15.10).
SchemaVersion = Literal[1]


class Neutral(BaseModel):
    """The complete neutral form.

    Top-level fields are the implicit base profile. `profiles` holds
    named overlays; `targets` holds per-target pass-through bags.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion

    # The implicit base profile.
    identity: Identity = Field(default_factory=Identity)
    directives: Directives = Field(default_factory=Directives)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    authorization: Authorization = Field(default_factory=Authorization)
    environment: Environment = Field(default_factory=Environment)
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)
    interface: Interface = Field(default_factory=Interface)
    governance: Governance = Field(default_factory=Governance)

    # Named overlays.
    profiles: dict[str, Profile] = Field(default_factory=dict)

    # Per-target pass-through.
    targets: dict[TargetId, PassThroughBag] = Field(default_factory=dict)


__all__ = ["Neutral", "SchemaVersion"]
