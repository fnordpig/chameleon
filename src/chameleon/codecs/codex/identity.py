"""Codex codec for identity.

Maps:
  reasoning_effort -> model_reasoning_effort  (minimal/low/medium/high/xhigh)
  model[codex]     -> model
  thinking         -> n/a in Codex (LossWarning if set)
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.identity import Identity, ReasoningEffort


class CodexIdentitySection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    model_reasoning_effort: str | None = None


class CodexIdentityCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.IDENTITY
    target_section: ClassVar[type[BaseModel]] = CodexIdentitySection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("model",)),
            FieldPath(segments=("model_reasoning_effort",)),
        }
    )

    @staticmethod
    def to_target(model: Identity, ctx: TranspileCtx) -> CodexIdentitySection:
        section = CodexIdentitySection()
        if model.reasoning_effort is not None:
            section.model_reasoning_effort = model.reasoning_effort.value
        if model.model is not None:
            codex_model = model.model.get(BUILTIN_CODEX)
            if codex_model is not None:
                section.model = codex_model
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.IDENTITY,
                        target=BUILTIN_CODEX,
                        message=(
                            "identity.model has no entry for Codex; leaving Codex model unset"
                        ),
                        field_path=FieldPath(segments=("model",)),
                    )
                )
        if model.thinking is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.IDENTITY,
                    target=BUILTIN_CODEX,
                    message="identity.thinking has no Codex equivalent; not propagating",
                )
            )
        return section

    @staticmethod
    def from_target(section: CodexIdentitySection, ctx: TranspileCtx) -> Identity:
        ident = Identity()
        if section.model_reasoning_effort is not None:
            try:
                ident.reasoning_effort = ReasoningEffort(section.model_reasoning_effort)
            except ValueError:
                ctx.warn(
                    LossWarning(
                        domain=Domains.IDENTITY,
                        target=BUILTIN_CODEX,
                        message=(
                            f"unknown model_reasoning_effort "
                            f"{section.model_reasoning_effort!r}; dropping"
                        ),
                    )
                )
        if section.model is not None:
            ident.model = {BUILTIN_CODEX: section.model}
        return ident


__all__ = ["CodexIdentityCodec", "CodexIdentitySection"]
