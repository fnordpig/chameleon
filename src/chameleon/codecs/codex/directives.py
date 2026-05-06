"""Codex codec for directives. V0: commit_attribution + system_prompt_file."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.directives import Directives


class CodexDirectivesSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_instructions_file: str | None = None
    commit_attribution: str | None = None


class CodexDirectivesCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.DIRECTIVES
    target_section: ClassVar[type[BaseModel]] = CodexDirectivesSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("model_instructions_file",)),
            FieldPath(segments=("commit_attribution",)),
        }
    )

    @staticmethod
    def to_target(model: Directives, ctx: TranspileCtx) -> CodexDirectivesSection:
        return CodexDirectivesSection(
            model_instructions_file=model.system_prompt_file,
            commit_attribution=model.commit_attribution,
        )

    @staticmethod
    def from_target(section: CodexDirectivesSection, ctx: TranspileCtx) -> Directives:
        return Directives(
            system_prompt_file=section.model_instructions_file,
            commit_attribution=section.commit_attribution,
        )


__all__ = ["CodexDirectivesCodec", "CodexDirectivesSection"]
