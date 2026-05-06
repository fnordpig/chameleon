"""Codex codec for the authorization domain.

V0 thin slice:
  default_mode                ↔ sandbox_mode
  filesystem.allow_write      ↔ [sandbox_workspace_write].writable_roots
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.authorization import Authorization, DefaultMode, FilesystemPolicy

_DEFAULT_MODE_TO_CODEX: dict[DefaultMode, str] = {
    DefaultMode.READ_ONLY: "read-only",
    DefaultMode.WORKSPACE_WRITE: "workspace-write",
    DefaultMode.FULL_ACCESS: "danger-full-access",
}
_CODEX_TO_DEFAULT_MODE: dict[str, DefaultMode] = {
    "read-only": DefaultMode.READ_ONLY,
    "workspace-write": DefaultMode.WORKSPACE_WRITE,
    "danger-full-access": DefaultMode.FULL_ACCESS,
}


class _CodexSandboxWorkspaceWrite(BaseModel):
    model_config = ConfigDict(extra="allow")
    writable_roots: list[str] = Field(default_factory=list)


class CodexAuthorizationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sandbox_mode: str | None = None
    sandbox_workspace_write: _CodexSandboxWorkspaceWrite = Field(
        default_factory=_CodexSandboxWorkspaceWrite
    )


class CodexAuthorizationCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.AUTHORIZATION
    target_section: ClassVar[type[BaseModel]] = CodexAuthorizationSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("sandbox_mode",)),
            FieldPath(segments=("sandbox_workspace_write", "writable_roots")),
        }
    )

    @staticmethod
    def to_target(model: Authorization, ctx: TranspileCtx) -> CodexAuthorizationSection:
        section = CodexAuthorizationSection()
        if model.default_mode is not None:
            section.sandbox_mode = _DEFAULT_MODE_TO_CODEX[model.default_mode]
        section.sandbox_workspace_write.writable_roots = list(model.filesystem.allow_write)
        if model.allow_patterns or model.ask_patterns or model.deny_patterns:
            ctx.warn(
                LossWarning(
                    domain=Domains.AUTHORIZATION,
                    target=BUILTIN_CODEX,
                    message=(
                        "authorization.{allow,ask,deny}_patterns are Claude-specific "
                        "shell-pattern allow-lists; the Codex equivalent (named "
                        "[permissions.<name>] profiles) lands in §15.1"
                    ),
                )
            )
        if (
            model.network.allowed_domains
            or model.network.denied_domains
            or model.network.allow_local_binding is not None
            or model.network.allow_unix_sockets
        ):
            ctx.warn(
                LossWarning(
                    domain=Domains.AUTHORIZATION,
                    target=BUILTIN_CODEX,
                    message=(
                        "authorization.network mapping to Codex's named permission "
                        "profiles is deferred to §15.1"
                    ),
                )
            )
        if model.filesystem.allow_read or model.filesystem.deny_read or model.filesystem.deny_write:
            ctx.warn(
                LossWarning(
                    domain=Domains.AUTHORIZATION,
                    target=BUILTIN_CODEX,
                    message=(
                        "filesystem.{allow_read, deny_read, deny_write} have no "
                        "Codex sandbox equivalents in V0 (§15.1)"
                    ),
                )
            )
        return section

    @staticmethod
    def from_target(section: CodexAuthorizationSection, ctx: TranspileCtx) -> Authorization:
        auth = Authorization()
        if section.sandbox_mode is not None:
            mapped = _CODEX_TO_DEFAULT_MODE.get(section.sandbox_mode)
            if mapped is not None:
                auth.default_mode = mapped
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.AUTHORIZATION,
                        target=BUILTIN_CODEX,
                        message=(
                            f"sandbox_mode {section.sandbox_mode!r} has no neutral "
                            f"equivalent in V0 (§15.1)"
                        ),
                    )
                )
        auth.filesystem = FilesystemPolicy(
            allow_write=list(section.sandbox_workspace_write.writable_roots),
        )
        return auth


__all__ = ["CodexAuthorizationCodec", "CodexAuthorizationSection"]
