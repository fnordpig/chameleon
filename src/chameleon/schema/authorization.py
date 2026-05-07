"""authorization domain — what the agent may do.

 S1 ships the LCD (lowest-common-denominator) schema half of the
P3 unification — see ``docs/superpowers/specs/2026-05-06-p3-authorization-design.md``.
S2 (Claude codec) and S3 (Codex codec) consume the schema in the
follow-up wave.

LCD principles encoded here:

* No translation between Claude's permission pattern language and
  Codex's named permission profiles. The DSL stays where it lives.
* Lossless on the small structurally-common subset:
    - global mode/policy (``sandbox_mode`` ≈ Codex, ``permission_mode``
      ≈ Claude)
    - approval policy (``approval_policy`` ≈ Codex's
      ``approval_policy``)
* Pass-through for everything richer via the existing
  ``targets.{claude,codex}.items["permissions"]`` machinery
  ( B1 +  F2).
* ``LossWarning`` on cross-target asymmetry so the operator sees
  authored fields that don't propagate.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SandboxMode(Enum):
    """Filesystem-level sandbox mode (Codex-aligned vocabulary).

    Wire alignment:
      * Codex ``SandboxMode`` enum
        (``codecs.codex._generated.SandboxMode``):
        ``read-only``, ``workspace-write``, ``danger-full-access``.
        Neutral's ``FULL_ACCESS`` projects to Codex's
        ``danger-full-access`` (the codec owns the rename).

    Renamed from ``DefaultMode`` in  S1 — the original name was
    always Codex-shaped (the values mirror Codex's wire enum). The new
    name is honest about which axis this represents.
    """

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"


class PermissionMode(Enum):
    """Pre-prompt disposition for tool calls (Claude-aligned subset).

    Wire alignment:
      * Claude ``PermissionMode`` StrEnum
        (``codecs.claude._generated.PermissionMode``) has 7 values:
        ``default``, ``acceptEdits``, ``plan``, ``auto``, ``dontAsk``,
        ``bypassPermissions``, ``delegate``.

     S1 LCD scope: this enum models only the 3 values with
    unambiguous cross-target meaning:

      * ``DEFAULT`` — prompt on first use.
      * ``ACCEPT_EDITS`` — auto-accept file edits.
      * ``PLAN`` — read-only planning mode.

    The other 4 Claude values (``auto``, ``dontAsk``,
    ``bypassPermissions``, ``delegate``) are Claude-specific and route
    through ``targets.claude.items["permissions"]`` pass-through rather
    than enter the neutral schema. ``delegate`` in particular is an
    experimental agent-team feature; ``bypassPermissions`` is dangerous
    enough that we want operators to author it explicitly in the
    target-namespaced bag.
    """

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"


class ApprovalPolicy(Enum):
    """Global approval policy (Codex-aligned subset).

    Wire alignment:
      * Codex ``AskForApproval`` discriminated RootModel union
        (``codecs.codex._generated.AskForApproval``) has 5 arms:
        ``AskForApproval1`` (``untrusted``), ``AskForApproval2``
        (``on-failure`` — DEPRECATED upstream but still wire-supported),
        ``AskForApproval3`` (``on-request``), ``AskForApproval4`` (the
        ``granular`` BaseModel structured shape), ``AskForApproval5``
        (``never``).

     S1 LCD scope: this enum models the 4 string-valued arms.

      * ``UNTRUSTED`` — only ``is_safe_command()``-approved reads
        auto-approve.
      * ``ON_FAILURE`` — Codex marks this DEPRECATED upstream
        (prefer ``ON_REQUEST`` for interactive, ``NEVER`` for
        non-interactive); kept here for round-trip fidelity since the
        wire still accepts it.
      * ``ON_REQUEST`` — model decides when to ask.
      * ``NEVER`` — never ask; failures bubble back to the model.

    The 5th arm (``AskForApproval4``, the ``granular`` structured
    config) is a richer shape, not an enum value. Operators authoring
    granular controls route through ``targets.codex.items["permissions"]``
    pass-through. Modelling it as a typed neutral field would require
    inventing cross-target semantics that Claude has no analogue for —
    out of scope for the LCD.

    Claude has no global approval policy in the same axis (its
    ``defaultMode`` is closer to ``permission_mode`` above). The Claude
    codec emits ``LossWarning`` when this field is set.
    """

    UNTRUSTED = "untrusted"
    ON_FAILURE = "on_failure"
    ON_REQUEST = "on_request"
    NEVER = "never"


class Reviewer(Enum):
    """Who reviews escalated approval requests.

    Codex-only at the field level today: Codex's ``approvals_reviewer``
    routes sandbox escapes, blocked network access, MCP approval prompts,
    and ARC escalations either to the user, to a freshly designed
    auto-review subagent, or to the legacy guardian subagent. Claude has
    no in-config equivalent — Claude codec emits a P1-G ``LossWarning``
    when this field is set.

    Vocabulary mirrors ``codecs.codex._generated.ApprovalsReviewer``;
    keep the value strings byte-for-byte aligned with the upstream enum
    so codec mappings stay schema-drift-checkable.
    """

    USER = "user"
    AUTO_REVIEW = "auto_review"
    GUARDIAN_SUBAGENT = "guardian_subagent"


class FilesystemPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_read: list[str] = Field(default_factory=list)
    allow_write: list[str] = Field(default_factory=list)
    deny_read: list[str] = Field(default_factory=list)
    deny_write: list[str] = Field(default_factory=list)


class NetworkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    allow_local_binding: bool | None = None
    allow_unix_sockets: list[str] = Field(default_factory=list)


class Authorization(BaseModel):
    """S1 LCD schema; codecs landing in S2 (Claude) and S3 (Codex)."""

    model_config = ConfigDict(extra="forbid")

    sandbox_mode: SandboxMode | None = None
    permission_mode: PermissionMode | None = None
    approval_policy: ApprovalPolicy | None = None
    filesystem: FilesystemPolicy = Field(default_factory=FilesystemPolicy)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    allow_patterns: list[str] = Field(default_factory=list)
    ask_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    reviewer: Reviewer | None = None


__all__ = [
    "ApprovalPolicy",
    "Authorization",
    "FilesystemPolicy",
    "NetworkPolicy",
    "PermissionMode",
    "Reviewer",
    "SandboxMode",
]
