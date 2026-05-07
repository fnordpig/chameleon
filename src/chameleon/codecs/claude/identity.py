"""Claude codec for the identity domain.

Maps neutral.identity ↔ Claude settings.json keys:
  reasoning_effort   -> effortLevel  (low/medium/high/xhigh)
  thinking           -> alwaysThinkingEnabled
  model[claude]      -> model
  auth.method        -> forceLoginMethod  (Wave-10 §15.x slot, partial)
  auth.api_key_helper -> apiKeyHelper     (Wave-10 §15.x adjacent slot)

Wave-10 §15.x — ``identity.auth.method`` is partially supported on Claude.
Claude's wire enum ``ForceLoginMethod`` only models two of the five
neutral ``AuthMethod`` values:
  * ``oauth``   ↔ ``claudeai`` (OAuth into Claude.ai / Pro / Max)
  * ``api-key`` ↔ ``console``  (API-key billing flow into Console)
The remaining values (``bedrock``, ``vertex``, ``azure``) have no
``forceLoginMethod`` analogue — Claude reaches those provider lanes
through the per-provider env vars in the ``env`` codec instead.
``to_target`` emits a typed ``LossWarning`` when neutral selects one
of those three; the value still round-trips through the Codex codec
lane and any per-provider env config.

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

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.identity import AuthMethod, Identity, IdentityAuth, ReasoningEffort

# Wire bidirectional map for the two AuthMethod values Claude's
# ``forceLoginMethod`` enum models. Other neutral values produce a
# LossWarning at ``to_target`` time; unknown wire values produce a
# LossWarning at ``from_target`` time.
_AUTH_METHOD_TO_WIRE: dict[AuthMethod, str] = {
    AuthMethod.OAUTH: "claudeai",
    AuthMethod.API_KEY: "console",
}
_WIRE_TO_AUTH_METHOD: dict[str, AuthMethod] = {
    wire: method for method, wire in _AUTH_METHOD_TO_WIRE.items()
}


class ClaudeIdentitySection(BaseModel):
    """Typed slice of ClaudeSettings for the identity codec.

    Field names mirror the upstream Claude settings.json keys exactly
    (camelCase). The disassembler routes input by walking ClaudeSettings
    field names that match this section's; values are copied through
    Pydantic, never via raw dict access.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    model: str | None = None
    effortLevel: str | None = None  # noqa: N815  -- mirrors upstream JSON key
    alwaysThinkingEnabled: bool | None = None  # noqa: N815  -- mirrors upstream JSON key
    force_login_method: str | None = Field(default=None, alias="forceLoginMethod")
    api_key_helper: str | None = Field(default=None, alias="apiKeyHelper")


class ClaudeIdentityCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.IDENTITY
    target_section: ClassVar[type[BaseModel]] = ClaudeIdentitySection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("model",)),
            FieldPath(segments=("effortLevel",)),
            FieldPath(segments=("alwaysThinkingEnabled",)),
            FieldPath(segments=("forceLoginMethod",)),
            FieldPath(segments=("apiKeyHelper",)),
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
        # Wave-10 §15.x — auth.method translation.
        if model.auth.method is not None:
            wire = _AUTH_METHOD_TO_WIRE.get(model.auth.method)
            if wire is not None:
                section.force_login_method = wire
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.IDENTITY,
                        target=BUILTIN_CLAUDE,
                        message=(
                            f"identity.auth.method={model.auth.method.value!r} has "
                            "no Claude forceLoginMethod analogue (Claude wire enum "
                            "only models 'oauth' and 'api-key'); the provider lane "
                            "is selected via per-provider env vars instead"
                        ),
                        field_path=FieldPath(segments=("forceLoginMethod",)),
                    )
                )
        if model.auth.api_key_helper is not None:
            section.api_key_helper = model.auth.api_key_helper
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
        # Wave-10 §15.x — auth.method translation (reverse).
        auth = IdentityAuth()
        auth_set = False
        if section.force_login_method is not None:
            method = _WIRE_TO_AUTH_METHOD.get(section.force_login_method)
            if method is not None:
                auth.method = method
                auth_set = True
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.IDENTITY,
                        target=BUILTIN_CLAUDE,
                        message=(
                            f"unknown forceLoginMethod {section.force_login_method!r}; "
                            "dropping (no neutral AuthMethod analogue)"
                        ),
                        field_path=FieldPath(segments=("forceLoginMethod",)),
                    )
                )
        if section.api_key_helper is not None:
            auth.api_key_helper = section.api_key_helper
            auth_set = True
        if auth_set:
            ident.auth = auth
        return ident


__all__ = ["ClaudeIdentityCodec", "ClaudeIdentitySection"]
