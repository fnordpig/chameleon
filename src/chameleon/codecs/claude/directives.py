"""Claude codec for the directives domain — V0 covers commit_attribution
and system_prompt_file only.

Maps:
  system_prompt_file -> outputStyle
  commit_attribution -> attribution.commit
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.directives import Directives


class ClaudeAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commit: str | None = None
    pr: str | None = None


class ClaudeDirectivesSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outputStyle: str | None = None  # noqa: N815
    attribution: ClaudeAttribution = ClaudeAttribution()


class ClaudeDirectivesCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.DIRECTIVES
    target_section: ClassVar[type[BaseModel]] = ClaudeDirectivesSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("outputStyle",)),
            FieldPath(segments=("attribution", "commit")),
        }
    )

    @staticmethod
    def to_target(model: Directives, ctx: TranspileCtx) -> ClaudeDirectivesSection:
        section = ClaudeDirectivesSection()
        if model.system_prompt_file is not None:
            section.outputStyle = model.system_prompt_file
        if model.commit_attribution is not None:
            section.attribution = ClaudeAttribution(commit=model.commit_attribution)
        return section

    @staticmethod
    def from_target(section: ClaudeDirectivesSection, ctx: TranspileCtx) -> Directives:
        return Directives(
            system_prompt_file=section.outputStyle,
            commit_attribution=section.attribution.commit,
        )


__all__ = [
    "ClaudeAttribution",
    "ClaudeDirectivesCodec",
    "ClaudeDirectivesSection",
]
