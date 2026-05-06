"""Regression tests for P0-1 (parity-gap.md): Claude MCP `type` discriminator.

Real `~/.claude.json` mcpServers entries carry a `"type": "stdio"` (or
`"http"`) discriminator field. Before the fix, `_ClaudeMcpServerStdio`
and `_ClaudeMcpServerHttp` used `extra="forbid"` and didn't model
`type`, so loading the exemplar fixture crashed with a misleading
multi-branch ValidationError. The fix:

  * adds `type: Literal["stdio"|"http"]` on each member model
  * wraps the union in `Annotated[..., Field(discriminator="type")]` so
    pydantic dispatches by tag instead of try-each-branch
  * writes `type` on serialization so round-trip is preserved

These tests pin all three behaviors. The exemplar-disassembly test is
the canonical regression: it directly drives the codec against the real
fixture's `mcpServers` shape (the bug repro from the parity-gap doc).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import (
    ClaudeCapabilitiesCodec,
    ClaudeCapabilitiesSection,
)
from chameleon.schema.capabilities import (
    Capabilities,
    McpServerStdio,
    McpServerStreamableHttp,
)

_EXEMPLAR_DOTCLAUDE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "exemplar" / "home" / "_claude.json"
)


def _exemplar_mcp_servers() -> dict[str, object]:
    raw = json.loads(_EXEMPLAR_DOTCLAUDE.read_text())
    servers = raw["mcpServers"]
    assert isinstance(servers, dict)
    return servers


def test_disassemble_exemplar_textual_mcp_is_stdio() -> None:
    """The fixture's `Textual-MCP` entry has `type: "stdio"` plus
    `command`/`args`/`env`. Validation must succeed and produce the
    stdio-typed neutral model."""
    servers = _exemplar_mcp_servers()
    section = ClaudeCapabilitiesSection.model_validate({"mcpServers": servers})
    restored = ClaudeCapabilitiesCodec.from_target(section, TranspileCtx())

    assert "Textual-MCP" in restored.mcp_servers
    server = restored.mcp_servers["Textual-MCP"]
    assert isinstance(server, McpServerStdio)
    assert server.command == "uv"
    assert server.args[0] == "run"
    assert server.env == {"GITHUB_TOKEN": "REDACTED_GITHUB_PAT"}


def test_round_trip_preserves_type_field_stdio() -> None:
    """to_target(from_target(x)).model_dump() must include `type: "stdio"`,
    so that re-serializing a Claude config produces the discriminator
    that real Claude expects."""
    capabilities = Capabilities(
        mcp_servers={
            "memory": McpServerStdio(
                command="npx",
                args=["-y", "@modelcontextprotocol/server-memory"],
                env={"FOO": "bar"},
            )
        }
    )
    section = ClaudeCapabilitiesCodec.to_target(capabilities, TranspileCtx())
    dumped = section.model_dump()
    assert dumped["mcpServers"]["memory"]["type"] == "stdio"
    assert dumped["mcpServers"]["memory"]["command"] == "npx"


def test_round_trip_preserves_type_field_http() -> None:
    """HTTP variant is missing from the fixture (only stdio present), so
    construct one. The discriminator must round-trip and resolve to the
    HTTP-typed neutral model."""
    raw = {
        "mcpServers": {
            "remote": {
                "type": "http",
                "url": "https://mcp.example.com/sse",
                "bearer_token_env_var": "MCP_TOKEN",
                "http_headers": {"X-Trace": "1"},
            }
        }
    }
    section = ClaudeCapabilitiesSection.model_validate(raw)
    restored = ClaudeCapabilitiesCodec.from_target(section, TranspileCtx())

    server = restored.mcp_servers["remote"]
    assert isinstance(server, McpServerStreamableHttp)
    assert str(server.url).startswith("https://mcp.example.com")
    assert server.bearer_token_env_var == "MCP_TOKEN"

    # And: round-trip back out preserves `type: "http"`.
    section2 = ClaudeCapabilitiesCodec.to_target(restored, TranspileCtx())
    dumped = section2.model_dump()
    assert dumped["mcpServers"]["remote"]["type"] == "http"
    assert dumped["mcpServers"]["remote"]["url"] == "https://mcp.example.com/sse"


def test_discriminator_dispatches_by_type_not_brute_force_union() -> None:
    """An entry with `type: "http"` but missing `url` must produce a
    SINGLE-branch error pointing at the http variant — not the misleading
    six-error multi-branch noise the original bug report shows. This is
    what `Annotated[..., Field(discriminator="type")]` buys us."""
    raw = {
        "mcpServers": {
            "broken": {
                "type": "http",
                # `url` intentionally absent
            }
        }
    }
    with pytest.raises(ValidationError) as exc_info:
        ClaudeCapabilitiesSection.model_validate(raw)

    errors = exc_info.value.errors()
    # Pydantic discriminated unions report errors only for the matched
    # branch. We should see the missing-url error, and we should NOT see
    # any error claiming `type` is an extra/forbidden field on the stdio
    # branch (which is the regression signature).
    assert any(e["type"] == "missing" and "url" in e["loc"] for e in errors)
    assert not any(e["type"] == "extra_forbidden" for e in errors)
