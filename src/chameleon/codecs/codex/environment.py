"""Codex codec for environment.variables.

Maps to `[shell_environment_policy].set` in Codex's config.toml.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.environment import Environment


class _CodexShellEnvPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    set: dict[str, str] = Field(default_factory=dict)


class CodexEnvironmentSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shell_environment_policy: _CodexShellEnvPolicy = Field(default_factory=_CodexShellEnvPolicy)


class CodexEnvironmentCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.ENVIRONMENT
    target_section: ClassVar[type[BaseModel]] = CodexEnvironmentSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {FieldPath(segments=("shell_environment_policy", "set"))}
    )

    @staticmethod
    def to_target(model: Environment, ctx: TranspileCtx) -> CodexEnvironmentSection:
        return CodexEnvironmentSection(
            shell_environment_policy=_CodexShellEnvPolicy(set=dict(model.variables))
        )

    @staticmethod
    def from_target(section: CodexEnvironmentSection, ctx: TranspileCtx) -> Environment:
        return Environment(variables=dict(section.shell_environment_policy.set))


__all__ = ["CodexEnvironmentCodec", "CodexEnvironmentSection"]
