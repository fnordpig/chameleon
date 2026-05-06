"""STUB: Claude lifecycle codec — implementation deferred to follow-on spec (§15.2)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.lifecycle import Lifecycle


class ClaudeLifecycleSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaudeLifecycleCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.LIFECYCLE
    target_section: ClassVar[type[BaseModel]] = ClaudeLifecycleSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset()

    @staticmethod
    def to_target(model: Lifecycle, ctx: TranspileCtx) -> ClaudeLifecycleSection:
        msg = "Claude lifecycle codec deferred to follow-on spec (§15.2)"
        raise NotImplementedError(msg)

    @staticmethod
    def from_target(section: ClaudeLifecycleSection, ctx: TranspileCtx) -> Lifecycle:
        msg = "Claude lifecycle codec deferred to follow-on spec (§15.2)"
        raise NotImplementedError(msg)


__all__ = ["ClaudeLifecycleCodec", "ClaudeLifecycleSection"]
