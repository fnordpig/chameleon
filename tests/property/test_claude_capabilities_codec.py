from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.schema.capabilities import Capabilities, McpServerStdio


def test_round_trip_stdio() -> None:
    orig = Capabilities(
        mcp_servers={
            "memory": McpServerStdio(
                command="npx",
                args=["-y", "@modelcontextprotocol/server-memory"],
                env={"X": "1"},
            ),
        }
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(orig, ctx)
    restored = ClaudeCapabilitiesCodec.from_target(section, ctx)
    assert "memory" in restored.mcp_servers
    server = restored.mcp_servers["memory"]
    assert isinstance(server, McpServerStdio)
    assert server.command == "npx"
    assert server.args == ["-y", "@modelcontextprotocol/server-memory"]
    assert server.env == {"X": "1"}


def test_round_trip_empty() -> None:
    orig = Capabilities()
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(orig, ctx)
    restored = ClaudeCapabilitiesCodec.from_target(section, ctx)
    assert restored.mcp_servers == {}
