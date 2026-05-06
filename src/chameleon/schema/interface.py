"""interface domain — human-facing UX (TUI, voice, notifications).

V0: typed schema only; codecs deferred (§15.3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Interface(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fullscreen: bool | None = None
    theme: str | None = None
    editor_mode: str | None = None
    status_line_command: str | None = None
    file_opener: str | None = None
    voice_enabled: bool | None = None
    motion_reduced: bool | None = None
    notification_channel: str | None = None


__all__ = ["Interface"]
