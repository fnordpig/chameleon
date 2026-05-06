"""Issue #44 regression: claimed-but-unauthored target data must survive.

When the operator authors a neutral.yaml that omits whole domains
(e.g. ``capabilities``, ``governance``) but the live target files carry
data those domains *claim* (Codex ``[plugins.*]``, ``[projects.*]``,
``[marketplaces.*]``; Claude ``enabledPlugins``, ``extraKnownMarketplaces``,
``permissions``), the merge engine must preserve that data on re-derive.

Wave-2's per-FieldPath classifier (P2-1) was supposed to make this work
— silence on the neutral side (``n0`` empty, ``n1`` empty) plus evidence
on at least one target side should classify CONSENSUAL and write the
target's value into ``composed``. The bug surfaced by running
``chameleon merge`` against the sanitized exemplar at
``tests/fixtures/exemplar/`` after Wave-2 landed: claimed entries
visible in the live files before the merge are missing after.

The acceptance criterion (parity-gap doc, P0-3 #2 + #3) is sharper than
"some pass-through bag survives" — it must include claimed-but-unauthored
data, because that's the dominant case in real operator setups.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from chameleon import cli
from chameleon.io.json import load_json
from chameleon.io.toml import load_toml
from chameleon.io.yaml import dump_yaml

FIXTURE_HOME = Path(__file__).parent.parent / "fixtures" / "exemplar" / "home"


def _setup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    state.mkdir()
    config.mkdir()
    home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("HOME", str(home))
    return {"state": state, "config": config, "home": home}


def _seed_exemplar_into_home(home: Path) -> None:
    """Mirror tests/fixtures/exemplar/home/ into ``home`` (acting as $HOME)."""
    src_codex = FIXTURE_HOME / "_codex"
    src_claude = FIXTURE_HOME / "_claude"
    src_claude_json = FIXTURE_HOME / "_claude.json"

    dst_codex = home / ".codex"
    dst_claude = home / ".claude"
    dst_claude_json = home / ".claude.json"

    shutil.copytree(src_codex, dst_codex)
    shutil.copytree(src_claude, dst_claude)
    shutil.copy2(src_claude_json, dst_claude_json)


def test_unauthored_codex_plugins_projects_marketplaces_survive_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex `[plugins.*]`, `[projects.*]`, `[marketplaces.*]` must survive.

    The operator authors only ``identity`` + ``directives`` in neutral —
    no ``capabilities`` (which claims ``plugins`` + ``plugin_marketplaces``)
    and no ``governance`` (which claims ``projects`` trust state). The live
    file before merge has ~30 plugin entries, ~9 marketplaces, ~7 trusted
    projects. After merge, those entries must still be in the live file.
    """
    paths = _setup_env(monkeypatch, tmp_path)
    _seed_exemplar_into_home(paths["home"])

    codex_path = paths["home"] / ".codex" / "config.toml"
    pre = load_toml(codex_path.read_bytes().decode("utf-8"))
    pre_plugins = dict(pre.get("plugins", {}) or {})
    pre_marketplaces = dict(pre.get("marketplaces", {}) or {})
    pre_projects = dict(pre.get("projects", {}) or {})
    assert pre_plugins, "fixture pre-condition: codex has [plugins.*]"
    assert pre_marketplaces, "fixture pre-condition: codex has [marketplaces.*]"
    assert pre_projects, "fixture pre-condition: codex has [projects.*]"

    # Bootstrap and absorb live state.
    assert cli.main(["init"]) == 0

    # Operator overwrites neutral with identity + directives ONLY — no
    # capabilities, no governance.
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"
    # Operator authors only identity + directives. We use `keep` here
    # because the exemplar's two targets disagree on a few authored
    # leaves (codex: model_reasoning_effort=xhigh; claude: effortLevel=
    # high) — that's a real conflict orthogonal to issue #44, and KEEP
    # is the documented strategy for "preserve what each target had."
    # The preservation contract under test must hold under KEEP.
    operator_contents: dict[str, object] = {
        "schema_version": 1,
        "directives": {},
    }
    neutral_file.write_text(dump_yaml(operator_contents), encoding="utf-8")

    assert cli.main(["merge", "--on-conflict=keep"]) == 0

    post = load_toml(codex_path.read_bytes().decode("utf-8"))
    post_plugins = dict(post.get("plugins", {}) or {})
    post_marketplaces = dict(post.get("marketplaces", {}) or {})
    post_projects = dict(post.get("projects", {}) or {})

    # Plugin keys must SURVIVE the re-derive — preservation is the issue
    # #44 contract. The unified-codec design (parity-gap P1-A) means the
    # post-merge view may also include cross-target unioned keys (Claude-
    # only plugins propagated into Codex), which is correct behaviour;
    # we therefore assert containment (subset), not strict equality.
    missing_plugins = set(pre_plugins) - set(post_plugins)
    assert not missing_plugins, f"codex plugins lost on re-derive: {sorted(missing_plugins)}"

    # Marketplace keys must survive (per-target operational state like
    # last_updated/last_revision rides via pass-through; the marketplace
    # entry itself comes from the codec). Same subset rule as plugins.
    missing_marketplaces = set(pre_marketplaces) - set(post_marketplaces)
    assert not missing_marketplaces, (
        f"codex marketplaces lost on re-derive: {sorted(missing_marketplaces)}"
    )

    # Project trust state must survive verbatim.
    assert post_projects == pre_projects, (
        f"codex [projects.*] trust state lost on re-derive\n"
        f"  before: {pre_projects!r}\n"
        f"  after:  {post_projects!r}"
    )


