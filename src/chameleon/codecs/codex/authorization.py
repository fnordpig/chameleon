"""Codex codec for the authorization domain.

V0 thin slice:
  default_mode                ↔ sandbox_mode
  filesystem.allow_write      ↔ [sandbox_workspace_write].writable_roots
  reviewer (P1-G)             ↔ approvals_reviewer
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.codecs.codex._generated import ApprovalsReviewer
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.authorization import (
    Authorization,
    DefaultMode,
    FilesystemPolicy,
    Reviewer,
)

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

# P1-G — neutral ``authorization.reviewer`` <-> Codex ``approvals_reviewer``.
# Both enums share the same wire vocabulary; we map enum members rather than
# strings so a future upstream regen that drops or renames a value will
# fail typing here, not silently at runtime.
_REVIEWER_TO_CODEX: dict[Reviewer, ApprovalsReviewer] = {
    Reviewer.USER: ApprovalsReviewer.user,
    Reviewer.AUTO_REVIEW: ApprovalsReviewer.auto_review,
    Reviewer.GUARDIAN_SUBAGENT: ApprovalsReviewer.guardian_subagent,
}
_CODEX_TO_REVIEWER: dict[ApprovalsReviewer, Reviewer] = {
    ApprovalsReviewer.user: Reviewer.USER,
    ApprovalsReviewer.auto_review: Reviewer.AUTO_REVIEW,
    ApprovalsReviewer.guardian_subagent: Reviewer.GUARDIAN_SUBAGENT,
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
    # P1-G: stored as the raw wire string (not the upstream ``ApprovalsReviewer``
    # enum) so that an unrecognized value disassembled from live config can
    # land in the section, hit ``from_target``, and emit a typed LossWarning
    # rather than crash inside Pydantic. Mirrors the ``sandbox_mode`` pattern
    # immediately above.
    approvals_reviewer: str | None = None


class CodexAuthorizationCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.AUTHORIZATION
    target_section: ClassVar[type[BaseModel]] = CodexAuthorizationSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("sandbox_mode",)),
            FieldPath(segments=("sandbox_workspace_write", "writable_roots")),
            FieldPath(segments=("approvals_reviewer",)),
        }
    )

    @staticmethod
    def to_target(model: Authorization, ctx: TranspileCtx) -> CodexAuthorizationSection:
        section = CodexAuthorizationSection()
        if model.default_mode is not None:
            section.sandbox_mode = _DEFAULT_MODE_TO_CODEX[model.default_mode]
        section.sandbox_workspace_write.writable_roots = list(model.filesystem.allow_write)
        if model.reviewer is not None:
            section.approvals_reviewer = _REVIEWER_TO_CODEX[model.reviewer].value
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
        if section.approvals_reviewer is not None:
            try:
                upstream = ApprovalsReviewer(section.approvals_reviewer)
            except ValueError:
                ctx.warn(
                    LossWarning(
                        domain=Domains.AUTHORIZATION,
                        target=BUILTIN_CODEX,
                        message=(
                            f"approvals_reviewer {section.approvals_reviewer!r} is "
                            "not in the documented vocabulary (P1-G); dropping"
                        ),
                    )
                )
            else:
                auth.reviewer = _CODEX_TO_REVIEWER[upstream]
        return auth


__all__ = ["CodexAuthorizationCodec", "CodexAuthorizationSection"]
