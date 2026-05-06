"""Claude codec for environment.variables — maps directly to settings.json `env`."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.environment import Environment


class ClaudeEnvironmentSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    env: dict[str, str] = Field(default_factory=dict)


class ClaudeEnvironmentCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.ENVIRONMENT
    target_section: ClassVar[type[BaseModel]] = ClaudeEnvironmentSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset({FieldPath(segments=("env",))})

    @staticmethod
    def to_target(model: Environment, ctx: TranspileCtx) -> ClaudeEnvironmentSection:
        return ClaudeEnvironmentSection(env=dict(model.variables))

    @staticmethod
    def from_target(section: ClaudeEnvironmentSection, ctx: TranspileCtx) -> Environment:
        return Environment(variables=dict(section.env))


__all__ = ["ClaudeEnvironmentCodec", "ClaudeEnvironmentSection"]
