"""F-CWD regression — Codex MCP stdio carries ``cwd``.

The neutral schema's :class:`~chameleon.schema.capabilities.McpServerStdio`
exposes a first-class ``cwd: str | None`` field (working directory for the
spawned MCP stdio server).  Agent B's cross-target fuzzer pinned
the silent loss as F-CWD in
:mod:`tests.fuzz.test_cross_target_unification` — the Codex codec's
``_CodexMcpServerStdio`` did not model ``cwd``, so encoding through
Codex and decoding back yielded ``cwd=None`` for every input where
``cwd`` was set, with no ``LossWarning`` to mark the loss.

This file is the per-target round-trip regression: it exercises the
Codex codec's ``to_target``/``from_target`` cycle for ``McpServerStdio``
with ``cwd`` populated and asserts the value survives without a
:class:`~chameleon.codecs._protocol.LossWarning`. It mirrors
``tests/property/test_claude_mcp_cwd.py`` ( W11-1's Claude-side
companion regression).
"""

from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex.capabilities import (
    CodexCapabilitiesCodec,
    CodexCapabilitiesSection,
    _CodexMcpServerStdio,
)
from chameleon.schema.capabilities import Capabilities, McpServerStdio


def test_codex_mcp_stdio_cwd_round_trips() -> None:
    """``McpServerStdio.cwd`` survives Codex's to_target/from_target."""
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
    section = CodexCapabilitiesCodec.to_target(orig, ctx)

    # The on-disk shape must carry cwd verbatim.
    on_disk = section.mcp_servers["memory"]
    assert isinstance(on_disk, _CodexMcpServerStdio)
    assert on_disk.cwd == "/srv/agents/memory"

    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    server = restored.mcp_servers["memory"]
    assert isinstance(server, McpServerStdio)
    assert server.cwd == "/srv/agents/memory"
    # The other fields must still round-trip alongside cwd.
    assert server.command == "npx"
    assert server.args == ["-y", "@modelcontextprotocol/server-memory"]
    assert server.env == {"X": "1"}
    # No LossWarning — cwd is now a first-class field on the Codex side.
    assert list(ctx.warnings) == []


def test_codex_mcp_stdio_cwd_absent_round_trips() -> None:
    """The default ``cwd=None`` round-trips without surfacing a value."""
    orig = Capabilities(
        mcp_servers={
            "memory": McpServerStdio(command="npx", args=[], env={}),
        }
    )
    ctx = TranspileCtx()
    section = CodexCapabilitiesCodec.to_target(orig, ctx)

    on_disk = section.mcp_servers["memory"]
    assert isinstance(on_disk, _CodexMcpServerStdio)
    assert on_disk.cwd is None

    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    server = restored.mcp_servers["memory"]
    assert isinstance(server, McpServerStdio)
    assert server.cwd is None
    assert list(ctx.warnings) == []


def test_codex_mcp_stdio_cwd_decodes_from_on_disk() -> None:
    """A raw Codex on-disk entry with ``cwd`` decodes into the neutral.

    The Codex on-disk shape for an stdio MCP entry mirrors the upstream
    ``RawMcpServerConfig`` schema (see
    ``src/chameleon/codecs/codex/_generated.py``): ``command`` plus
    optional ``args`` / ``env`` / ``cwd``. The discriminated union here
    routes via the presence of ``command`` (vs. ``url``) — no explicit
    ``type`` tag.
    """
    section = CodexCapabilitiesSection.model_validate(
        {
            "mcp_servers": {
                "memory": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-memory"],
                    "env": {"X": "1"},
                    "cwd": "/srv/agents/memory",
                }
            }
        }
    )
    ctx = TranspileCtx()
    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    server = restored.mcp_servers["memory"]
    assert isinstance(server, McpServerStdio)
    assert server.cwd == "/srv/agents/memory"
    assert list(ctx.warnings) == []
