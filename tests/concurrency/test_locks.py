from __future__ import annotations

import json
from pathlib import Path

import pytest

from chameleon.state.locks import partial_owned_write

pytestmark = pytest.mark.concurrency


def test_partial_owned_write_preserves_unowned_keys(tmp_path: Path) -> None:
    target = tmp_path / "claude.json"
    target.write_text('{"oauth_token": "secret", "mcpServers": {"old": {}}}', encoding="utf-8")

    def update(existing: dict[str, object]) -> dict[str, object]:
        existing["mcpServers"] = {"new": {"command": "x"}}
        return existing

    partial_owned_write(target, owned_keys=frozenset({"mcpServers"}), update=update)

    final = json.loads(target.read_text(encoding="utf-8"))
    assert final["oauth_token"] == "secret"
    assert final["mcpServers"] == {"new": {"command": "x"}}


def test_partial_owned_write_detects_concurrent_modification(tmp_path: Path) -> None:
    target = tmp_path / "claude.json"
    target.write_text('{"a": 1, "mcpServers": {}}', encoding="utf-8")

    call_count = {"n": 0}

    def update(existing: dict[str, object]) -> dict[str, object]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            target.write_text('{"a": 1, "mcpServers": {}, "b": 2}', encoding="utf-8")
        existing["mcpServers"] = {"x": "y"}
        return existing

    partial_owned_write(target, owned_keys=frozenset({"mcpServers"}), update=update)

    final = json.loads(target.read_text(encoding="utf-8"))
    assert final["a"] == 1
    assert final["mcpServers"] == {"x": "y"}
