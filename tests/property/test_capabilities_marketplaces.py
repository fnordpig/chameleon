"""Round-trip property tests for ``capabilities.plugin_marketplaces``."""

from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.codecs.codex.capabilities import CodexCapabilitiesCodec
from chameleon.schema.capabilities import (
    Capabilities,
    PluginMarketplace,
    PluginMarketplaceSource,
)


def test_claude_round_trip_github_marketplace() -> None:
    orig = Capabilities(
        plugin_marketplaces={
            "example-user-plugins": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="github",
                    repo="example-user/example-user-plugins",
                ),
                auto_update=True,
            ),
            "astral-sh": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="github",
                    repo="astral-sh/claude-code-plugins",
                ),
            ),
        }
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(orig, ctx)
    restored = ClaudeCapabilitiesCodec.from_target(section, ctx)
    assert restored.plugin_marketplaces == orig.plugin_marketplaces


def test_codex_round_trip_git_marketplace() -> None:
    orig = Capabilities(
        plugin_marketplaces={
            "example-org-marketplace": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="git",
                    url="git@github-example-org:example-org/example-org-marketplace.git",
                ),
            ),
            "example-user-plugins": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="git",
                    url="https://github.com/example-user/example-user-plugins.git",
                ),
            ),
        }
    )
    ctx = TranspileCtx()
    section = CodexCapabilitiesCodec.to_target(orig, ctx)
    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    assert restored.plugin_marketplaces == orig.plugin_marketplaces


def test_codex_local_marketplace_round_trip() -> None:
    orig = Capabilities(
        plugin_marketplaces={
            "vendored": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="local",
                    path="/srv/vendored-marketplace",
                ),
            ),
        }
    )
    ctx = TranspileCtx()
    section = CodexCapabilitiesCodec.to_target(orig, ctx)
    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    assert restored.plugin_marketplaces == orig.plugin_marketplaces
