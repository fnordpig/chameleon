"""environment domain — process context the agent runs in."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class InheritPolicy(Enum):
    ALL = "all"
    CORE = "core"
    NONE = "none"


class Environment(BaseModel):
    """Variables and execution context.

    V0 codecs cover `variables` only.
    """

    model_config = ConfigDict(extra="forbid")

    variables: dict[str, str] = Field(default_factory=dict)
    inherit: InheritPolicy | None = None
    include_only: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    additional_directories: list[str] = Field(default_factory=list)
    respect_gitignore: bool | None = None


__all__ = ["Environment", "InheritPolicy"]
