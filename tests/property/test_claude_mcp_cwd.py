"""F-CWD regression — Claude MCP stdio carries ``cwd``.

The neutral schema's :class:`~chameleon.schema.capabilities.McpServerStdio`
exposes a first-class ``cwd: str | None`` field (working directory for the
spawned MCP stdio server).  Agent B's cross-target fuzzer pinned
the silent loss as F-CWD in
:mod:`tests.fuzz.test_cross_target_unification` — the Claude codec's
``_ClaudeMcpServerStdio`` did not model ``cwd``, so encoding through
Claude and decoding back yielded ``cwd=None`` for every input where
``cwd`` was set, with no ``LossWarning`` to mark the loss.

This file is the per-target round-trip regression: it exercises the
Claude codec's ``to_target``/``from_target`` cycle for ``McpServerStdio``
with ``cwd`` populated and asserts the value survives without a
:class:`~chameleon.codecs._protocol.LossWarning`.
"""

from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import (
    ClaudeCapabilitiesCodec,
    ClaudeCapabilitiesSection,
    _ClaudeMcpServerStdio,
)
from chameleon.schema.capabilities import Capabilities, McpServerStdio


def test_claude_mcp_stdio_cwd_round_trips() -> None:
    """``McpServerStdio.cwd`` survives Claude's to_target/from_target."""
    orig = Capabilities(
        mcp_servers={
            "memory": McpServerStdio(
                command="npx",
                args=["-y", "@modelcontextprotocol/server-memory"],
                env={"X": "1"},
                cwd="/srv/agents/memory",
            ),
        }
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(orig, ctx)

    # The on-disk shape must carry cwd verbatim.
    on_disk = section.mcpServers["memory"]
    assert isinstance(on_disk, _ClaudeMcpServerStdio)
    assert on_disk.cwd == "/srv/agents/memory"

    restored = ClaudeCapabilitiesCodec.from_target(section, ctx)
    server = restored.mcp_servers["memory"]
    assert isinstance(server, McpServerStdio)
    assert server.cwd == "/srv/agents/memory"
    # The other fields must still round-trip alongside cwd.
    assert server.command == "npx"
    assert server.args == ["-y", "@modelcontextprotocol/server-memory"]
    assert server.env == {"X": "1"}
    # No LossWarning — cwd is now a first-class field on the Claude side.
    assert list(ctx.warnings) == []


def test_claude_mcp_stdio_cwd_absent_round_trips() -> None:
    """The default ``cwd=None`` round-trips without surfacing a value."""
    orig = Capabilities(
        mcp_servers={
            "memory": McpServerStdio(command="npx", args=[], env={}),
        }
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(orig, ctx)

    on_disk = section.mcpServers["memory"]
    assert isinstance(on_disk, _ClaudeMcpServerStdio)
    assert on_disk.cwd is None

    restored = ClaudeCapabilitiesCodec.from_target(section, ctx)
    server = restored.mcp_servers["memory"]
    assert isinstance(server, McpServerStdio)
    assert server.cwd is None
    assert list(ctx.warnings) == []


def test_claude_mcp_stdio_cwd_decodes_from_on_disk() -> None:
    """A raw Claude on-disk entry with ``cwd`` decodes into the neutral."""
    section = ClaudeCapabilitiesSection.model_validate(
        {
            "mcpServers": {
                "memory": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-memory"],
                    "env": {"X": "1"},
                    "cwd": "/srv/agents/memory",
                }
            }
        }
    )
    ctx = TranspileCtx()
    restored = ClaudeCapabilitiesCodec.from_target(section, ctx)
    server = restored.mcp_servers["memory"]
    assert isinstance(server, McpServerStdio)
    assert server.cwd == "/srv/agents/memory"
    assert list(ctx.warnings) == []
