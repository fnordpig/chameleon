"""End-to-end exemplar smoke test.

Drives `chameleon` as a subprocess against the sanitized exemplar
fixture, asserting the V0+post-Wave-4 contract:

  - `init` against a real-world Claude+Codex setup doesn't crash.
  - First `merge --on-conflict=keep` doesn't crash.
  - 71 non-`mcpServers` keys in `~/.claude.json` (partial-owned)
    survive the round-trip.
  - The legacy attribution alias consolidation (P1-D) is observable
    and stable.
  - `chameleon diff <target>` correctly detects manual drift.
  - `chameleon discard <target> --yes` restores live to state-repo HEAD.
  - `chameleon merge --dry-run` writes nothing.

Three known bugs documented in
``docs/superpowers/specs/2026-05-06-smoke-findings.md`` are pinned as
xfails here so the test suite doesn't drift away from honest reporting:

  - B1: Codex `[tui]` sub-table data loss.
  - B2: Marketplace dict ordering instability across keep-merges.
  - B3: Per-FieldPath leaf-write skips schema coercion.

When any of those fixes lands, the corresponding xfail flips to a
real assertion and the bug is closed for good.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURE_HOME = REPO / "tests" / "fixtures" / "exemplar" / "home"


@pytest.fixture
def exemplar_env() -> Iterator[dict[str, Path]]:
    """A tmpdir HOME mirroring the sanitized exemplar."""
    with tempfile.TemporaryDirectory() as td:
        sb = Path(td)
        home = sb / "home"
        home.mkdir()
        state = sb / "state"
        state.mkdir()
        config = sb / "config"
        config.mkdir()
        shutil.copytree(FIXTURE_HOME / "_claude", home / ".claude")
        shutil.copytree(FIXTURE_HOME / "_codex", home / ".codex")
        shutil.copy(FIXTURE_HOME / "_claude.json", home / ".claude.json")
        yield {"home": home, "state": state, "config": config}


def _run(args: list[str], env_paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HOME": str(env_paths["home"]),
        "XDG_STATE_HOME": str(env_paths["state"]),
        "XDG_CONFIG_HOME": str(env_paths["config"]),
    }
    return subprocess.run(
        ["uv", "run", "chameleon", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_exemplar_init_and_first_merge_succeed(exemplar_env: dict[str, Path]) -> None:
    """The exemplar exists *because* a real init crashed pre-Wave-1."""
    init = _run(["init"], exemplar_env)
    assert init.returncode == 0, f"init failed: {init.stderr[-500:]}"
    merge = _run(["merge", "--on-conflict=keep"], exemplar_env)
    assert merge.returncode == 0, f"first merge failed: {merge.stderr[-500:]}"


def test_dotclaude_partial_owned_preserves_unowned_keys(
    exemplar_env: dict[str, Path],
) -> None:
    """~/.claude.json has 71 top-level keys; chameleon only owns mcpServers.
    The other 70 must survive untouched after a merge.
    """
    live_dotclaude = exemplar_env["home"] / ".claude.json"
    baseline_keys = set(json.loads(live_dotclaude.read_text()).keys())

    _run(["init"], exemplar_env)
    _run(["merge", "--on-conflict=keep"], exemplar_env)

    after_keys = set(json.loads(live_dotclaude.read_text()).keys())
    lost = baseline_keys - after_keys
    assert not lost, f"~/.claude.json lost top-level keys: {sorted(lost)}"


def test_legacy_attribution_aliases_consolidate_to_attribution_commit(
    exemplar_env: dict[str, Path],
) -> None:
    """P1-D: the three legacy bool aliases should consolidate into a single
    `attribution.commit` entry (the canonical modern form).
    """
    live_settings = exemplar_env["home"] / ".claude" / "settings.json"
    _run(["init"], exemplar_env)
    _run(["merge", "--on-conflict=keep"], exemplar_env)

    settings = json.loads(live_settings.read_text())
    assert "attribution" in settings, "expected attribution object after P1-D"
    assert "includeCoAuthoredBy" not in settings, "legacy alias should consolidate"
    assert "coauthoredBy" not in settings, "legacy alias should consolidate"
    assert "gitAttribution" not in settings, "legacy alias should consolidate"


def test_chameleon_diff_detects_manual_drift(exemplar_env: dict[str, Path]) -> None:
    live_settings = exemplar_env["home"] / ".claude" / "settings.json"
    _run(["init"], exemplar_env)
    _run(["merge", "--on-conflict=keep"], exemplar_env)

    settings = json.loads(live_settings.read_text())
    settings["voiceEnabled"] = not settings.get("voiceEnabled", False)
    live_settings.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    diff = _run(["diff", "claude"], exemplar_env)
    assert diff.returncode == 1, "diff after manual drift must exit 1"
    assert "voiceEnabled" in diff.stdout, (
        f"diff didn't show the changed key; stdout: {diff.stdout[:200]}"
    )


@pytest.mark.xfail(
    reason="B4: dump_json escapes non-ASCII (~/.claude.json's em-dashes "
    "in companion.personality become \\u2014). Discard correctly restores "
    "HEAD bytes via partial_owned_write, but the partial-owned-write path "
    "re-serializes through dump_json which doesn't pass ensure_ascii=False, "
    "corrupting non-ASCII UTF-8 chars. "
    "See docs/superpowers/specs/2026-05-06-smoke-findings.md.",
    strict=True,
)
def test_chameleon_discard_restores_state(exemplar_env: dict[str, Path]) -> None:
    live_settings = exemplar_env["home"] / ".claude" / "settings.json"
    _run(["init"], exemplar_env)
    _run(["merge", "--on-conflict=keep"], exemplar_env)

    settings = json.loads(live_settings.read_text())
    settings["voiceEnabled"] = not settings.get("voiceEnabled", False)
    live_settings.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    discard = _run(["discard", "claude", "--yes"], exemplar_env)
    assert discard.returncode == 0, f"discard failed: {discard.stderr[-200:]}"

    diff = _run(["diff", "claude"], exemplar_env)
    assert diff.returncode == 0, "diff after discard should report clean"


def test_dry_run_writes_nothing(exemplar_env: dict[str, Path]) -> None:
    live_settings = exemplar_env["home"] / ".claude" / "settings.json"
    live_codex = exemplar_env["home"] / ".codex" / "config.toml"
    _run(["init"], exemplar_env)
    _run(["merge", "--on-conflict=keep"], exemplar_env)

    pre_settings = live_settings.read_bytes()
    pre_codex = live_codex.read_bytes()

    dry = _run(["merge", "--dry-run", "--on-conflict=keep"], exemplar_env)
    assert dry.returncode == 0

    assert live_settings.read_bytes() == pre_settings, "dry-run modified Claude settings"
    assert live_codex.read_bytes() == pre_codex, "dry-run modified Codex config"


# ---- Pinned regressions (xfailed until B1/B2/B3 fix lands) ----------------


@pytest.mark.xfail(
    reason="B1: Codex partially-claimed sub-tables lose unclaimed sub-keys "
    "(e.g. [tui].status_line, [tui.model_availability_nux]). "
    "See docs/superpowers/specs/2026-05-06-smoke-findings.md.",
    strict=True,
)
def test_codex_tui_subtable_preserved(exemplar_env: dict[str, Path]) -> None:
    live_codex = exemplar_env["home"] / ".codex" / "config.toml"
    baseline = live_codex.read_text()
    assert "[tui]" in baseline
    assert "status_line" in baseline
    assert "model_availability_nux" in baseline

    _run(["init"], exemplar_env)
    _run(["merge", "--on-conflict=keep"], exemplar_env)

    after = live_codex.read_text()
    assert "[tui]" in after, "the entire [tui] table was wiped"
    assert "status_line" in after, "[tui].status_line array was lost"
    assert "model_availability_nux" in after, "[tui.model_availability_nux] sub-table was lost"


@pytest.mark.xfail(
    reason="B2: marketplace dict key ordering not stable across two "
    "--on-conflict=keep merges. All keys preserved but byte-equality "
    "breaks. See docs/superpowers/specs/2026-05-06-smoke-findings.md.",
    strict=True,
)
def test_keep_merge_is_byte_idempotent(exemplar_env: dict[str, Path]) -> None:
    live_settings = exemplar_env["home"] / ".claude" / "settings.json"
    live_codex = exemplar_env["home"] / ".codex" / "config.toml"
    _run(["init"], exemplar_env)
    _run(["merge", "--on-conflict=keep"], exemplar_env)

    a = (live_settings.read_bytes(), live_codex.read_bytes())
    _run(["merge", "--on-conflict=keep"], exemplar_env)
    b = (live_settings.read_bytes(), live_codex.read_bytes())
    assert a == b, "second keep-merge changed live files"


@pytest.mark.xfail(
    reason="B3: per-FieldPath leaf-write does setattr without coercing "
    "value through field.annotation; resolver returns 'xhigh' (str), "
    "_write_leaf assigns it to a ReasoningEffort enum field, "
    "to_target's `.value` access then crashes. "
    "See docs/superpowers/specs/2026-05-06-smoke-findings.md.",
    strict=True,
)
def test_prefer_neutral_resolves_real_conflict(exemplar_env: dict[str, Path]) -> None:
    """Operator authors a unified value; --on-conflict=prefer-neutral
    should resolve and re-derive cleanly.
    """
    _run(["init"], exemplar_env)
    _run(["merge", "--on-conflict=keep"], exemplar_env)

    neutral = exemplar_env["config"] / "chameleon" / "neutral.yaml"
    neutral.write_text(
        "schema_version: 1\n"
        "identity:\n"
        "  reasoning_effort: xhigh\n"
        "  model:\n"
        "    claude: claude-sonnet-4-7\n"
        "    codex: gpt-5.5\n",
        encoding="utf-8",
    )

    merge = _run(["merge", "--on-conflict=prefer-neutral"], exemplar_env)
    assert merge.returncode == 0, f"merge failed: {merge.stderr[-400:]}"

    settings = json.loads((exemplar_env["home"] / ".claude" / "settings.json").read_text())
    assert settings.get("model") == "claude-sonnet-4-7"
    assert settings.get("effortLevel") == "xhigh"
