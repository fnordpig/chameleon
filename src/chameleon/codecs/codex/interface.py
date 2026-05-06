"""STUB: Codex interface codec — implementation deferred to follow-on spec (§15.3)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.interface import Interface


class CodexInterfaceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexInterfaceCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.INTERFACE
    target_section: ClassVar[type[BaseModel]] = CodexInterfaceSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset()

    @staticmethod
    def to_target(model: Interface, ctx: TranspileCtx) -> CodexInterfaceSection:
        msg = "Codex interface codec deferred to follow-on spec (§15.3)"
        raise NotImplementedError(msg)

    @staticmethod
    def from_target(section: CodexInterfaceSection, ctx: TranspileCtx) -> Interface:
        msg = "Codex interface codec deferred to follow-on spec (§15.3)"
        raise NotImplementedError(msg)


__all__ = ["CodexInterfaceCodec", "CodexInterfaceSection"]
