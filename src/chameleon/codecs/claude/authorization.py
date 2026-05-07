"""Claude codec for the authorization domain.

V0 thin slice (Wave-13 S1 schema rename — codec body is unchanged
mechanically because we only renamed the type, not its values; S2 will
rewrite this codec to consume the LCD schema's new ``permission_mode``
and ``approval_policy`` fields):

  sandbox_mode                          ↔ permissions.defaultMode
                                          + sandbox.enabled (full-access → False)
  filesystem.{allow,deny}_{read,write}  ↔ sandbox.filesystem.{allow,deny}{Read,Write}
  network.{allowed,denied}_domains      ↔ sandbox.network.{allowed,denied}Domains
  network.allow_local_binding           ↔ sandbox.network.allowLocalBinding
  allow_patterns                        ↔ permissions.allow
  ask_patterns                          ↔ permissions.ask
  deny_patterns                         ↔ permissions.deny

Mapping rationale (and known asymmetries):
  - neutral.sandbox_mode "read-only"      → defaultMode "default"      + sandbox.enabled True
  - neutral.sandbox_mode "workspace-write"→ defaultMode "acceptEdits"  + sandbox.enabled True
  - neutral.sandbox_mode "full-access"    → defaultMode "bypassPermissions" + sandbox.enabled False
  Reverse uses the same table; unrecognized values warn and drop.

This is the V0 codec for §15.1 — the full design (granular approval
policy, additional_directories, named permission profiles) lands in
the authorization spec.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.authorization import (
    Authorization,
    FilesystemPolicy,
    NetworkPolicy,
    SandboxMode,
)


class _ClaudePermissions(BaseModel):
    model_config = ConfigDict(extra="allow")
    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    defaultMode: str | None = None  # noqa: N815


class _ClaudeSandboxFilesystem(BaseModel):
    model_config = ConfigDict(extra="allow")
    allowRead: list[str] = Field(default_factory=list)  # noqa: N815
    allowWrite: list[str] = Field(default_factory=list)  # noqa: N815
    denyRead: list[str] = Field(default_factory=list)  # noqa: N815
    denyWrite: list[str] = Field(default_factory=list)  # noqa: N815


class _ClaudeSandboxNetwork(BaseModel):
    model_config = ConfigDict(extra="allow")
    allowedDomains: list[str] = Field(default_factory=list)  # noqa: N815
    deniedDomains: list[str] = Field(default_factory=list)  # noqa: N815
    allowLocalBinding: bool | None = None  # noqa: N815


class _ClaudeSandbox(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool | None = None
    filesystem: _ClaudeSandboxFilesystem = Field(default_factory=_ClaudeSandboxFilesystem)
    network: _ClaudeSandboxNetwork = Field(default_factory=_ClaudeSandboxNetwork)


class ClaudeAuthorizationSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    permissions: _ClaudePermissions = Field(default_factory=_ClaudePermissions)
    sandbox: _ClaudeSandbox = Field(default_factory=_ClaudeSandbox)


_SANDBOX_MODE_TO_CLAUDE: dict[SandboxMode, tuple[str, bool]] = {
    SandboxMode.READ_ONLY: ("default", True),
    SandboxMode.WORKSPACE_WRITE: ("acceptEdits", True),
    SandboxMode.FULL_ACCESS: ("bypassPermissions", False),
}
_CLAUDE_TO_SANDBOX_MODE: dict[str, SandboxMode] = {
    "default": SandboxMode.READ_ONLY,
    "acceptEdits": SandboxMode.WORKSPACE_WRITE,
    "bypassPermissions": SandboxMode.FULL_ACCESS,
}


class ClaudeAuthorizationCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.AUTHORIZATION
    target_section: ClassVar[type[BaseModel]] = ClaudeAuthorizationSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("permissions", "allow")),
            FieldPath(segments=("permissions", "ask")),
            FieldPath(segments=("permissions", "deny")),
            FieldPath(segments=("permissions", "defaultMode")),
            FieldPath(segments=("sandbox", "enabled")),
            FieldPath(segments=("sandbox", "filesystem", "allowRead")),
            FieldPath(segments=("sandbox", "filesystem", "allowWrite")),
            FieldPath(segments=("sandbox", "filesystem", "denyRead")),
            FieldPath(segments=("sandbox", "filesystem", "denyWrite")),
            FieldPath(segments=("sandbox", "network", "allowedDomains")),
            FieldPath(segments=("sandbox", "network", "deniedDomains")),
            FieldPath(segments=("sandbox", "network", "allowLocalBinding")),
        }
    )

    @staticmethod
    def to_target(model: Authorization, ctx: TranspileCtx) -> ClaudeAuthorizationSection:
        section = ClaudeAuthorizationSection()
        if model.sandbox_mode is not None:
            mode, sandbox_enabled = _SANDBOX_MODE_TO_CLAUDE[model.sandbox_mode]
            section.permissions.defaultMode = mode
            section.sandbox.enabled = sandbox_enabled
        section.permissions.allow = list(model.allow_patterns)
        section.permissions.ask = list(model.ask_patterns)
        section.permissions.deny = list(model.deny_patterns)
        section.sandbox.filesystem.allowRead = list(model.filesystem.allow_read)
        section.sandbox.filesystem.allowWrite = list(model.filesystem.allow_write)
        section.sandbox.filesystem.denyRead = list(model.filesystem.deny_read)
        section.sandbox.filesystem.denyWrite = list(model.filesystem.deny_write)
        section.sandbox.network.allowedDomains = list(model.network.allowed_domains)
        section.sandbox.network.deniedDomains = list(model.network.denied_domains)
        if model.network.allow_local_binding is not None:
            section.sandbox.network.allowLocalBinding = model.network.allow_local_binding
        if model.network.allow_unix_sockets:
            ctx.warn(
                LossWarning(
                    domain=Domains.AUTHORIZATION,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "network.allow_unix_sockets — handled via "
                        "sandbox.network.allowUnixSockets in Claude; deferred"
                    ),
                )
            )
        if model.reviewer is not None:
            # P1-G — Codex-only field at V0. Claude has no in-config equivalent
            # (Claude routes approvals through the runtime UI, not config).
            # The richer authorization unification (Claude pattern allow-lists
            # ↔ Codex named profiles) is P3, not this gap.
            ctx.warn(
                LossWarning(
                    domain=Domains.AUTHORIZATION,
                    target=BUILTIN_CLAUDE,
                    message=(
                        f"authorization.reviewer={model.reviewer.value!r} has no "
                        "Claude equivalent (P1-G); the field is Codex-only — "
                        "Claude routes approvals via the runtime UI"
                    ),
                )
            )
        return section

    @staticmethod
    def from_target(section: ClaudeAuthorizationSection, ctx: TranspileCtx) -> Authorization:
        auth = Authorization()
        if section.permissions.defaultMode is not None:
            mapped = _CLAUDE_TO_SANDBOX_MODE.get(section.permissions.defaultMode)
            if mapped is not None:
                auth.sandbox_mode = mapped
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.AUTHORIZATION,
                        target=BUILTIN_CLAUDE,
                        message=(
                            f"permissions.defaultMode "
                            f"{section.permissions.defaultMode!r} has no neutral "
                            f"equivalent in V0 (§15.1)"
                        ),
                    )
                )
        auth.allow_patterns = list(section.permissions.allow)
        auth.ask_patterns = list(section.permissions.ask)
        auth.deny_patterns = list(section.permissions.deny)
        auth.filesystem = FilesystemPolicy(
            allow_read=list(section.sandbox.filesystem.allowRead),
            allow_write=list(section.sandbox.filesystem.allowWrite),
            deny_read=list(section.sandbox.filesystem.denyRead),
            deny_write=list(section.sandbox.filesystem.denyWrite),
        )
        auth.network = NetworkPolicy(
            allowed_domains=list(section.sandbox.network.allowedDomains),
            denied_domains=list(section.sandbox.network.deniedDomains),
            allow_local_binding=section.sandbox.network.allowLocalBinding,
        )
        return auth


__all__ = ["ClaudeAuthorizationCodec", "ClaudeAuthorizationSection"]