def test_unauthored_claude_plugins_marketplaces_permissions_survive_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Claude ``enabledPlugins``, ``extraKnownMarketplaces``, ``permissions`` survive.

    Same setup as the Codex test: operator authors only identity + directives;
    capabilities + authorization domains are claimed by codecs but unauthored
    in neutral, so the live data must round-trip.
    """
    paths = _setup_env(monkeypatch, tmp_path)
    _seed_exemplar_into_home(paths["home"])

    claude_settings_path = paths["home"] / ".claude" / "settings.json"
    pre = load_json(claude_settings_path.read_bytes())
    assert isinstance(pre, dict)
    pre_enabled_plugins = dict(pre.get("enabledPlugins", {}) or {})
    pre_marketplaces = dict(pre.get("extraKnownMarketplaces", {}) or {})
    pre_permissions = dict(pre.get("permissions", {}) or {})
    assert pre_enabled_plugins, "fixture pre-condition"
    assert pre_marketplaces, "fixture pre-condition"
    assert pre_permissions, "fixture pre-condition"

    assert cli.main(["init"]) == 0

    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"
    # Operator authors only identity + directives. We use `keep` here
    # because the exemplar's two targets disagree on a few authored
    # leaves (codex: model_reasoning_effort=xhigh; claude: effortLevel=
    # high) — that's a real conflict orthogonal to issue #44, and KEEP
    # is the documented strategy for "preserve what each target had."
    # The preservation contract under test must hold under KEEP.
    operator_contents: dict[str, object] = {
        "schema_version": 1,
        "directives": {},
    }
    neutral_file.write_text(dump_yaml(operator_contents), encoding="utf-8")

    assert cli.main(["merge", "--on-conflict=keep"]) == 0

    post = load_json(claude_settings_path.read_bytes())
    assert isinstance(post, dict)
    post_enabled_plugins = dict(post.get("enabledPlugins", {}) or {})
    post_marketplaces = dict(post.get("extraKnownMarketplaces", {}) or {})
    post_permissions = dict(post.get("permissions", {}) or {})

    # Same subset semantics as the codex test — the unified-codec design
    # may add cross-target unioned keys; preservation is the requirement.
    missing_plugins = set(pre_enabled_plugins) - set(post_enabled_plugins)
    assert not missing_plugins, (
        f"claude enabledPlugins lost on re-derive: {sorted(missing_plugins)}"
    )

    missing_marketplaces = set(pre_marketplaces) - set(post_marketplaces)
    assert not missing_marketplaces, (
        f"claude extraKnownMarketplaces lost on re-derive: {sorted(missing_marketplaces)}"
    )

    # Permissions are a single claimed leaf (authorization codec); they must
    # survive byte-equal modulo dict ordering.
    assert post_permissions == pre_permissions, (
        f"claude permissions lost on re-derive\n"
        f"  before: {pre_permissions!r}\n"
        f"  after:  {post_permissions!r}"
    )
