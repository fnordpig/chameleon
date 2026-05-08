"""Defensive Claude assemble: drop plugins whose marketplace is unknown.

Claude reads ``enabledPlugins`` at startup and reconciles every key
against its marketplace cache (``~/.claude/plugins/known_marketplaces.json``
plus the compiled-in builtins). When a key references a marketplace
Claude has never cached AND the operator has not declared it in
``extraKnownMarketplaces``, Claude logs ``error: Plugin foo@bar is not
cached at (not recorded)`` and refuses to load that plugin.

Codex tolerates plugin-key marketplace references it has no
``[marketplaces.<name>]`` declaration for (they're inert until
resolved), so chameleon's cross-target unification carries those keys
to Claude. Pre-fix, those Codex-only references became Claude
``enabledPlugins`` entries that crash on read.

The fix: at Claude assemble time, only emit ``enabledPlugins[k]``
when the marketplace component (after ``@``) is either declared in
``model.plugin_marketplaces`` or is a Claude built-in marketplace
(``claude-plugins-official``, ``anthropic-agent-skills``). Dropped
plugin keys are surfaced via a ``LossWarning`` so the operator sees
which entries went away and can either declare the missing marketplace
or accept the drop.
"""

from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.schema.capabilities import (
    Capabilities,
    PluginEntry,
    PluginMarketplace,
    PluginMarketplaceSource,
)


def test_claude_assemble_drops_plugins_for_unknown_marketplace() -> None:
    """Plugins keyed against a marketplace that is neither in
    ``plugin_marketplaces`` nor a Claude built-in are dropped from
    ``enabledPlugins`` and surfaced as a ``LossWarning``."""

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

    # Declared marketplace → plugin survives.
    assert "ripvec@example-user-plugins" in section.enabled_plugins
    assert section.enabled_plugins["ripvec@example-user-plugins"] is True

    # Unknown marketplace → plugin dropped.
    assert "superpowers@openai-curated" not in section.enabled_plugins

    # Drop is surfaced via LossWarning, naming the dropped key + marketplace.
    drop_warnings = [
        w for w in ctx.warnings if w.target == BUILTIN_CLAUDE and "openai-curated" in w.message
    ]
    assert len(drop_warnings) >= 1, (
        f"expected a LossWarning naming openai-curated; got: {ctx.warnings!r}"
    )
    assert "superpowers@openai-curated" in drop_warnings[0].message


def test_claude_assemble_preserves_plugins_for_builtin_marketplaces() -> None:
    """Claude has compiled-in builtin marketplaces (``claude-plugins-official``
    and ``anthropic-agent-skills``). Plugins keyed against those survive
    even though they are not in ``plugin_marketplaces``."""

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

    # No drop warnings — both marketplaces are recognized builtins.
    drop_warnings = [
        w
        for w in ctx.warnings
        if w.target == BUILTIN_CLAUDE
        and ("claude-plugins-official" in w.message or "anthropic-agent-skills" in w.message)
    ]
    assert not drop_warnings, f"unexpected drop warnings: {drop_warnings!r}"


def test_claude_assemble_drops_plugin_with_malformed_key() -> None:
    """A plugin key without ``@<marketplace>`` has no resolvable
    marketplace; chameleon drops it (and Claude would crash on it
    anyway)."""

    model = Capabilities(
        plugins={"orphan-no-marketplace": PluginEntry(enabled=True)},
    )
    ctx = TranspileCtx()
    section = ClaudeCapabilitiesCodec.to_target(model, ctx)
    assert "orphan-no-marketplace" not in section.enabled_plugins
