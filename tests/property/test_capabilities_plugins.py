"""Round-trip property tests for ``capabilities.plugins`` through both codecs.

P1-A acceptance: a single neutral ``capabilities.plugins`` dict survives
``to_target -> from_target`` on both Claude and Codex independently. The
cross-target unification proof lives in ``tests/integration``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.codecs.codex.capabilities import CodexCapabilitiesCodec
from chameleon.schema.capabilities import (
    Capabilities,
    PluginEntry,
    PluginMarketplace,
    PluginMarketplaceSource,
)


def _write_installed_plugins_cache(
    monkeypatch: pytest.MonkeyPatch, path: Path, plugin_keys: set[str]
) -> None:
    path.write_text(
        json.dumps({key: {} for key in sorted(plugin_keys)}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "chameleon.codecs.claude.capabilities._CLAUDE_INSTALLED_PLUGINS_PATH",
        path,
    )


def test_claude_round_trip_plugins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Round-trip works when every plugin's ``@<marketplace>`` resolves.

    We pin the installed-plugin cache for the test because the codec drops
    uncached non-builtin plugins to avoid runtime startup errors.
    """
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
    _write_installed_plugins_cache(
        monkeypatch, tmp_path / "installed_plugins.json", set(orig.plugins)
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
