"""Smoke tests proving the package is importable and the CLI entry point runs."""

from __future__ import annotations

import subprocess
import sys

import chameleon
from chameleon import cli


def test_package_has_version() -> None:
    assert isinstance(chameleon.__version__, str)
    assert chameleon.__version__  # non-empty


def test_cli_main_returns_zero_on_help() -> None:
    rc = cli.main(["--help"])
    assert rc == 0


def test_cli_invokable_via_subprocess() -> None:
    # Verifies the [project.scripts] entry point landed in the venv.
    result = subprocess.run(
        [sys.executable, "-m", "chameleon", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "chameleon" in result.stdout.lower()
