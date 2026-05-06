"""Codex codec for identity.

Maps:
  reasoning_effort   -> model_reasoning_effort  (minimal/low/medium/high/xhigh)
  model[codex]       -> model
  thinking           -> n/a in Codex (LossWarning if set)

P1-F — Codex-only identity tuning knobs (claimed here; no analogue in Claude):
  context_window     -> model_context_window
  compact_threshold  -> model_auto_compact_token_limit
  model_catalog_path -> model_catalog_json

The neutral schema uses cross-target vocabulary; this codec is the single
place that maps neutral names to Codex's wire names. Round-trip preserves
each field exactly (subject to the int/str types declared on the section).
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
    # P1-F — Codex-only identity tuning knobs. Field names mirror the
    # upstream Codex config.toml keys exactly so the assembler can splat
    # via `model_dump(exclude_none=True)`.
    model_context_window: int | None = None
    model_auto_compact_token_limit: int | None = None
    model_catalog_json: str | None = None


class CodexIdentityCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.IDENTITY
    target_section: ClassVar[type[BaseModel]] = CodexIdentitySection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("model",)),
            FieldPath(segments=("model_reasoning_effort",)),
            # P1-F additions:
            FieldPath(segments=("model_context_window",)),
            FieldPath(segments=("model_auto_compact_token_limit",)),
            FieldPath(segments=("model_catalog_json",)),
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
        # P1-F — Codex-only identity tuning knobs. Lossless on Codex.
        if model.context_window is not None:
            section.model_context_window = model.context_window
        if model.compact_threshold is not None:
            section.model_auto_compact_token_limit = model.compact_threshold
        if model.model_catalog_path is not None:
            section.model_catalog_json = model.model_catalog_path
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
        # P1-F — reverse mapping for Codex-only identity tuning knobs.
        if section.model_context_window is not None:
            ident.context_window = section.model_context_window
        if section.model_auto_compact_token_limit is not None:
            ident.compact_threshold = section.model_auto_compact_token_limit
        if section.model_catalog_json is not None:
            ident.model_catalog_path = section.model_catalog_json
        return ident


__all__ = ["CodexIdentityCodec", "CodexIdentitySection"]
