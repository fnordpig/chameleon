"""STUB: Claude interface codec — implementation deferred to follow-on spec (§15.3)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.interface import Interface


class ClaudeInterfaceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaudeInterfaceCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.INTERFACE
    target_section: ClassVar[type[BaseModel]] = ClaudeInterfaceSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset()

    @staticmethod
    def to_target(model: Interface, ctx: TranspileCtx) -> ClaudeInterfaceSection:
        msg = "Claude interface codec deferred to follow-on spec (§15.3)"
        raise NotImplementedError(msg)

    @staticmethod
    def from_target(section: ClaudeInterfaceSection, ctx: TranspileCtx) -> Interface:
        msg = "Claude interface codec deferred to follow-on spec (§15.3)"
        raise NotImplementedError(msg)


__all__ = ["ClaudeInterfaceCodec", "ClaudeInterfaceSection"]
