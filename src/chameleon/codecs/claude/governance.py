"""STUB: Claude governance codec — implementation deferred to follow-on spec (§15.4)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.governance import Governance


class ClaudeGovernanceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaudeGovernanceCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.GOVERNANCE
    target_section: ClassVar[type[BaseModel]] = ClaudeGovernanceSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset()

    @staticmethod
    def to_target(model: Governance, ctx: TranspileCtx) -> ClaudeGovernanceSection:
        msg = "Claude governance codec deferred to follow-on spec (§15.4)"
        raise NotImplementedError(msg)

    @staticmethod
    def from_target(section: ClaudeGovernanceSection, ctx: TranspileCtx) -> Governance:
        msg = "Claude governance codec deferred to follow-on spec (§15.4)"
        raise NotImplementedError(msg)


__all__ = ["ClaudeGovernanceCodec", "ClaudeGovernanceSection"]
