"""directives domain — how the agent thinks and writes.

Owns: system_prompt_file pointer, output_style, language, personality,
commit_attribution, verbosity, show_thinking_summary. V0 codecs cover
commit_attribution + system_prompt_file only; the rest are typed
schema fields with deferred codec implementation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Verbosity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Directives(BaseModel):
    """Behaviour-shaping configuration."""

    model_config = ConfigDict(extra="forbid")

    system_prompt_file: str | None = Field(
        default=None,
        description="Filesystem path to a markdown file used as the agent's system prompt.",
    )
    commit_attribution: str | None = None
    output_style: str | None = None
    language: str | None = None
    personality: str | None = None
    verbosity: Verbosity | None = None
    show_thinking_summary: bool | None = None


__all__ = ["Directives", "Verbosity"]
