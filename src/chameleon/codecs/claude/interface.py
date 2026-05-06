"""Claude codec for the interface domain (TUI/voice/notifications).

V0 thin slice (only fields the schemastore.org Claude schema models):
  fullscreen           ↔ tui ("fullscreen" | "default")
  status_line_command  ↔ statusLine.command
  voice_enabled        ↔ voiceEnabled
  motion_reduced       ↔ prefersReducedMotion

`interface.editor_mode` and `interface.notification_channel` are
documented in the design dossier but are not in schemastore.org's
published schema yet — operators who set them get a LossWarning.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.interface import Interface


class _ClaudeStatusLine(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "command"
    command: str | None = None


class ClaudeInterfaceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tui: str | None = None
    statusLine: _ClaudeStatusLine = Field(default_factory=_ClaudeStatusLine)  # noqa: N815
    voiceEnabled: bool | None = None  # noqa: N815
    prefersReducedMotion: bool | None = None  # noqa: N815


class ClaudeInterfaceCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.INTERFACE
    target_section: ClassVar[type[BaseModel]] = ClaudeInterfaceSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("tui",)),
            FieldPath(segments=("statusLine", "command")),
            FieldPath(segments=("voiceEnabled",)),
            FieldPath(segments=("prefersReducedMotion",)),
        }
    )

    @staticmethod
    def to_target(model: Interface, ctx: TranspileCtx) -> ClaudeInterfaceSection:
        section = ClaudeInterfaceSection()
        if model.fullscreen is not None:
            section.tui = "fullscreen" if model.fullscreen else "default"
        if model.status_line_command is not None:
            section.statusLine = _ClaudeStatusLine(
                type="command", command=model.status_line_command
            )
        if model.voice_enabled is not None:
            section.voiceEnabled = model.voice_enabled
        if model.motion_reduced is not None:
            section.prefersReducedMotion = model.motion_reduced
        if model.editor_mode is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.INTERFACE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "interface.editor_mode is documented in the design dossier "
                        "but not in schemastore.org's published Claude schema yet"
                    ),
                )
            )
        if model.notification_channel is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.INTERFACE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "interface.notification_channel not in schemastore.org's "
                        "published Claude schema yet"
                    ),
                )
            )
        if model.theme is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.INTERFACE,
                    target=BUILTIN_CLAUDE,
                    message="interface.theme has no first-class Claude equivalent",
                )
            )
        if model.file_opener is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.INTERFACE,
                    target=BUILTIN_CLAUDE,
                    message="interface.file_opener has no Claude equivalent (Codex-only)",
                )
            )
        return section

    @staticmethod
    def from_target(section: ClaudeInterfaceSection, ctx: TranspileCtx) -> Interface:
        iface = Interface()
        if section.tui is not None:
            iface.fullscreen = section.tui == "fullscreen"
        if section.statusLine.command is not None:
            iface.status_line_command = section.statusLine.command
        if section.voiceEnabled is not None:
            iface.voice_enabled = section.voiceEnabled
        if section.prefersReducedMotion is not None:
            iface.motion_reduced = section.prefersReducedMotion
        return iface


__all__ = ["ClaudeInterfaceCodec", "ClaudeInterfaceSection"]
