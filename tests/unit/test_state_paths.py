from __future__ import annotations

from pathlib import Path

import pytest

from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.state.paths import StatePaths


def test_state_root_under_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    paths = StatePaths.resolve()
    assert paths.state_root == tmp_path / "chameleon"


def test_target_repo_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    paths = StatePaths.resolve()
    repo = paths.target_repo(BUILTIN_CLAUDE)
    assert repo == tmp_path / "chameleon" / "targets" / "claude"


def test_neutral_path_under_xdg_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    paths = StatePaths.resolve()
    assert paths.neutral == tmp_path / "chameleon" / "neutral.yaml"
