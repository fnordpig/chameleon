"""Defensive Claude assemble: drop plugins whose marketplace is unknown.

Claude reads ``enabledPlugins`` at startup and reconciles every key
against its marketplace cache (``~/.claude/plugins/known_marketplaces.json``
plus the compiled-in builtins). When a key references a marketplace
that Claude has not cached and the operator has not declared it in
``extraKnownMarketplaces``, Claude logs ``error: Plugin foo@bar is not
cached at (not recorded)`` and refuses to load that plugin.

Codex tolerates plugin-key marketplace references it has no
``[marketplaces.<name>]`` declaration for (they're inert until resolved),
so chameleon's cross-target unification can carry those keys to Claude.
Pre-fix, those Codex-only references became Claude ``enabledPlugins``
entries that fail on read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import (
    ClaudeCapabilitiesCodec,
    ClaudeCapabilitiesSection,
)
from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.schema.capabilities import (
    Capabilities,
    PluginEntry,
    PluginMarketplace,
    PluginMarketplaceSource,
)


def _patch_missing_claude_installed_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "chameleon.codecs.claude.capabilities._CLAUDE_INSTALLED_PLUGINS_PATH",
        tmp_path / "installed_plugins.json",
    )


def test_claude_assemble_drops_plugins_for_unknown_marketplace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plugins with unknown marketplaces are dropped from ``enabledPlugins``."""
    _patch_missing_claude_installed_cache(monkeypatch, tmp_path)

    model = Capabilities(
        plugins={
            "ripvec@example-user-plugins": PluginEntry(enabled=True),
            "superpowers@openai-curated": PluginEntry(enabled=True),
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
    section = ClaudeCapabilitiesCodec.to_target(model, ctx)

    assert "ripvec@example-user-plugins" in section.enabled_plugins
    assert "superpowers@openai-curated" not in section.enabled_plugins

    drop_warnings = [
        w for w in ctx.warnings if w.target == BUILTIN_CLAUDE and "openai-curated" in w.message
    ]
    assert drop_warnings
    assert "superpowers@openai-curated" in drop_warnings[0].message


def test_claude_assemble_preserves_plugins_for_builtin_marketplaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Builtin marketplaces survive even without cache entries."""
    _patch_missing_claude_installed_cache(monkeypatch, tmp_path)

    model = Capabilities(
        plugins={
            "code-review@claude-plugins-official": PluginEntry(enabled=True),
            "document-skills@anthropic-agent-skills": PluginEntry(enabled=True),
        },
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(model, ctx)

    assert "code-review@claude-plugins-official" in section.enabled_plugins
    assert "document-skills@anthropic-agent-skills" in section.enabled_plugins
    assert not ctx.warnings


def test_claude_assemble_drops_plugin_with_malformed_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plugin key with no marketplace is always dropped."""
    _patch_missing_claude_installed_cache(monkeypatch, tmp_path)

    model = Capabilities(
        plugins={"orphan-no-marketplace": PluginEntry(enabled=True)},
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(model, ctx)
    assert "orphan-no-marketplace" not in section.enabled_plugins


def test_claude_disassemble_drops_plugins_with_unknown_marketplace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unknown marketplaces are dropped when parsing Claude settings."""
    _patch_missing_claude_installed_cache(monkeypatch, tmp_path)

    section = ClaudeCapabilitiesSection.model_validate(
        {
            "enabledPlugins": {
                "archiuvium-plugin-creator@my-claude-plugins": False,
                "code-review@claude-plugins-official": True,
            }
        }
    )
    ctx = TranspileCtx()
    model = ClaudeCapabilitiesCodec.from_target(section, ctx)

    assert "code-review@claude-plugins-official" in model.plugins
    assert "archiuvium-plugin-creator@my-claude-plugins" not in model.plugins

    drop_warnings = [
        w
        for w in ctx.warnings
        if w.target == BUILTIN_CLAUDE and "archiuvium-plugin-creator" in w.message
    ]
    assert drop_warnings


def test_claude_assemble_drops_plugins_not_cached_in_local_claude_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_path = tmp_path / "installed_plugins.json"
    cache_path.write_text(json.dumps({"code-review@openai-curated": []}), encoding="utf-8")
    monkeypatch.setattr(
        "chameleon.codecs.claude.capabilities._CLAUDE_INSTALLED_PLUGINS_PATH",
        cache_path,
    )

    model = Capabilities(
        plugins={
            "code-review@openai-curated": PluginEntry(enabled=True),
            "bash-lsp@zircote-lsp": PluginEntry(enabled=True),
        },
        plugin_marketplaces={
            "openai-curated": PluginMarketplace(
                source=PluginMarketplaceSource(kind="github", repo="openai-curated/repo"),
            ),
            "zircote-lsp": PluginMarketplace(
                source=PluginMarketplaceSource(kind="github", repo="zircote/lsp-marketplace"),
            ),
        },
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(model, ctx)

    assert "code-review@openai-curated" in section.enabled_plugins
    assert "bash-lsp@zircote-lsp" not in section.enabled_plugins

    assert any(
        w.target == BUILTIN_CLAUDE and "bash-lsp@zircote-lsp" in w.message for w in ctx.warnings
    )


def test_claude_disassemble_drops_plugins_not_cached_in_local_claude_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_path = tmp_path / "installed_plugins.json"
    cache_path.write_text(json.dumps({"code-review@openai-curated": []}), encoding="utf-8")
    monkeypatch.setattr(
        "chameleon.codecs.claude.capabilities._CLAUDE_INSTALLED_PLUGINS_PATH",
        cache_path,
    )
    section = ClaudeCapabilitiesSection.model_validate(
        {
            "extraKnownMarketplaces": {
                "openai-curated": {
                    "source": {"source": "github", "repo": "openai-curated/repo"},
                },
                "zircote-lsp": {
                    "source": {"source": "github", "repo": "zircote/lsp-marketplace"},
                },
            },
            "enabledPlugins": {
                "code-review@openai-curated": True,
                "bash-lsp@zircote-lsp": True,
            },
        }
    )
    ctx = TranspileCtx()
    model = ClaudeCapabilitiesCodec.from_target(section, ctx)

    assert "code-review@openai-curated" in model.plugins
    assert "bash-lsp@zircote-lsp" not in model.plugins
    assert any(
        w.target == BUILTIN_CLAUDE and "bash-lsp@zircote-lsp" in w.message for w in ctx.warnings
    )
