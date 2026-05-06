"""authorization domain — what the agent may do.

V0 has typed schema only; codecs raise NotImplementedError under
xfail tests. The full unification of Claude's permissions.allow/ask/deny
pattern language with Codex's structured [permissions.<name>] is the
subject of a follow-on spec (§15.1).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DefaultMode(Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"


class Reviewer(Enum):
    """Who reviews escalated approval requests (P1-G).

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
    """V0: typed schema only; codecs deferred to follow-on spec (§15.1)."""

    model_config = ConfigDict(extra="forbid")

    default_mode: DefaultMode | None = None
    filesystem: FilesystemPolicy = Field(default_factory=FilesystemPolicy)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    allow_patterns: list[str] = Field(default_factory=list)
    ask_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    reviewer: Reviewer | None = None


__all__ = ["Authorization", "DefaultMode", "FilesystemPolicy", "NetworkPolicy", "Reviewer"]
