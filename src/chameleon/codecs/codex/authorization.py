"""Codex codec for the authorization domain.

 S3 — LCD scheme. Maps the LCD axes Codex actually has on the
wire and emits typed ``LossWarning``s for the Claude-only axes.

Wire claims:

  sandbox_mode (3 LCD enum values)   ↔ sandbox_mode
    READ_ONLY        ↔ "read-only"
    WORKSPACE_WRITE  ↔ "workspace-write"
    FULL_ACCESS      ↔ "danger-full-access"
  approval_policy (4 LCD enum arms)  ↔ approval_policy
    UNTRUSTED        ↔ "untrusted"
    ON_FAILURE       ↔ "on-failure"
    ON_REQUEST       ↔ "on-request"
    NEVER            ↔ "never"
  filesystem.allow_write             ↔ [sandbox_workspace_write].writable_roots
  reviewer                    ↔ approvals_reviewer

LCD-discipline drops:

  permission_mode → not on the Codex wire. ``to_target`` emits a
    LossWarning naming the field; ``from_target`` never produces one
    (Codex has nothing to project from).

  approval_policy granular shape → ``AskForApproval4`` from upstream's
    discriminated union is a richer BaseModel, not a plain string.
    Operators authoring it route through
    ``targets.codex.items["permissions"]`` pass-through. ``from_target``
    sees the dict shape, emits a LossWarning, and leaves
    ``approval_policy`` unset on the neutral side; ``to_target`` only
    emits the 4 plain-enum arms.

Pattern lists (allow/ask/deny) and the network sub-block remain
LossWarning-on-encode; mapping them to Codex's
``[permissions.<name>]`` profiles is.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.codecs.codex._generated import ApprovalsReviewer
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.authorization import (
    ApprovalPolicy,
    Authorization,
    FilesystemPolicy,
    Reviewer,
    SandboxMode,
)

_SANDBOX_MODE_TO_CODEX: dict[SandboxMode, str] = {
    SandboxMode.READ_ONLY: "read-only",
    SandboxMode.WORKSPACE_WRITE: "workspace-write",
    SandboxMode.FULL_ACCESS: "danger-full-access",
}
_CODEX_TO_SANDBOX_MODE: dict[str, SandboxMode] = {
    "read-only": SandboxMode.READ_ONLY,
    "workspace-write": SandboxMode.WORKSPACE_WRITE,
    "danger-full-access": SandboxMode.FULL_ACCESS,
}

#  S3 — neutral ``ApprovalPolicy`` <-> Codex ``approval_policy``
# (the 4 plain-enum arms of upstream's ``AskForApproval`` discriminated
# RootModel union; the 5th arm — ``AskForApproval4`` granular — is a
# structured BaseModel and lives in pass-through). Wire values use
# hyphens for ``on-failure`` / ``on-request``; the neutral enum's
# python-friendly underscores never reach the wire.
_APPROVAL_POLICY_TO_CODEX: dict[ApprovalPolicy, str] = {
    ApprovalPolicy.UNTRUSTED: "untrusted",
    ApprovalPolicy.ON_FAILURE: "on-failure",
    ApprovalPolicy.ON_REQUEST: "on-request",
    ApprovalPolicy.NEVER: "never",
}
_CODEX_TO_APPROVAL_POLICY: dict[str, ApprovalPolicy] = {
    "untrusted": ApprovalPolicy.UNTRUSTED,
    "on-failure": ApprovalPolicy.ON_FAILURE,
    "on-request": ApprovalPolicy.ON_REQUEST,
    "never": ApprovalPolicy.NEVER,
}

# neutral ``authorization.reviewer`` <-> Codex ``approvals_reviewer``.
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
    model_config = ConfigDict(extra="allow")
    sandbox_mode: str | None = None
    sandbox_workspace_write: _CodexSandboxWorkspaceWrite = Field(
        default_factory=_CodexSandboxWorkspaceWrite
    )
    #  S3: typed loose to accommodate both the 4 plain-enum arms
    # of upstream ``AskForApproval`` (a wire string like ``"on-request"``)
    # AND the ``AskForApproval4`` granular arm (a TOML inline table that
    # decodes to a dict). The codec resolves the str case to
    # ``ApprovalPolicy`` and emits a LossWarning + drops on the dict case
    # — modelling the granular shape in neutral would require inventing
    # cross-target semantics Claude has no analogue for. Mirrors the
    # ``sandbox_mode`` and ``approvals_reviewer`` patterns above (typed
    # loose so unknown wire payloads land here and hit a typed
    # LossWarning rather than crashing inside Pydantic).
    approval_policy: str | dict[str, object] | None = None
    # stored as the raw wire string (not the upstream ``ApprovalsReviewer``
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
            FieldPath(segments=("approval_policy",)),
            FieldPath(segments=("approvals_reviewer",)),
        }
    )

    @staticmethod
    def to_target(model: Authorization, ctx: TranspileCtx) -> CodexAuthorizationSection:
        section = CodexAuthorizationSection()
        if model.sandbox_mode is not None:
            section.sandbox_mode = _SANDBOX_MODE_TO_CODEX[model.sandbox_mode]
        if model.approval_policy is not None:
            section.approval_policy = _APPROVAL_POLICY_TO_CODEX[model.approval_policy]
        if model.permission_mode is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.AUTHORIZATION,
                    target=BUILTIN_CODEX,
                    message=(
                        "Authorization.permission_mode is a Claude-only axis "
                        "(no Codex wire equivalent); dropping on Codex encode. "
                        "Set this field for Claude; Codex's coarse mode lives "
                        "in Authorization.sandbox_mode."
                    ),
                )
            )
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
                        "[permissions.<name>] profiles)"
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
                        "authorization.network mapping to Codex's named permission profiles is"
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
                        "Codex sandbox equivalents in V0"
                    ),
                )
            )
        return section

    @staticmethod
    def from_target(section: CodexAuthorizationSection, ctx: TranspileCtx) -> Authorization:
        auth = Authorization()
        if section.sandbox_mode is not None:
            mapped = _CODEX_TO_SANDBOX_MODE.get(section.sandbox_mode)
            if mapped is not None:
                auth.sandbox_mode = mapped
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.AUTHORIZATION,
                        target=BUILTIN_CODEX,
                        message=(
                            f"sandbox_mode {section.sandbox_mode!r} has no neutral equivalent in V0"
                        ),
                    )
                )
        if section.approval_policy is not None:
            if isinstance(section.approval_policy, str):
                policy = _CODEX_TO_APPROVAL_POLICY.get(section.approval_policy)
                if policy is not None:
                    auth.approval_policy = policy
                else:
                    ctx.warn(
                        LossWarning(
                            domain=Domains.AUTHORIZATION,
                            target=BUILTIN_CODEX,
                            message=(
                                f"approval_policy {section.approval_policy!r} is not "
                                "in the LCD vocabulary (untrusted/on-failure/"
                                "on-request/never); dropping"
                            ),
                        )
                    )
            else:
                # Dict shape — upstream's ``AskForApproval4`` granular arm.
                # No neutral equivalent under LCD discipline.
                ctx.warn(
                    LossWarning(
                        domain=Domains.AUTHORIZATION,
                        target=BUILTIN_CODEX,
                        message=(
                            "Codex approval_policy granular form has no neutral "
                            "ApprovalPolicy equivalent (LCD only models the 4 "
                            "plain-enum arms); routing structured shape to "
                            "targets.codex.items['permissions'].approval_policy"
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
                            "not in the documented vocabulary; dropping"
                        ),
                    )
                )
            else:
                auth.reviewer = _CODEX_TO_REVIEWER[upstream]
        return auth


__all__ = ["CodexAuthorizationCodec", "CodexAuthorizationSection"]
