"""interface domain — human-facing UX (TUI, voice, notifications).

V0: typed schema only; codecs deferred.

voice promoted from a flat bool to a structured object. The
upstream Claude `~/.claude/settings.json` carries both a documented
``voiceEnabled: bool`` and an undocumented ``voice: {enabled, mode}``
object. We model the richer shape so the dictation mode survives
round-trip; the Claude codec preserves the legacy bool for the
documented schema's compat.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class VoiceMode(StrEnum):
    """Push-to-talk dictation mode.

    Only ``hold`` has been observed in the wild (in the sanitized
    operator exemplar at ``tests/fixtures/exemplar/``). The upstream
    Claude JSON schema does not document the ``voice`` object at all —
    if a future build adds modes (``toggle``, ``always``…) extend this
    enum and re-run round-trip tests against an updated fixture.
    Speculative members are intentionally NOT pre-added.
    """

    HOLD = "hold"


class Voice(BaseModel):
    """Voice/dictation preferences.

    ``enabled`` mirrors the upstream documented ``voiceEnabled`` bool;
    ``mode`` carries the (currently undocumented) push-to-talk mode.
    """

    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    mode: VoiceMode | None = None


class Interface(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fullscreen: bool | None = None
    theme: str | None = None
    editor_mode: str | None = None
    status_line_command: str | None = None
    file_opener: str | None = None
    voice: Voice | None = None
    motion_reduced: bool | None = None
    notification_channel: str | None = None


__all__ = ["Interface", "Voice", "VoiceMode"]
