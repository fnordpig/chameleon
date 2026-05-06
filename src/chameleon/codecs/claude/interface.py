"""Claude codec for the interface domain (TUI/voice/notifications).

V0 thin slice (only fields the schemastore.org Claude schema models):
  fullscreen           ↔ tui ("fullscreen" | "default")
  status_line_command  ↔ statusLine.command
  voice (P1-C)         ↔ voice {enabled, mode} + voiceEnabled (legacy)
  motion_reduced       ↔ prefersReducedMotion

`interface.editor_mode` and `interface.notification_channel` are
documented in the design dossier but are not in schemastore.org's
published schema yet — operators who set them get a LossWarning.

P1-C — voice as a structured object
-----------------------------------
The exemplar at ``tests/fixtures/exemplar/home/_claude/settings.json``
carries BOTH a ``voiceEnabled: bool`` (documented in the upstream JSON
schema) AND a ``voice: {enabled, mode}`` object (undocumented; written
by the runtime's /voice flow). The two booleans CAN disagree at
runtime. The codec rule:

  * On disassemble (``from_target``): if both are present and disagree,
    prefer ``voice.enabled`` (the structured form is the canonical
    source — it carries strictly more information than the bool) and
    emit a typed ``LossWarning`` naming the conflict so the operator
    can resolve upstream.
  * On assemble (``to_target``): write BOTH the structured object and
    the legacy bool, kept in agreement. This maximizes compat with
    consumers that only honour the documented ``voiceEnabled`` field
    AND with the runtime's /voice flow that reads/writes the
    structured object.

The structured ``voice`` field is NOT in ``claimed_paths`` because
``ClaudeCodeSettings`` (upstream-canonized) does not model it — the
schema-drift verifier walks ``claimed_paths`` against that root and
would refuse to register the codec. The wire-level
``ClaudeInterfaceSection`` accepts the field directly as a typed
optional submodel.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.interface import Interface, Voice, VoiceMode


class _ClaudeStatusLine(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "command"
    command: str | None = None


class _ClaudeVoice(BaseModel):
    """Wire shape of the (undocumented) ``voice`` object.

    ``mode`` is typed as ``str`` here (not ``VoiceMode``) so the codec
    surface accepts unknown future modes without crashing — the
    from_target translator filters to known ``VoiceMode`` members and
    routes anything else through a LossWarning rather than a
    ValidationError.
    """

    model_config = ConfigDict(extra="allow")
    enabled: bool | None = None
    mode: str | None = None


class ClaudeInterfaceSection(BaseModel):
    # ``extra="allow"`` (B1) — unclaimed top-level keys round-trip
    # through ``__pydantic_extra__`` and are re-emitted by the
    # assembler.
    model_config = ConfigDict(extra="allow")
    tui: str | None = None
    statusLine: _ClaudeStatusLine = Field(default_factory=_ClaudeStatusLine)  # noqa: N815
    voice: _ClaudeVoice | None = None
    voiceEnabled: bool | None = None  # noqa: N815
    prefersReducedMotion: bool | None = None  # noqa: N815


class ClaudeInterfaceCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.INTERFACE
    target_section: ClassVar[type[BaseModel]] = ClaudeInterfaceSection
    # NOTE: ("voice",) is intentionally absent — it is not modelled by
    # the upstream-canonized ``ClaudeCodeSettings``; see module docstring.
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
        if model.voice is not None:
            wire_mode: str | None = model.voice.mode.value if model.voice.mode is not None else None
            section.voice = _ClaudeVoice(
                enabled=model.voice.enabled,
                mode=wire_mode,
            )
            # Mirror the structured `enabled` to the legacy documented
            # `voiceEnabled` bool so consumers that only know the
            # documented schema still see a consistent value.
            if model.voice.enabled is not None:
                section.voiceEnabled = model.voice.enabled
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
        # Voice resolution. Three input shapes possible:
        #   1. only structured `voice` present  → use it.
        #   2. only legacy `voiceEnabled` bool   → synthesize Voice(enabled=…).
        #   3. both present                      → prefer structured; on
        #                                          disagreement, warn.
        if section.voice is not None or section.voiceEnabled is not None:
            structured = section.voice
            structured_enabled = structured.enabled if structured is not None else None
            legacy_enabled = section.voiceEnabled
            if (
                structured_enabled is not None
                and legacy_enabled is not None
                and structured_enabled != legacy_enabled
            ):
                ctx.warn(
                    LossWarning(
                        domain=Domains.INTERFACE,
                        target=BUILTIN_CLAUDE,
                        message=(
                            "voice.enabled and voiceEnabled disagree "
                            f"({structured_enabled!r} vs {legacy_enabled!r}); "
                            "preferring structured voice.enabled — the documented "
                            "voiceEnabled bool is treated as a legacy mirror"
                        ),
                    )
                )
            chosen_enabled = (
                structured_enabled if structured_enabled is not None else legacy_enabled
            )
            chosen_mode: VoiceMode | None = None
            if structured is not None and structured.mode is not None:
                try:
                    chosen_mode = VoiceMode(structured.mode)
                except ValueError:
                    ctx.warn(
                        LossWarning(
                            domain=Domains.INTERFACE,
                            target=BUILTIN_CLAUDE,
                            message=(
                                f"unknown voice.mode {structured.mode!r}; "
                                "extend schema.interface.VoiceMode to model it. "
                                "Dropping mode for this round-trip."
                            ),
                        )
                    )
            iface.voice = Voice(enabled=chosen_enabled, mode=chosen_mode)
        if section.prefersReducedMotion is not None:
            iface.motion_reduced = section.prefersReducedMotion
        return iface


__all__ = ["ClaudeInterfaceCodec", "ClaudeInterfaceSection"]
