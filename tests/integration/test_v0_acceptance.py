"""V0 acceptance: end-to-end exercise of init -> edit neutral -> merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from chameleon import cli
from chameleon.io.json import load_json
from chameleon.io.toml import load_toml
from chameleon.io.yaml import dump_yaml


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


def test_full_v0_acceptance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _setup_env(monkeypatch, tmp_path)

    # 1. Bootstrap with `init`.
    assert cli.main(["init"]) == 0

    # 2. Operator edits the neutral file.
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"
    # Build the operator's neutral edit as a fresh dict (rather than load + mutate)
    # so static type-checkers can see the dict shape directly.
    operator_contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {
            "reasoning_effort": "high",
            "model": {
                "claude": "claude-sonnet-4-7",
                "codex": "gpt-5.4",
            },
        },
        "environment": {"variables": {"CI": "true"}},
    }
    neutral_file.write_text(dump_yaml(operator_contents), encoding="utf-8")

    # 3. Run merge.
    assert cli.main(["merge", "--on-conflict=fail"]) == 0

    # 4. Verify Claude settings.json has the right keys.
    claude_settings = paths["home"] / ".claude" / "settings.json"
    assert claude_settings.exists()
    s = load_json(claude_settings)
    assert s.get("model") == "claude-sonnet-4-7"
    assert s.get("effortLevel") == "high"
    assert s.get("env") == {"CI": "true"}

    # 5. Verify Codex config.toml has the right keys.
    codex_config = paths["home"] / ".codex" / "config.toml"
    assert codex_config.exists()
    c = load_toml(codex_config)
    assert c["model"] == "gpt-5.4"
    assert c["model_reasoning_effort"] == "high"

    # 6. Run merge again with FAIL. With per-FieldPath classification
    # (P2-1, merge/changeset.py:walk_changes), `dict[TargetId, V]`
    # fields like `identity.model` no longer false-conflict on re-merge:
    # each target's reverse codec speaks only for its own TargetId key,
    # and the walker classifies each key independently against that
    # target's own evidence. The second merge is a true no-op.
    rc = cli.main(["merge", "--on-conflict=fail"])
    assert rc == 0

    # 7. doctor should be clean — no transactions outstanding.
    assert cli.main(["doctor"]) == 0
