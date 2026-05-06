from __future__ import annotations

from pydantic import AnyHttpUrl

from chameleon.schema.capabilities import (
    Capabilities,
    McpServerStdio,
    McpServerStreamableHttp,
)


def test_mcp_server_stdio() -> None:
    s = McpServerStdio(command="npx", args=["-y", "@x/y"])
    assert s.command == "npx"
    assert s.args == ["-y", "@x/y"]


def test_mcp_server_http() -> None:
    s = McpServerStreamableHttp(url=AnyHttpUrl("https://x/mcp"), bearer_token_env_var="X_TOKEN")
    assert str(s.url).startswith("https://")
    assert s.bearer_token_env_var == "X_TOKEN"


def test_capabilities_with_mcp_servers() -> None:
    c = Capabilities(mcp_servers={"memory": McpServerStdio(command="npx", args=["-y", "memory"])})
    assert "memory" in c.mcp_servers
