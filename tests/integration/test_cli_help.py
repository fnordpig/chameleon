from __future__ import annotations

from pathlib import Path

import pytest

from chameleon import cli
from chameleon.io.yaml import dump_yaml
from chameleon.merge.resolve import LatestResolutionError, LatestResolver


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


def test_cli_merge_defaults_to_latest_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    args = cli._build_parser().parse_args(["merge"])
    monkeypatch.setattr(cli, "stdin_is_a_tty", lambda: False)
    resolver = cli._resolver_from_args(args)
    assert isinstance(resolver, LatestResolver)


def test_cli_merge_latest_ambiguity_is_concise_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    class RaisingEngine:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def merge(self, *args: object, **kwargs: object) -> object:
            raise LatestResolutionError("ambiguous latest conflict on identity.model")

    monkeypatch.setattr(cli, "MergeEngine", RaisingEngine)

    rc = cli.main(["merge", "--quiet", "--no-warn"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == (
        "error: ambiguous latest conflict on identity.model\n"
        "rerun `chameleon merge` from an interactive shell or choose "
        "`--on-conflict=<strategy>` explicitly\n"
    )


def test_cli_merge_reports_invalid_neutral_yaml_concisely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    marker = "<" * 7
    neutral = tmp_path / "config" / "chameleon" / "neutral.yaml"
    neutral.parent.mkdir(parents=True)
    neutral.write_text(
        f"schema_version: 1\n{marker} HEAD\nidentity: {{}}\n",
        encoding="utf-8",
    )

    rc = cli.main(["merge", "--quiet", "--no-warn", "--on-conflict=keep"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "error: invalid YAML in " in captured.err
    assert str(neutral) in captured.err
    assert "line 2" in captured.err
    assert "raw git conflict marker" in captured.err


def test_cli_init_dry_run_is_side_effect_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`init --dry-run` must NOT create the neutral file or any state-repo.

    Regression test for the bug where init wrote ~/.config/chameleon/neutral.yaml
    unconditionally before checking dry_run. The merge engine respected dry_run
    but the neutral-bootstrap path did not.
    """
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("HOME", str(home))

    rc = cli.main(["init", "--dry-run"])
    assert rc == 0

    # The whole point: nothing on disk.
    assert not (config / "chameleon" / "neutral.yaml").exists(), "dry-run wrote neutral.yaml"
    assert not (state / "chameleon").exists(), "dry-run created state directory"


def test_cli_verbose_emits_observable_extra_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--verbose` must produce observable extra stderr beyond the default.

    Without --verbose the merge command emits only the summary and any
    LossWarnings. With --verbose it also emits a preamble (state_root,
    neutral path, registered targets) and a per-target warning-count
    tally after the summary. This test pins both the absence (no
    'verbose:' lines without the flag) and the presence (lines tagged
    'verbose:' on stderr with the flag).
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    cli.main(["init"])

    # Without --verbose: zero "verbose:" prefixed lines.
    capsys.readouterr()  # drain init's output
    assert cli.main(["merge", "--on-conflict=keep"]) == 0
    quiet_err = capsys.readouterr().err
    assert "verbose:" not in quiet_err, f"non-verbose merge leaked verbose: lines:\n{quiet_err}"

    # With --verbose: at least the preamble's three lines (state_root,
    # neutral, targets) and the per-target tally on stderr.
    assert cli.main(["merge", "--verbose", "--on-conflict=keep"]) == 0
    verbose_err = capsys.readouterr().err
    assert "verbose: state_root=" in verbose_err
    assert "verbose: neutral=" in verbose_err
    assert "verbose: targets=" in verbose_err
    assert "LossWarning(s)" in verbose_err


def test_cli_merge_quiet_no_warn_is_silent_when_clean(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["merge", "--quiet", "--no-warn", "--on-conflict=keep"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cli_merge_no_warn_suppresses_losswarning_errata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert cli.main(["init"]) == 0
    neutral = tmp_path / "config" / "chameleon" / "neutral.yaml"
    neutral.write_text(
        dump_yaml(
            {
                "schema_version": 1,
                "identity": {
                    "context_window": 600000,
                },
            }
        ),
        encoding="utf-8",
    )

    capsys.readouterr()
    assert cli.main(["merge", "--on-conflict=prefer-neutral"]) == 0
    with_warn = capsys.readouterr()
    assert "warning:" in with_warn.err

    neutral.write_text(
        dump_yaml(
            {
                "schema_version": 1,
                "identity": {
                    "context_window": 700000,
                },
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["merge", "--on-conflict=prefer-neutral", "--no-warn"]) == 0
    no_warn = capsys.readouterr()
    assert "warning:" not in no_warn.err
