"""Claude codec for the identity domain.

Maps neutral.identity ↔ Claude settings.json keys:
  reasoning_effort -> effortLevel  (low/medium/high/xhigh)
  thinking         -> alwaysThinkingEnabled
  model[claude]    -> model

P1-F — three Codex-only identity tuning knobs have no Claude analogue:
  context_window, compact_threshold, model_catalog_path
If any of these is set on the neutral Identity, this codec emits a
LossWarning naming P1-F (so the operator can see what didn't propagate
and the warnings are surfaced in MergeResult). The values themselves
survive the round-trip via the Codex codec lane; this codec's only job
is to be honest that Claude can't host them.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.identity import Identity, ReasoningEffort


class ClaudeIdentitySection(BaseModel):
    """Typed slice of ClaudeSettings for the identity codec.

    Field names mirror the upstream Claude settings.json keys exactly
    (camelCase). The disassembler routes input by walking ClaudeSettings
    field names that match this section's; values are copied through
    Pydantic, never via raw dict access.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    effortLevel: str | None = None  # noqa: N815  -- mirrors upstream JSON key
    alwaysThinkingEnabled: bool | None = None  # noqa: N815  -- mirrors upstream JSON key


class ClaudeIdentityCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.IDENTITY
    target_section: ClassVar[type[BaseModel]] = ClaudeIdentitySection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("model",)),
            FieldPath(segments=("effortLevel",)),
            FieldPath(segments=("alwaysThinkingEnabled",)),
        }
    )

    @staticmethod
    def to_target(model: Identity, ctx: TranspileCtx) -> ClaudeIdentitySection:
        section = ClaudeIdentitySection()
        if model.reasoning_effort is not None:
            section.effortLevel = model.reasoning_effort.value
        if model.thinking is not None:
            section.alwaysThinkingEnabled = model.thinking
        if model.model is not None:
            claude_model = model.model.get(BUILTIN_CLAUDE)
            if claude_model is not None:
                section.model = claude_model
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.IDENTITY,
                        target=BUILTIN_CLAUDE,
                        message=(
                            "identity.model has no entry for Claude; leaving Claude model unset"
                        ),
                        field_path=FieldPath(segments=("model",)),
                    )
                )
        # P1-F — Codex-only identity tuning knobs. No Claude analogue;
        # warn per field so the operator sees exactly what didn't propagate.
        if model.context_window is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.IDENTITY,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "P1-F: identity.context_window is a Codex-only tuning "
                        "knob (model_context_window); not propagating to Claude"
                    ),
                )
            )
        if model.compact_threshold is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.IDENTITY,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "P1-F: identity.compact_threshold is a Codex-only tuning "
                        "knob (model_auto_compact_token_limit); not propagating to Claude"
                    ),
                )
            )
        if model.model_catalog_path is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.IDENTITY,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "P1-F: identity.model_catalog_path is a Codex-only tuning "
                        "knob (model_catalog_json); not propagating to Claude"
                    ),
                )
            )
        return section

    @staticmethod
    def from_target(section: ClaudeIdentitySection, ctx: TranspileCtx) -> Identity:
        ident = Identity()
        if section.effortLevel is not None:
            try:
                ident.reasoning_effort = ReasoningEffort(section.effortLevel)
            except ValueError:
                ctx.warn(
                    LossWarning(
                        domain=Domains.IDENTITY,
                        target=BUILTIN_CLAUDE,
                        message=f"unknown effortLevel {section.effortLevel!r}; dropping",
                        field_path=FieldPath(segments=("effortLevel",)),
                    )
                )
        if section.alwaysThinkingEnabled is not None:
            ident.thinking = section.alwaysThinkingEnabled
        if section.model is not None:
            ident.model = {BUILTIN_CLAUDE: section.model}
        return ident


__all__ = ["ClaudeIdentityCodec", "ClaudeIdentitySection"]
