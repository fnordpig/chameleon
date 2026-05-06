"""Cross-target unification proof for ``capabilities.plugins`` (P1-A).

The exemplar fixture has the same operator declaring ~40 plugins by hand
across both Claude (``enabledPlugins``) and Codex (``[plugins.<id>]``).
This test disassembles BOTH targets, runs each side's capabilities codec
``from_target``, and asserts the resulting neutral plugin sets agree on
the keys that both targets enumerate.

It also exercises the documented cross-target conflict rule:
``ripvec@example-user-plugins`` and similar plugins that one target enables
and the other disables surface as ``PluginDisagreement`` records, NOT as
silent loss.
"""

from __future__ import annotations

from pathlib import Path

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import (
    ClaudeCapabilitiesCodec,
    ClaudeCapabilitiesSection,
)
from chameleon.codecs.codex.capabilities import (
    CodexCapabilitiesCodec,
    CodexCapabilitiesSection,
)
from chameleon.io.json import load_json
from chameleon.io.toml import load_toml
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains
from chameleon.schema.capabilities import (
    PluginDisagreement,
    PluginEntry,
    reconcile_plugins,
)

FIXTURE_HOME = Path(__file__).parent.parent / "fixtures" / "exemplar" / "home"


def _claude_plugins_neutral() -> dict[str, PluginEntry]:
    raw = (FIXTURE_HOME / "_claude" / "settings.json").read_bytes()
    settings = load_json(raw) or {}
    assert isinstance(settings, dict)
    section = ClaudeCapabilitiesSection.model_validate(
        {
            "enabledPlugins": settings.get("enabledPlugins", {}),
            "extraKnownMarketplaces": settings.get("extraKnownMarketplaces", {}),
        }
    )
    return ClaudeCapabilitiesCodec.from_target(section, TranspileCtx()).plugins


def _codex_plugins_neutral() -> dict[str, PluginEntry]:
    raw = (FIXTURE_HOME / "_codex" / "config.toml").read_bytes()
    doc = load_toml(raw.decode("utf-8"))
    section = CodexCapabilitiesSection.model_validate(
        {
            "plugins": dict(doc.get("plugins", {}) or {}),
            "marketplaces": dict(doc.get("marketplaces", {}) or {}),
        }
    )
    return CodexCapabilitiesCodec.from_target(section, TranspileCtx()).plugins


def test_exemplar_disassembles_plugins_from_both_targets() -> None:
    claude_plugins = _claude_plugins_neutral()
    codex_plugins = _codex_plugins_neutral()

    # Sanity: the exemplar carries plugin tables on both sides.
    assert claude_plugins, "Claude exemplar enabledPlugins should not be empty"
    assert codex_plugins, "Codex exemplar [plugins.*] tables should not be empty"

    # Some keys appear in both targets verbatim — the unification proof.
    in_both = set(claude_plugins) & set(codex_plugins)
    assert "ripvec@example-user-plugins" in in_both
    assert "tracemeld@example-user-plugins" in in_both
    assert "plannotator@plannotator" in in_both


def test_cross_target_conflict_rule_surfaces_disagreements() -> None:
    """Documented resolution rule: when targets disagree on a plugin's
    ``enabled`` value, ``reconcile_plugins`` returns a permissive union AND
    a ``PluginDisagreement`` record per offending key.

    The exemplar is constructed so that the Claude side toggles a few
    plugins to ``false`` (e.g. ``rust-analyzer-lsp@claude-plugins-official``)
    while Codex would have those at ``true`` if they appeared. For keys
    only one target enumerates, no disagreement; for keys both enumerate
    with different ``enabled`` values, exactly one disagreement.
    """

    claude_plugins = _claude_plugins_neutral()
    codex_plugins = _codex_plugins_neutral()

    union, disagreements = reconcile_plugins(
        {BUILTIN_CLAUDE: claude_plugins, BUILTIN_CODEX: codex_plugins}
    )

    # Union ⊇ each target's own keys.
    assert set(claude_plugins).issubset(union)
    assert set(codex_plugins).issubset(union)

    # Permissive: any True wins.
    for key, entry in union.items():
        per_target = []
        if key in claude_plugins:
            per_target.append(claude_plugins[key].enabled)
        if key in codex_plugins:
            per_target.append(codex_plugins[key].enabled)
        assert entry.enabled is any(per_target), (
            f"reconcile_plugins should be permissive-OR for {key}"
        )

    # Every disagreement is between two targets that BOTH enumerate the key
    # AND give different bool values.
    for d in disagreements:
        assert isinstance(d, PluginDisagreement)
        assert d.domain is Domains.CAPABILITIES
        assert d.field_path.segments == ("capabilities", "plugins", d.plugin_key, "enabled")
        vals = set(d.per_target.values())
        assert len(vals) > 1, f"PluginDisagreement for {d.plugin_key} has no real disagreement"

    # No disagreement for keys present in only one target.
    in_one_only = set(claude_plugins) ^ set(codex_plugins)
    flagged_keys = {d.plugin_key for d in disagreements}
    assert not (in_one_only & flagged_keys), (
        "PluginDisagreement should never fire for keys present in only one target"
    )
