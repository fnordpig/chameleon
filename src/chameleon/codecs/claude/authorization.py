"""STUB: Claude authorization codec — implementation deferred to follow-on spec (§15.1)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.authorization import Authorization


class ClaudeAuthorizationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaudeAuthorizationCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.AUTHORIZATION
    target_section: ClassVar[type[BaseModel]] = ClaudeAuthorizationSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset()

    @staticmethod
    def to_target(model: Authorization, ctx: TranspileCtx) -> ClaudeAuthorizationSection:
        msg = "Claude authorization codec deferred to follow-on spec (§15.1)"
        raise NotImplementedError(msg)

    @staticmethod
    def from_target(section: ClaudeAuthorizationSection, ctx: TranspileCtx) -> Authorization:
        msg = "Claude authorization codec deferred to follow-on spec (§15.1)"
        raise NotImplementedError(msg)


__all__ = ["ClaudeAuthorizationCodec", "ClaudeAuthorizationSection"]
