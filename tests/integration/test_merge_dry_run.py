"""End-to-end tests for `chameleon merge --dry-run` (P2-2).

Dry-run runs the full pipeline (sample → disassemble → classify → resolve →
compose → re-derive) but skips writes-to-disk and emits a unified diff
against live, identical to what `chameleon diff` would show after the
non-dry-run merge.
"""

from __future__ import annotations

from pathlib import Path

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


def _bootstrap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    paths = _setup_env(monkeypatch, tmp_path)
    assert cli.main(["init"]) == 0
    return paths


def _snapshot_dir(root: Path) -> dict[Path, bytes]:
    """Snapshot every file under ``root`` recursively as bytes."""
    out: dict[Path, bytes] = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            out[p] = p.read_bytes()
    return out


def _author_distinct_identity(neutral_file: Path) -> None:
    """Edit the neutral file so it differs from what bootstrap composed.

    Uses a clearly-distinct identity so the diff text is easy to assert on.
    """
    operator: dict[str, object] = {
        "schema_version": 1,
        "identity": {
            "reasoning_effort": "high",
            "model": {
                "claude": "claude-sonnet-DRYRUN",
                "codex": "gpt-DRYRUN",
            },
        },
    }
    neutral_file.write_text(dump_yaml(operator), encoding="utf-8")


def test_dry_run_emits_diff_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _bootstrap(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"
    _author_distinct_identity(neutral_file)

    # Snapshot every file under home/ and state/ before the dry-run, except
    # the neutral file itself (which we just edited and is the *input*).
    home_before = _snapshot_dir(paths["home"])
    state_before = _snapshot_dir(paths["state"])
    neutral_before = neutral_file.read_bytes()

    capsys.readouterr()  # flush bootstrap output
    rc = cli.main(["merge", "--on-conflict=fail", "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0, f"dry-run should succeed; stderr={captured.err!r}"

    # No file under home/ or state/ should have changed.
    home_after = _snapshot_dir(paths["home"])
    state_after = _snapshot_dir(paths["state"])
    assert home_after == home_before, (
        f"dry-run modified files under home/: "
        f"added={set(home_after) - set(home_before)} "
        f"removed={set(home_before) - set(home_after)} "
        f"changed={[p for p in home_after if p in home_before and home_after[p] != home_before[p]]}"
    )
    assert state_after == state_before, "dry-run modified state-repo files"
    assert neutral_file.read_bytes() == neutral_before, "dry-run rewrote neutral.yaml"

    # Stdout must contain a unified diff with the new identity values.
    assert "---" in captured.out
    assert "+++" in captured.out
    assert "claude-sonnet-DRYRUN" in captured.out, (
        f"expected new identity in diff stdout, got:\n{captured.out}"
    )


def test_real_merge_after_dry_run_actually_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _bootstrap(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"
    _author_distinct_identity(neutral_file)

    settings_path = paths["home"] / ".claude" / "settings.json"
    codex_path = paths["home"] / ".codex" / "config.toml"
    settings_before = settings_path.read_bytes()
    codex_before = codex_path.read_bytes()

    capsys.readouterr()
    rc = cli.main(["merge", "--on-conflict=fail"])
    assert rc == 0
    # At least one of the two target files should have changed.
    settings_after = settings_path.read_bytes()
    codex_after = codex_path.read_bytes()
    assert settings_before != settings_after or codex_before != codex_after, (
        "real merge should have rewritten at least one target file"
    )
    assert b"claude-sonnet-DRYRUN" in settings_after


def test_dry_run_no_changes_emits_blank_diff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When neutral matches live, dry-run prints no diff hunks but still exits 0."""
    paths = _bootstrap(monkeypatch, tmp_path)

    home_before = _snapshot_dir(paths["home"])
    state_before = _snapshot_dir(paths["state"])

    capsys.readouterr()
    rc = cli.main(["merge", "--on-conflict=fail", "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0

    # No diff hunks emitted (no `+++` / `---` lines).
    assert "+++" not in captured.out
    assert "---" not in captured.out

    # Files unchanged.
    assert _snapshot_dir(paths["home"]) == home_before
    assert _snapshot_dir(paths["state"]) == state_before
