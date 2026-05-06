"""STUB: Codex authorization codec — implementation deferred to follow-on spec (§15.1)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.authorization import Authorization


class CodexAuthorizationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexAuthorizationCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.AUTHORIZATION
    target_section: ClassVar[type[BaseModel]] = CodexAuthorizationSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset()

    @staticmethod
    def to_target(model: Authorization, ctx: TranspileCtx) -> CodexAuthorizationSection:
        msg = "Codex authorization codec deferred to follow-on spec (§15.1)"
        raise NotImplementedError(msg)

    @staticmethod
    def from_target(section: CodexAuthorizationSection, ctx: TranspileCtx) -> Authorization:
        msg = "Codex authorization codec deferred to follow-on spec (§15.1)"
        raise NotImplementedError(msg)


__all__ = ["CodexAuthorizationCodec", "CodexAuthorizationSection"]
