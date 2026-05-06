"""capabilities domain — what tools/skills/MCP/subagents are available."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Discriminator, Field, Tag


class McpServerStdio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class McpServerStreamableHttp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["http"] = "http"
    url: AnyHttpUrl
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] = Field(default_factory=dict)


def _mcp_server_discriminator(v: object) -> str:
    if isinstance(v, dict):
        if "url" in v:
            return "http"
        return "stdio"
    return getattr(v, "transport", "stdio")


McpServer = Annotated[
    Annotated[McpServerStdio, Tag("stdio")] | Annotated[McpServerStreamableHttp, Tag("http")],
    Discriminator(_mcp_server_discriminator),
]


class Capabilities(BaseModel):
    """What tools/MCP/skills/subagents the agent can use.

    V0 codecs cover `mcp_servers` only; remaining fields are typed schema
    with deferred codec implementation.
    """

    model_config = ConfigDict(extra="forbid")

    mcp_servers: dict[str, McpServer] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    subagents: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of subagent name to a config file path.",
    )
    web_search: Literal["cached", "live", "disabled"] | None = None


__all__ = [
    "Capabilities",
    "McpServer",
    "McpServerStdio",
    "McpServerStreamableHttp",
]
