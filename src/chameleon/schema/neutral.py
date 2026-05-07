"""The composed Neutral schema — eight domains + profiles + pass-through."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
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
# requires an explicit migration spec.
SchemaVersion = Literal[1]


class ResolutionDecisionKind(StrEnum):
    """How an operator resolved a conflict (resolution-memory spec ).

    Persisted in ``Neutral.resolutions`` so the engine can replay the
    operator's decision on subsequent merges instead of re-prompting.
    """

    TAKE_NEUTRAL = "take_neutral"
    """Neutral (N₁) wins."""

    TAKE_LKG = "take_lkg"
    """Last-known-good (N₀) wins."""

    TAKE_TARGET = "take_target"
    """A specific target wins; ``decision_target`` names which one."""

    TARGET_SPECIFIC = "target_specific"
    """Preserve each target's value separately; no cross-target propagation."""

    SKIP = "skip"
    """Leave unresolved. Rare; not auto-replayed (re-prompts each merge)."""


class Resolution(BaseModel):
    """One operator decision keyed by ``FieldPath.render()``.

    ``decision_hash`` captures the (N₀, N₁, per_target) shape the operator
    decided over; on the next merge the engine recomputes the hash from
    the current ``ChangeRecord`` and only auto-applies if it matches.
    """

    model_config = ConfigDict(extra="forbid")

    decided_at: datetime
    decision: ResolutionDecisionKind
    decision_target: TargetId | None = None
    decision_hash: str


class Resolutions(BaseModel):
    """Persisted operator decisions.

    Keyed by ``FieldPath.render()``-with-discriminators (e.g.
    ``identity.model[claude]`` or
    ``capabilities.plugin_marketplaces[archivium-marketplace]``) so a
    keyed-dict leaf gets one entry per dict key, mirroring the walker's
    per-key decomposition (issue #44).
    """

    model_config = ConfigDict(extra="forbid")

    items: dict[str, Resolution] = Field(default_factory=dict)


class Neutral(BaseModel):
    """The complete neutral form.

    Top-level fields are the implicit base profile. `profiles` holds
    named overlays; `targets` holds per-target pass-through bags;
    `resolutions` holds persisted operator conflict decisions.
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

    # Persisted operator conflict decisions (resolution-memory spec ).
    resolutions: Resolutions = Field(default_factory=Resolutions)


__all__ = [
    "Neutral",
    "Resolution",
    "ResolutionDecisionKind",
    "Resolutions",
    "SchemaVersion",
]
