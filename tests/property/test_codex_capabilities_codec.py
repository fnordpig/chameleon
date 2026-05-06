from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex.capabilities import CodexCapabilitiesCodec
from chameleon.schema.capabilities import Capabilities, McpServerStdio


def test_round_trip_stdio() -> None:
    orig = Capabilities(
        mcp_servers={"docs": McpServerStdio(command="docs-server", args=["--port", "4000"])}
    )
    ctx = TranspileCtx()
    section = CodexCapabilitiesCodec.to_target(orig, ctx)
    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    assert "docs" in restored.mcp_servers
    s = restored.mcp_servers["docs"]
    assert isinstance(s, McpServerStdio)
    assert s.command == "docs-server"
