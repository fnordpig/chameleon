"""Codex codec for environment.

Maps:
  variables ↔ [shell_environment_policy].set
  inherit   ↔ [shell_environment_policy].inherit (Wave-10 §15.x)

The neutral ``InheritPolicy`` enum (``all``/``core``/``none``) was chosen
to mirror Codex's ``ShellEnvironmentPolicyInherit`` RootModel union
exactly — same vocabulary, same wire values, so the round-trip is a
direct lookup-by-value.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.environment import Environment, InheritPolicy


class _CodexShellEnvPolicy(BaseModel):
    # ``extra="allow"`` (B1) — Codex's shell_environment_policy supports
    # additional knobs (e.g. ``ignore_default_excludes``,
    # ``include_only``) that we don't model in V0; preserve them through
    # round-trip via ``__pydantic_extra__``.
    model_config = ConfigDict(extra="allow")
    set: dict[str, str] = Field(default_factory=dict)
    # Wave-10 §15.x — environment.inherit ↔ shell_environment_policy.inherit.
    # Stored as the raw wire string (not the upstream
    # ``ShellEnvironmentPolicyInherit`` RootModel) so an unrecognized value
    # disassembled from live config can land in the section, hit
    # ``from_target``, and emit a typed LossWarning rather than crash
    # inside Pydantic. Mirrors the ``approvals_reviewer`` /
    # ``forced_login_method`` patterns elsewhere in the Codex codecs.
    inherit: str | None = None


class CodexEnvironmentSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    shell_environment_policy: _CodexShellEnvPolicy = Field(default_factory=_CodexShellEnvPolicy)


# Wave-10 §15.x — bidirectional value-by-value mapping. The neutral
# ``InheritPolicy`` enum mirrors the Codex wire vocabulary exactly so a
# future upstream rename fails typing here, not silently at runtime.
_INHERIT_TO_CODEX: dict[InheritPolicy, str] = {
    InheritPolicy.ALL: "all",
    InheritPolicy.CORE: "core",
    InheritPolicy.NONE: "none",
}
_CODEX_TO_INHERIT: dict[str, InheritPolicy] = {
    "all": InheritPolicy.ALL,
    "core": InheritPolicy.CORE,
    "none": InheritPolicy.NONE,
}


class CodexEnvironmentCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.ENVIRONMENT
    target_section: ClassVar[type[BaseModel]] = CodexEnvironmentSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("shell_environment_policy", "set")),
            # Wave-10 §15.x:
            FieldPath(segments=("shell_environment_policy", "inherit")),
        }
    )

    @staticmethod
    def to_target(model: Environment, ctx: TranspileCtx) -> CodexEnvironmentSection:
        policy = _CodexShellEnvPolicy(set=dict(model.variables))
        if model.inherit is not None:
            policy.inherit = _INHERIT_TO_CODEX[model.inherit]
        return CodexEnvironmentSection(shell_environment_policy=policy)

    @staticmethod
    def from_target(section: CodexEnvironmentSection, ctx: TranspileCtx) -> Environment:
        env = Environment(variables=dict(section.shell_environment_policy.set))
        raw_inherit = section.shell_environment_policy.inherit
        if raw_inherit is not None:
            mapped = _CODEX_TO_INHERIT.get(raw_inherit)
            if mapped is None:
                ctx.warn(
                    LossWarning(
                        domain=Domains.ENVIRONMENT,
                        target=BUILTIN_CODEX,
                        message=(
                            f"shell_environment_policy.inherit "
                            f"{raw_inherit!r} is not in the documented "
                            "vocabulary ('all'/'core'/'none'); dropping"
                        ),
                        field_path=FieldPath(segments=("shell_environment_policy", "inherit")),
                    )
                )
            else:
                env.inherit = mapped
        return env


__all__ = ["CodexEnvironmentCodec", "CodexEnvironmentSection"]
