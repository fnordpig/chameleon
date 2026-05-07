"""Target-side deletions must round-trip as real changes."""

from __future__ import annotations

import os
from pathlib import Path

import tomlkit

from chameleon import cli
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.schema.neutral import Neutral


def _setup_env(monkeypatch, tmp_path: Path) -> dict[str, Path]:
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


def _set_mtime_ns(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def test_newer_codex_config_deletion_clears_catalog_tuning(monkeypatch, tmp_path: Path) -> None:
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    assert cli.main(["init"]) == 0

    neutral_file.write_text(
        dump_yaml(
            {
                "schema_version": 1,
                "identity": {
                    "model": {"codex": "gpt-5.5"},
                    "context_window": 600000,
                    "compact_threshold": 500000,
                    "model_catalog_path": "~/.codex/model-catalog-600k.json",
                },
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["merge", "--on-conflict=prefer-neutral"]) == 0

    codex_config = paths["home"] / ".codex" / "config.toml"
    doc = tomlkit.parse(codex_config.read_text(encoding="utf-8"))
    for key in (
        "model_context_window",
        "model_auto_compact_token_limit",
        "model_catalog_json",
    ):
        doc.pop(key, None)
    codex_config.write_text(tomlkit.dumps(doc), encoding="utf-8")

    neutral_file.write_text(
        dump_yaml(
            {
                "schema_version": 1,
                "identity": {
                    "model": {"codex": "gpt-5.5"},
                    "context_window": None,
                    "compact_threshold": None,
                    "model_catalog_path": None,
                },
            }
        ),
        encoding="utf-8",
    )

    _set_mtime_ns(neutral_file, 1_000_000_000)
    _set_mtime_ns(codex_config, 2_000_000_000)

    assert cli.main(["merge", "--on-conflict=latest"]) == 0

    after_config = codex_config.read_text(encoding="utf-8")
    assert "model_context_window" not in after_config
    assert "model_auto_compact_token_limit" not in after_config
    assert "model_catalog_json" not in after_config

    after_neutral = Neutral.model_validate(load_yaml(neutral_file))
    assert after_neutral.identity.context_window is None
    assert after_neutral.identity.compact_threshold is None
    assert after_neutral.identity.model_catalog_path is None
