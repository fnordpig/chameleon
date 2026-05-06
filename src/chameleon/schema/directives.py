"""directives domain — how the agent thinks and writes.

Owns: system_prompt_file pointer, output_style, language, personality,
commit_attribution, verbosity, show_thinking_summary. V0 codecs cover
commit_attribution + system_prompt_file only; P1-E adds personality
as a first-class neutral field. The remaining fields are typed schema
slots with deferred codec implementation.
"""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Verbosity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Personality(StrEnum):
    """Fixed-vocabulary personality selector for the agent (P1-E).

    Mirrors Codex's upstream-canonized ``Personality`` enum exactly.
    Modelled as a fixed enum rather than a free string because Codex
    rejects values outside this set — a free string would permit
    neutral configurations that fail to round-trip into Codex.

    Claude has no equivalent concept; the Claude codec emits a
    typed ``LossWarning`` when this field is set in neutral.
    """

    NONE = "none"
    FRIENDLY = "friendly"
    PRAGMATIC = "pragmatic"


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
    personality: Personality | None = None
    verbosity: Verbosity | None = None
    show_thinking_summary: bool | None = None


__all__ = ["Directives", "Personality", "Verbosity"]
