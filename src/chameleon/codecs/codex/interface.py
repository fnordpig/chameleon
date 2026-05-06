"""Codex codec for the interface domain ([tui], file_opener)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.interface import Interface


class _CodexTui(BaseModel):
    model_config = ConfigDict(extra="allow")
    theme: str | None = None
    alternate_screen: str | None = None  # "auto" | "always" | "never"


class CodexInterfaceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tui: _CodexTui = Field(default_factory=_CodexTui)
    file_opener: str | None = None


class CodexInterfaceCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.INTERFACE
    target_section: ClassVar[type[BaseModel]] = CodexInterfaceSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("tui", "theme")),
            FieldPath(segments=("tui", "alternate_screen")),
            FieldPath(segments=("file_opener",)),
        }
    )

    @staticmethod
    def to_target(model: Interface, ctx: TranspileCtx) -> CodexInterfaceSection:
        section = CodexInterfaceSection()
        if model.fullscreen is not None:
            section.tui.alternate_screen = "always" if model.fullscreen else "never"
        if model.theme is not None:
            section.tui.theme = model.theme
        if model.file_opener is not None:
            section.file_opener = model.file_opener
        if model.editor_mode is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.INTERFACE,
                    target=BUILTIN_CODEX,
                    message="interface.editor_mode has no Codex equivalent (Claude-only)",
                )
            )
        if model.voice is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.INTERFACE,
                    target=BUILTIN_CODEX,
                    message=(
                        "interface.voice has no Codex equivalent (Claude-only); "
                        "dropping voice on encode to Codex"
                    ),
                )
            )
        return section

    @staticmethod
    def from_target(section: CodexInterfaceSection, ctx: TranspileCtx) -> Interface:
        iface = Interface()
        if section.tui.theme is not None:
            iface.theme = section.tui.theme
        if section.tui.alternate_screen is not None:
            iface.fullscreen = section.tui.alternate_screen == "always"
        if section.file_opener is not None:
            iface.file_opener = section.file_opener
        return iface


__all__ = ["CodexInterfaceCodec", "CodexInterfaceSection"]
