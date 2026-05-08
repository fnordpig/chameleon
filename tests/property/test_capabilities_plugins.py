"""Round-trip property tests for ``capabilities.plugins`` through both codecs.

P1-A acceptance: a single neutral ``capabilities.plugins`` dict survives
``to_target -> from_target`` on both Claude and Codex independently. The
cross-target unification proof lives in ``tests/integration``.
"""

from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.codecs.codex.capabilities import CodexCapabilitiesCodec
from chameleon.schema.capabilities import (
    Capabilities,
    PluginEntry,
    PluginMarketplace,
    PluginMarketplaceSource,
)


def test_claude_round_trip_plugins() -> None:
    """Round-trip works when every plugin's ``@<marketplace>`` resolves —
    either via a ``plugin_marketplaces`` declaration or a Claude built-in
    marketplace. Unresolvable keys are intentionally dropped at assemble
    time (see ``test_claude_plugin_marketplace_filter``)."""
    orig = Capabilities(
        plugins={
            "ripvec@example-user-plugins": PluginEntry(enabled=True),
            "github@claude-plugins-official": PluginEntry(enabled=False),
            "code-review@claude-plugins-official": PluginEntry(enabled=True),
        },
        plugin_marketplaces={
            "example-user-plugins": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="github",
                    repo="example-user/example-user-plugins",
                ),
            ),
        },
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(orig, ctx)
    restored = ClaudeCapabilitiesCodec.from_target(section, ctx)
    assert restored.plugins == orig.plugins
    assert restored.plugin_marketplaces == orig.plugin_marketplaces


def test_codex_round_trip_plugins() -> None:
    orig = Capabilities(
        plugins={
            "ripvec@example-user-plugins": PluginEntry(enabled=True),
            "github@claude-plugins-official": PluginEntry(enabled=False),
            "code-review@openai-curated": PluginEntry(enabled=True),
        }
    )
    ctx = TranspileCtx()
    section = CodexCapabilitiesCodec.to_target(orig, ctx)
    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    assert restored.plugins == orig.plugins


def test_claude_empty_plugins() -> None:
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(Capabilities(), ctx)
    restored = ClaudeCapabilitiesCodec.from_target(section, ctx)
    assert restored.plugins == {}


def test_codex_empty_plugins() -> None:
    ctx = TranspileCtx()
    section = CodexCapabilitiesCodec.to_target(Capabilities(), ctx)
    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    assert restored.plugins == {}
