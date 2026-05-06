from __future__ import annotations

from pathlib import Path

import pytest

from chameleon import cli


def test_cli_help_lists_v0_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for cmd in (
        "init",
        "merge",
        "status",
        "diff",
        "log",
        "adopt",
        "discard",
        "validate",
        "doctor",
        "targets",
    ):
        assert cmd in out


def test_cli_unknown_subcommand_exits_non_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["definitely-not-a-command"])
    assert exc_info.value.code != 0


def test_cli_merge_dry_run_invokable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    rc = cli.main(["merge", "--dry-run", "--on-conflict=keep"])
    assert rc == 0
