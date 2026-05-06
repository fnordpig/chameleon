"""STUB: Codex governance codec — implementation deferred to follow-on spec (§15.4)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.governance import Governance


class CodexGovernanceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexGovernanceCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.GOVERNANCE
    target_section: ClassVar[type[BaseModel]] = CodexGovernanceSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset()

    @staticmethod
    def to_target(model: Governance, ctx: TranspileCtx) -> CodexGovernanceSection:
        msg = "Codex governance codec deferred to follow-on spec (§15.4)"
        raise NotImplementedError(msg)

    @staticmethod
    def from_target(section: CodexGovernanceSection, ctx: TranspileCtx) -> Governance:
        msg = "Codex governance codec deferred to follow-on spec (§15.4)"
        raise NotImplementedError(msg)


__all__ = ["CodexGovernanceCodec", "CodexGovernanceSection"]
