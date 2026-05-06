"""P0-3 regression: pass-through bag must propagate through the merge engine.

The disassembler harvests unclaimed top-level keys into a `passthrough`
dict. The original V0 merge engine called `assembler.assemble(passthrough={})`
unconditionally — so unclaimed keys (~40 plugins, marketplaces, hooks in
real Claude configs) only survived because the assembler also reads
`existing` files and merges per-domain on top. Deleting the live target
file destroyed every unclaimed key.

These tests pin the propagation:

  1. A `Neutral` carrying a `targets[BUILTIN_CLAUDE].items["customKey"]`
     bag value lands in the produced settings.json (round-trip from
     neutral → live).
  2. A live key Chameleon doesn't claim survives a merge cycle even when
     the live file is deleted between merges (acceptance #2 from
     `docs/superpowers/specs/2026-05-06-parity-gap.md`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chameleon import cli
from chameleon.io.json import load_json
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.schema.neutral import Neutral


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


def test_passthrough_from_neutral_lands_in_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neutral -> live: a `targets.claude.items.customKey` propagates."""
    paths = _setup_env(monkeypatch, tmp_path)

    # Bootstrap.
    assert cli.main(["init"]) == 0

    # Operator authors a neutral with an explicit pass-through bag for Claude.
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"
    operator_contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {"model": {"claude": "claude-sonnet-4-7"}},
        "targets": {
            "claude": {
                "items": {
                    "customKey": "customValue",
                    "enabledPlugins": {"foo@1.0": True},
                }
            }
        },
    }
    neutral_file.write_text(dump_yaml(operator_contents), encoding="utf-8")

    # Per-FieldPath classification (P2-1) means `identity.model[claude]`
    # no longer false-conflicts on a fresh-from-neutral merge. Run with
    # --on-conflict=fail so any unexpected drift surfaces as a hard
    # failure rather than being silently dropped.
    assert cli.main(["merge", "--on-conflict=fail"]) == 0

    settings = load_json(paths["home"] / ".claude" / "settings.json")
    assert settings.get("customKey") == "customValue"
    assert settings.get("enabledPlugins") == {"foo@1.0": True}


def test_passthrough_survives_deleted_live_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Acceptance #2 from the parity-gap doc.

    Bootstrap a Claude settings.json containing an unclaimed key, run
    `chameleon merge` to absorb it into neutral, delete the live file,
    and run merge again — the unclaimed key must be regenerated from
    neutral alone.
    """
    paths = _setup_env(monkeypatch, tmp_path)

    # Pre-seed a live ~/.claude/settings.json with a key Chameleon doesn't
    # claim. `hooks` is still unclaimed at P1-A (next codec lane is P1-B).
    # When P1-B lands, swap to whichever key remains unclaimed at that time.
    live_settings = paths["home"] / ".claude" / "settings.json"
    live_settings.parent.mkdir(parents=True, exist_ok=True)
    live_settings.write_text(
        '{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": '
        '[{"type": "command", "command": "rtk hook claude"}]}]}}\n',
        encoding="utf-8",
    )

    # init absorbs the live state; first merge adopts unclaimed key into neutral.
    assert cli.main(["init"]) == 0
    assert cli.main(["merge", "--on-conflict=fail"]) == 0

    # The neutral file now must carry the unclaimed key in its targets bag —
    # otherwise the next merge would have nothing to re-derive from after
    # the live file is deleted. ruamel returns CommentedMap which behaves
    # like dict but ty narrows isinstance(_, dict) to dict[Never, Never];
    # validate through Neutral to keep the test typed all the way through.
    neutral_doc_obj = load_yaml(paths["config"] / "chameleon" / "neutral.yaml")
    n = Neutral.model_validate(neutral_doc_obj)
    claude_bag = n.targets.get(BUILTIN_CLAUDE)
    assert claude_bag is not None, "neutral missing targets.claude after first merge"
    assert "hooks" in claude_bag.items, (
        f"expected hooks in neutral.targets.claude.items; got items={claude_bag.items!r}"
    )

    # Operator deletes the live file (e.g. machine wipe / fresh install).
    live_settings.unlink()
    assert not live_settings.exists()

    # Re-running merge must regenerate the file from neutral alone, with
    # the unclaimed key intact. Use --on-conflict=fail to surface any
    # spurious drift that per-FieldPath classification was supposed to
    # eliminate.
    assert cli.main(["merge", "--on-conflict=fail"]) == 0
    assert live_settings.exists()
    settings = load_json(live_settings)
    assert "hooks" in settings, (
        f"unclaimed key was lost on re-derive from neutral alone; got {settings!r}"
    )
    assert settings["hooks"] == {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}
        ]
    }
