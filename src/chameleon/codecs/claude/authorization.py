"""Claude codec for the authorization domain.

Wave-13 S2 — Claude consumes the LCD-aligned schema split:

  permission_mode                       ↔ permissions.defaultMode
                                          (Claude IS this axis — lossless)
  filesystem.{allow,deny}_{read,write}  ↔ sandbox.filesystem.{allow,deny}{Read,Write}
  network.{allowed,denied}_domains      ↔ sandbox.network.{allowed,denied}Domains
  network.allow_local_binding           ↔ sandbox.network.allowLocalBinding
  allow_patterns                        ↔ permissions.allow
  ask_patterns                          ↔ permissions.ask
  deny_patterns                         ↔ permissions.deny

LCD asymmetries (codec emits ``LossWarning`` on encode):

  * ``sandbox_mode`` is a Codex-only axis. Claude has no equivalent —
    the codec drops it on encode (no synthesis attempt) with a typed
    warning so the operator knows the field is Codex-side only.
  * ``approval_policy`` is a Codex-only axis. Same disposition.
  * ``reviewer`` is Codex-only (P1-G). Same disposition (existing).

LCD asymmetries (codec emits ``LossWarning`` on decode):

  * Claude's ``permissions.defaultMode`` has 7 wire values; the LCD
    ``PermissionMode`` enum only models 3 (``default``/``acceptEdits``/
    ``plan``). The other 4 (``auto``, ``dontAsk``, ``bypassPermissions``,
    ``delegate``) round-trip via the pass-through bag rather than enter
    neutral. The codec emits a typed warning when the wire carries one
    of those values; the value itself survives via the assembler's
    extras-merge mechanism (``permissions`` is a section with
    ``extra='allow'``).

The DSL stays where it lives: this codec does NOT translate Claude's
pattern allow-lists into Codex's structured permission profiles or vice
versa. Each target's pattern surface is target-native; cross-target
translation is explicitly out of scope for the LCD.
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
    PermissionMode,
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
    # ``extra='allow'`` covers the rest of Claude's wire ``sandbox`` keys
    # (``enabled``, ``ignoreViolations``, ``excludedCommands``,
    # ``autoAllowBashIfSandboxed``, ``enableWeakerNetworkIsolation``,
    # ``enableWeakerNestedSandbox``, ``allowUnsandboxedCommands``,
    # ``ripgrep``); they round-trip as section extras through the
    # assembler's existing-extras merge.
    model_config = ConfigDict(extra="allow")
    filesystem: _ClaudeSandboxFilesystem = Field(default_factory=_ClaudeSandboxFilesystem)
    network: _ClaudeSandboxNetwork = Field(default_factory=_ClaudeSandboxNetwork)


class ClaudeAuthorizationSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    permissions: _ClaudePermissions = Field(default_factory=_ClaudePermissions)
    sandbox: _ClaudeSandbox = Field(default_factory=_ClaudeSandbox)


# LCD permission_mode <-> wire defaultMode mapping. Wire uses camelCase
# (``acceptEdits``); neutral uses snake_case (``accept_edits``). The
# codec owns the rename so the schema's enum reads cleanly in YAML.
_PERMISSION_MODE_TO_WIRE: dict[PermissionMode, str] = {
    PermissionMode.DEFAULT: "default",
    PermissionMode.ACCEPT_EDITS: "acceptEdits",
    PermissionMode.PLAN: "plan",
}
_WIRE_TO_PERMISSION_MODE: dict[str, PermissionMode] = {
    wire: mode for mode, wire in _PERMISSION_MODE_TO_WIRE.items()
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
        if model.permission_mode is not None:
            section.permissions.defaultMode = _PERMISSION_MODE_TO_WIRE[model.permission_mode]
        if model.sandbox_mode is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.AUTHORIZATION,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "Authorization.sandbox_mode is a Codex-only axis (no Claude wire "
                        "equivalent); dropping on Claude encode. Set this field for Codex; "
                        "Claude's coarse mode lives in Authorization.permission_mode."
                    ),
                )
            )
        if model.approval_policy is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.AUTHORIZATION,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "Authorization.approval_policy is a Codex-only axis (no Claude wire "
                        "equivalent); dropping on Claude encode. Set this field for Codex; "
                        "Claude's per-prompt disposition lives in Authorization.permission_mode."
                    ),
                )
            )
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
            mapped = _WIRE_TO_PERMISSION_MODE.get(section.permissions.defaultMode)
            if mapped is not None:
                auth.permission_mode = mapped
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.AUTHORIZATION,
                        target=BUILTIN_CLAUDE,
                        message=(
                            f"Claude permissions.defaultMode "
                            f"{section.permissions.defaultMode!r} has no neutral "
                            "PermissionMode equivalent (LCD only models "
                            "default/acceptEdits/plan); routing to "
                            "targets.claude.items['permissions'].defaultMode"
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
