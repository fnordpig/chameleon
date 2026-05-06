"""Codex codec for directives.

V0: commit_attribution + system_prompt_file.
P1-E: personality (fixed-vocabulary StrEnum mirroring upstream).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex._generated import Personality as CodexPersonality
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.directives import Directives, Personality


class CodexDirectivesSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    model_instructions_file: str | None = None
    commit_attribution: str | None = None
    personality: CodexPersonality | None = None


class CodexDirectivesCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.DIRECTIVES
    target_section: ClassVar[type[BaseModel]] = CodexDirectivesSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("model_instructions_file",)),
            FieldPath(segments=("commit_attribution",)),
            FieldPath(segments=("personality",)),
        }
    )

    @staticmethod
    def to_target(model: Directives, ctx: TranspileCtx) -> CodexDirectivesSection:
        # Both enums are StrEnum with identical wire values (P1-E mirrors
        # upstream exactly). Look up by value to keep the boundary explicit
        # and immune to accidental name drift between neutral and upstream.
        codex_personality = (
            CodexPersonality(model.personality.value) if model.personality is not None else None
        )
        return CodexDirectivesSection(
            model_instructions_file=model.system_prompt_file,
            commit_attribution=model.commit_attribution,
            personality=codex_personality,
        )

    @staticmethod
    def from_target(section: CodexDirectivesSection, ctx: TranspileCtx) -> Directives:
        neutral_personality = (
            Personality(section.personality.value) if section.personality is not None else None
        )
        return Directives(
            system_prompt_file=section.model_instructions_file,
            commit_attribution=section.commit_attribution,
            personality=neutral_personality,
        )


__all__ = ["CodexDirectivesCodec", "CodexDirectivesSection"]
