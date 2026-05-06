"""B1 regression: nested sub-tables retain unclaimed sub-keys across round-trip.

When a codec partially claims a top-level table (e.g. ``[tui]`` in the
Codex config: ``theme`` and ``alternate_screen`` are claimed, but
``status_line`` and the ``[tui.model_availability_nux]`` sub-table are
not), the disassemble → assemble round-trip must preserve the unclaimed
sub-keys.

Pre-fix behaviour: the assembler produced a fresh ``[tui]`` containing
only the claimed keys, silently dropping ``status_line`` and
``model_availability_nux``. This is data loss the per-codec property
tests didn't catch because they exercise ``to_target`` /
``from_target`` over the modeled fields only.

Post-fix behaviour: the assembler's ``existing`` parameter is consulted
not just for partially-owned files (e.g. ``~/.claude.json``) but also
for nested sub-table preservation — every section model that wraps a
table-shaped sub-section now keeps unclaimed inner keys via
``ConfigDict(extra="allow")``, and the assembler re-projects them onto
the produced output.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex.capabilities import (
    CodexCapabilitiesCodec,
    CodexCapabilitiesSection,
)
from chameleon.codecs.codex.interface import CodexInterfaceCodec, CodexInterfaceSection
from chameleon.codecs.codex.lifecycle import CodexLifecycleCodec, CodexLifecycleSection
from chameleon.schema._constants import Domains
from chameleon.schema.capabilities import Capabilities
from chameleon.schema.interface import Interface
from chameleon.schema.lifecycle import Lifecycle
from chameleon.targets.codex.assembler import CodexAssembler


def _round_trip_codex(raw: bytes) -> bytes:
    """Disassemble + immediately re-assemble Codex bytes via the real
    codec stack — i.e. neutral does NOT carry the extras; they live on
    the target side only.
    """
    ctx = TranspileCtx()
    domains, passthrough = CodexAssembler.disassemble({CodexAssembler.CONFIG_TOML: raw}, ctx=ctx)

    # Re-derive each domain through its codec round-trip so we exercise
    # the full to_target → assemble pipeline (which is what the merge
    # engine does). This also guarantees the fresh section instances do
    # NOT carry the extras directly — the assembler must recover them
    # from ``existing``.
    per_domain: dict[Domains, object] = {}
    if Domains.INTERFACE in domains:
        section = domains[Domains.INTERFACE]
        assert isinstance(section, CodexInterfaceSection)
        neutral = CodexInterfaceCodec.from_target(section, ctx)
        assert isinstance(neutral, Interface)
        per_domain[Domains.INTERFACE] = CodexInterfaceCodec.to_target(neutral, ctx)
    if Domains.LIFECYCLE in domains:
        section = domains[Domains.LIFECYCLE]
        assert isinstance(section, CodexLifecycleSection)
        neutral_lc = CodexLifecycleCodec.from_target(section, ctx)
        assert isinstance(neutral_lc, Lifecycle)
        per_domain[Domains.LIFECYCLE] = CodexLifecycleCodec.to_target(neutral_lc, ctx)
    if Domains.CAPABILITIES in domains:
        section = domains[Domains.CAPABILITIES]
        assert isinstance(section, CodexCapabilitiesSection)
        neutral_caps = CodexCapabilitiesCodec.from_target(section, ctx)
        assert isinstance(neutral_caps, Capabilities)
        per_domain[Domains.CAPABILITIES] = CodexCapabilitiesCodec.to_target(neutral_caps, ctx)

    files = CodexAssembler.assemble(
        per_domain=cast("Mapping[Domains, BaseModel]", per_domain),
        passthrough=passthrough,
        existing={CodexAssembler.CONFIG_TOML: raw},
    )
    return files[CodexAssembler.CONFIG_TOML]


def test_codex_tui_subtable_extras_preserved() -> None:
    """``[tui]`` partially-claimed: ``status_line`` and
    ``[tui.model_availability_nux]`` must survive the round-trip even
    though only ``theme`` and ``alternate_screen`` are claimed by the
    interface codec.
    """
    raw = (
        b"[tui]\n"
        b'theme = "dark"\n'
        b'alternate_screen = "always"\n'
        b'status_line = ["model-with-reasoning", "current-dir"]\n'
        b"\n"
        b"[tui.model_availability_nux]\n"
        b'"gpt-5.5" = 4\n'
    )

    out = _round_trip_codex(raw).decode("utf-8")
    assert "[tui]" in out
    assert 'theme = "dark"' in out
    assert 'alternate_screen = "always"' in out
    # The unclaimed sub-keys must survive the round-trip.
    assert "status_line" in out, (
        "tui.status_line was dropped during the disassemble/assemble round-trip"
    )
    assert "model_availability_nux" in out, (
        "[tui.model_availability_nux] sub-table was dropped during round-trip"
    )
    assert '"gpt-5.5" = 4' in out


def test_codex_history_subtable_extras_preserved() -> None:
    """``[history]`` partially-claimed: ``persistence`` and ``max_bytes``
    are claimed; an unclaimed sub-key (e.g. an upstream-introduced
    ``cleanup_period_days``) must survive the round-trip.
    """
    raw = b'[history]\npersistence = "save-all"\nmax_bytes = 1048576\ncleanup_period_days = 30\n'

    out = _round_trip_codex(raw).decode("utf-8")
    assert "[history]" in out
    assert 'persistence = "save-all"' in out
    assert "max_bytes = 1048576" in out
    assert "cleanup_period_days = 30" in out, (
        "history.cleanup_period_days was dropped during round-trip"
    )


def test_codex_mcp_server_subtable_extras_preserved() -> None:
    """``[mcp_servers.<name>]`` partially-claimed: the modeled fields
    are ``command``/``args``/``env`` (stdio) plus ``enabled``; an
    upstream-extension field on a single mcp server entry must survive
    the round-trip.

    NB: ``_CodexMcpServerStdio`` was historically ``extra="forbid"``;
    a real-world Codex install can add fields like ``startup_timeout``
    that the codec should pass through rather than drop.
    """
    raw = (
        b"[mcp_servers.context7]\n"
        b'command = "uvx"\n'
        b'args = ["context7-mcp"]\n'
        b"startup_timeout_sec = 30\n"
    )

    out = _round_trip_codex(raw).decode("utf-8")
    assert "[mcp_servers.context7]" in out
    assert 'command = "uvx"' in out
    assert "startup_timeout_sec = 30" in out, (
        "mcp_servers.<name>.startup_timeout_sec was dropped during round-trip"
    )
