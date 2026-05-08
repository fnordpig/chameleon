"""Round-trip property tests for ``capabilities.plugin_marketplaces``."""

from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import (
    ClaudeCapabilitiesCodec,
    _claude_marketplace_to_neutral,
    _ClaudeMarketplace,
    _ClaudeMarketplaceSourceGit,
)
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
    """``kind='git'`` round-trips byte-clean for non-github URLs.

    Note: github.com URLs are NOT used here because the disassemble path
    canonicalizes them to ``kind='github'`` (see
    ``test_claude_disassemble_canonicalizes_github_url_to_kind_github``).
    """
    orig = Capabilities(
        plugin_marketplaces={
            "example-org-marketplace": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="git",
                    # Custom SSH alias — NOT literal github.com; stays ``kind='git'``.
                    url="git@github-example-org:example-org/example-org-marketplace.git",
                ),
            ),
            "example-user-plugins": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="git",
                    url="https://gitlab.example.com/example-user/example-user-plugins.git",
                ),
            ),
        }
    )
    ctx = TranspileCtx()
    section = CodexCapabilitiesCodec.to_target(orig, ctx)
    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    assert restored.plugin_marketplaces == orig.plugin_marketplaces


def test_claude_disassemble_canonicalizes_github_url_to_kind_github() -> None:
    """A Claude entry ``{source: 'git', url: 'https://github.com/X/Y.git'}``
    canonicalizes to neutral ``kind='github', repo='X/Y'`` on disassemble.

    Claude's schema permits ``source: 'git'`` for github URLs (the user
    may have hand-authored their settings.json that way), but Claude's
    own native preferred form for github-hosted plugins is
    ``source: 'github'`` with structured ``repo: 'owner/name'``. Neutral
    holds the higher-detail form so the next assemble re-emits Claude
    in its native preferred shape.
    """
    mp = _ClaudeMarketplace(
        source=_ClaudeMarketplaceSourceGit(
            url="https://github.com/example-org/example.git",
            ref="main",
        ),
    )
    ctx = TranspileCtx()
    neutral = _claude_marketplace_to_neutral("example", mp, ctx)
    assert neutral is not None
    assert neutral.source.kind == "github"
    assert neutral.source.repo == "example-org/example"
    assert neutral.source.url is None
    assert neutral.source.ref == "main"


def test_claude_disassemble_non_github_git_url_stays_kind_git() -> None:
    """Non-github git URLs are NOT promoted — they have no canonical
    GitHub repo shape, so ``kind='git'`` remains."""

    mp = _ClaudeMarketplace(
        source=_ClaudeMarketplaceSourceGit(
            url="https://gitlab.example.com/example-org/example.git",
        ),
    )
    ctx = TranspileCtx()
    neutral = _claude_marketplace_to_neutral("example", mp, ctx)
    assert neutral is not None
    assert neutral.source.kind == "git"
    assert neutral.source.url == "https://gitlab.example.com/example-org/example.git"


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
