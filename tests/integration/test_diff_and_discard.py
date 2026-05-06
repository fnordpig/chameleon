"""End-to-end tests for `chameleon diff` and `chameleon discard` (P2-3)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from chameleon import cli
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


def _bootstrap_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    paths = _setup_env(monkeypatch, tmp_path)
    assert cli.main(["init"]) == 0
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"
    operator_contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {
            "reasoning_effort": "high",
            "model": {"claude": "claude-sonnet-4-7", "codex": "gpt-5.4"},
        },
        "environment": {"variables": {"CI": "true"}},
    }
    neutral_file.write_text(dump_yaml(operator_contents), encoding="utf-8")
    assert cli.main(["merge", "--on-conflict=fail"]) == 0
    return paths


def test_diff_clean_target_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _bootstrap_clean(monkeypatch, tmp_path)
    capsys.readouterr()  # flush merge output
    rc = cli.main(["diff", "claude"])
    captured = capsys.readouterr()
    assert rc == 0, f"expected clean exit; stdout={captured.out!r} stderr={captured.err!r}"
    assert captured.out == "", f"expected empty stdout for clean diff, got {captured.out!r}"


def test_diff_with_drift_shows_unified_diff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _bootstrap_clean(monkeypatch, tmp_path)
    settings_path = paths["home"] / ".claude" / "settings.json"
    obj = json.loads(settings_path.read_text())
    obj["model"] = "claude-opus-DRIFTED"
    settings_path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    capsys.readouterr()  # flush bootstrap noise
    rc = cli.main(["diff", "claude"])
    captured = capsys.readouterr()
    assert rc == 1, f"expected drift exit code 1; stdout={captured.out!r}"
    # unified diff markers
    assert "---" in captured.out
    assert "+++" in captured.out
    assert "claude-opus-DRIFTED" in captured.out
    # original value shown as a removed line
    assert any(
        line.startswith("-") and "claude-sonnet-4-7" in line for line in captured.out.splitlines()
    ), captured.out


def test_diff_all_targets_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _bootstrap_clean(monkeypatch, tmp_path)
    # Drift only the codex side.
    codex_path = paths["home"] / ".codex" / "config.toml"
    text = codex_path.read_text()
    codex_path.write_text(text + '\nextra_codex_only_key = "drifted"\n', encoding="utf-8")

    capsys.readouterr()
    rc = cli.main(["diff"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "extra_codex_only_key" in captured.out
    # Should mention the codex repo path somewhere in the diff header.
    assert "config.toml" in captured.out


def test_discard_restores_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _bootstrap_clean(monkeypatch, tmp_path)
    settings_path = paths["home"] / ".claude" / "settings.json"
    head_bytes = settings_path.read_bytes()
    settings_path.write_text('{ "model": "DRIFTED" }\n', encoding="utf-8")
    assert settings_path.read_bytes() != head_bytes

    capsys.readouterr()
    rc = cli.main(["discard", "claude", "--yes"])
    assert rc == 0
    assert settings_path.read_bytes() == head_bytes


def test_discard_without_yes_prompts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _bootstrap_clean(monkeypatch, tmp_path)
    settings_path = paths["home"] / ".claude" / "settings.json"
    head_bytes = settings_path.read_bytes()
    drifted = b'{ "model": "DRIFTED" }\n'
    settings_path.write_bytes(drifted)

    # Force interactive path: pretend we're on a TTY, mock confirm.
    monkeypatch.setattr(cli, "_stdin_is_a_tty", lambda: True, raising=False)

    answers = iter(["n", "y"])

    def fake_confirm(_prompt: object, **_kwargs: Any) -> bool:
        return next(answers) == "y"

    monkeypatch.setattr(cli, "_confirm_discard", fake_confirm, raising=False)

    # 1) decline → file unchanged
    capsys.readouterr()
    rc = cli.main(["discard", "claude"])
    assert rc == 0  # declining is not an error
    assert settings_path.read_bytes() == drifted

    # 2) accept → restored
    rc = cli.main(["discard", "claude"])
    assert rc == 0
    assert settings_path.read_bytes() == head_bytes


def test_discard_unknown_target_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _bootstrap_clean(monkeypatch, tmp_path)
    capsys.readouterr()
    rc = cli.main(["discard", "nonexistent", "--yes"])
    captured = capsys.readouterr()
    assert rc != 0
    # Error message must mention the bad target name.
    assert "nonexistent" in captured.err or "nonexistent" in captured.out


def test_discard_off_tty_without_yes_refuses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _bootstrap_clean(monkeypatch, tmp_path)
    settings_path = paths["home"] / ".claude" / "settings.json"
    drifted = b'{ "model": "DRIFTED" }\n'
    settings_path.write_bytes(drifted)

    # Force non-TTY context (CI / pipe).
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    capsys.readouterr()
    rc = cli.main(["discard", "claude"])
    captured = capsys.readouterr()
    assert rc != 0
    assert "--yes" in captured.err or "--yes" in captured.out
    # File must not have changed.
    assert settings_path.read_bytes() == drifted
