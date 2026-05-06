"""Claude codec for capabilities — V0 ships mcp_servers only.

Claude's MCP server definitions live in two files (per the design spec
§10.2): `~/.claude.json` (user-level mcpServers map) and `.mcp.json`
(project-level). For V0 we serialize the operator's neutral
`capabilities.mcp_servers` mapping into the user-level location; the
assembler is what splits across files.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.capabilities import (
    Capabilities,
    McpServer,
    McpServerStdio,
    McpServerStreamableHttp,
)


class _ClaudeMcpServerStdio(BaseModel):
    """Claude's user-level mcpServers entry shape (stdio variant).

    The `type` field is the on-disk discriminator real `~/.claude.json`
    entries carry; without it modelled here, `extra="forbid"` would
    reject every modern Claude config (parity-gap.md P0-1).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class _ClaudeMcpServerHttp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["http"] = "http"
    url: str
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] = Field(default_factory=dict)


# Discriminated by the on-disk `type` tag. Without `Field(discriminator=...)`,
# pydantic tries each branch in turn and surfaces the union of all branch
# errors — the misleading multi-error output in parity-gap.md P0-1.
_ClaudeMcpServer = Annotated[
    _ClaudeMcpServerStdio | _ClaudeMcpServerHttp,
    Field(discriminator="type"),
]


class ClaudeCapabilitiesSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcpServers: dict[str, _ClaudeMcpServer] = Field(default_factory=dict)  # noqa: N815
    enabledMcpjsonServers: list[str] = Field(default_factory=list)  # noqa: N815
    disabledMcpjsonServers: list[str] = Field(default_factory=list)  # noqa: N815


class ClaudeCapabilitiesCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.CAPABILITIES
    target_section: ClassVar[type[BaseModel]] = ClaudeCapabilitiesSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("mcpServers",)),
            FieldPath(segments=("enabledMcpjsonServers",)),
            FieldPath(segments=("disabledMcpjsonServers",)),
        }
    )

    @staticmethod
    def to_target(model: Capabilities, ctx: TranspileCtx) -> ClaudeCapabilitiesSection:
        section = ClaudeCapabilitiesSection()
        for name, server in model.mcp_servers.items():
            if isinstance(server, McpServerStdio):
                section.mcpServers[name] = _ClaudeMcpServerStdio(
                    command=server.command,
                    args=list(server.args),
                    env=dict(server.env),
                )
            elif isinstance(server, McpServerStreamableHttp):
                section.mcpServers[name] = _ClaudeMcpServerHttp(
                    url=str(server.url),
                    bearer_token_env_var=server.bearer_token_env_var,
                    http_headers=dict(server.http_headers),
                )
        return section

    @staticmethod
    def from_target(section: ClaudeCapabilitiesSection, ctx: TranspileCtx) -> Capabilities:
        servers: dict[str, McpServer] = {}
        for name, raw in section.mcpServers.items():
            if isinstance(raw, _ClaudeMcpServerStdio):
                servers[name] = McpServerStdio(
                    command=raw.command,
                    args=list(raw.args),
                    env=dict(raw.env),
                )
            elif isinstance(raw, _ClaudeMcpServerHttp):
                # Use model_validate so Pydantic coerces the str URL into AnyHttpUrl;
                # direct keyword construction would fail static type checking.
                servers[name] = McpServerStreamableHttp.model_validate(
                    {
                        "url": raw.url,
                        "bearer_token_env_var": raw.bearer_token_env_var,
                        "http_headers": dict(raw.http_headers),
                    }
                )
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.CAPABILITIES,
                        target=BUILTIN_CLAUDE,
                        message=f"unknown mcpServers entry shape for {name!r}; dropping",
                        field_path=FieldPath(segments=("mcpServers",)),
                    )
                )
        return Capabilities(mcp_servers=servers)


__all__ = ["ClaudeCapabilitiesCodec", "ClaudeCapabilitiesSection"]
