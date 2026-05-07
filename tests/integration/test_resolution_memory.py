"""integration acceptance: resolution-memory behaviour.

The four behavioural acceptance criteria implementable without the
interactive UI (W15-B owns that):

1. Same disagreement, same hash → no re-prompt; auto-applied silently.
2. GC removes stale entries when the disagreement converges.
3. Non-interactive strategy (KEEP) does not persist any resolutions.
4. ``chameleon resolutions list / clear`` works end-to-end.

The interactive ``[t]`` choice is W15-B's territory — these tests
either drive the engine via ``NonInteractiveResolver`` or pre-seed
``Neutral.resolutions`` directly to exercise the silent-replay path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from os.path import expanduser
from pathlib import Path

import pytest

from chameleon import cli
from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge.changeset import walk_changes
from chameleon.merge.resolutions import compute_decision_hash, render_change_path
from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.schema.neutral import (
    Neutral,
    Resolution,
    ResolutionDecisionKind,
)
from chameleon.targets._registry import TargetRegistry


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


def _read_neutral(path: Path) -> Neutral:
    return Neutral.model_validate(load_yaml(path))


def test_same_hash_auto_applies_silently(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Acceptance: same disagreement, same decision → no re-prompt."""
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    # 1. Bootstrap.
    assert cli.main(["init"]) == 0

    # 2. Author conflicting per-target identity.model so the next merge
    # produces a CONFLICT (operator says one thing, targets another).
    contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {
            "model": {
                "claude": "claude-opus-4-7",
                "codex": "gpt-5-pro",
            },
        },
    }
    neutral_file.write_text(dump_yaml(contents), encoding="utf-8")
    assert cli.main(["merge", "--on-conflict=prefer=claude"]) == 0

    # 3. Force a conflict: edit Codex's live config to drift away from
    # neutral so the walker emits a CONFLICT for identity.model[codex].
    codex_config = paths["home"] / ".codex" / "config.toml"
    text = codex_config.read_text()
    text = text.replace("gpt-5-pro", "gpt-5.4-pro")
    codex_config.write_text(text)

    # 4. Pre-seed a TAKE_NEUTRAL resolution whose hash matches the
    # current disagreement — easiest path is to compute the hash by
    # invoking the engine's classification helper.
    n_now = _read_neutral(neutral_file)
    # Drive merge once with prefer-neutral to land the resolution path
    # we'll auto-replay; we then assert that on the next merge with
    # --on-conflict=fail (which would raise on any unresolved conflict)
    # the auto-replay path silently applies. To avoid depending on B's
    # interactive UI we drop a Resolution into the YAML with a hash
    # computed off the live ChangeRecord.
    targets = TargetRegistry.discover()
    ctx = TranspileCtx()
    per_target_neutral: dict[TargetId, Neutral] = {}
    for tid in targets.target_ids():
        target_cls = targets.get(tid)
        if target_cls is None:
            continue
        live: dict[str, bytes] = {}
        for spec in target_cls.assembler.files:
            live_path = Path(expanduser(spec.live_path))
            if live_path.exists():
                live[spec.repo_path] = live_path.read_bytes()
        domains, _ = target_cls.assembler.disassemble(live, ctx=ctx)
        target_neutral = Neutral(schema_version=1)
        for codec_cls in target_cls.codecs:
            if codec_cls.domain not in domains:
                continue
            try:
                fragment = codec_cls.from_target(domains[codec_cls.domain], ctx)
            except NotImplementedError:
                continue
            setattr(target_neutral, codec_cls.domain.value, fragment)
        per_target_neutral[tid] = target_neutral

    n0 = _read_neutral(paths["state"] / "chameleon" / "neutral.lkg.yaml")
    records = walk_changes(n0, n_now, per_target_neutral)
    target_record = next(
        r
        for r in records
        if r.path == FieldPath(segments=("identity", "model"))
        and any(v != r.n1 for v in r.per_target.values())
    )
    target_hash = compute_decision_hash(target_record)
    n_now.resolutions.items[render_change_path(target_record)] = Resolution(
        decided_at=datetime.now(tz=UTC),
        decision=ResolutionDecisionKind.TAKE_NEUTRAL,
        decision_hash=target_hash,
    )
    neutral_file.write_text(
        dump_yaml(n_now.model_dump(mode="json", exclude_none=False)),
        encoding="utf-8",
    )

    # 5. Run merge with --on-conflict=fail. Without the resolution, this
    # would raise; with the matching-hash resolution, the engine
    # auto-applies and exits 0.
    assert cli.main(["merge", "--on-conflict=fail"]) == 0


def test_gc_removes_stale_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Acceptance: GC prunes resolutions whose disagreement converged."""
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    # Initial bootstrap + a clean merge so target state exists.
    assert cli.main(["init"]) == 0
    contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {
            "reasoning_effort": "high",
        },
    }
    neutral_file.write_text(dump_yaml(contents), encoding="utf-8")
    assert cli.main(["merge", "--on-conflict=prefer-neutral"]) == 0

    # Plant a stale TAKE_NEUTRAL resolution at a path that has no
    # current disagreement (every target now matches neutral after the
    # prior merge). The GC pass on the next clean merge should prune it.
    n_now = _read_neutral(neutral_file)
    n_now.resolutions.items["identity.reasoning_effort"] = Resolution(
        decided_at=datetime.now(tz=UTC),
        decision=ResolutionDecisionKind.TAKE_NEUTRAL,
        decision_hash="dead-hash",
    )
    neutral_file.write_text(
        dump_yaml(n_now.model_dump(mode="json", exclude_none=False)),
        encoding="utf-8",
    )

    assert cli.main(["merge", "--on-conflict=fail"]) == 0
    n_after = _read_neutral(neutral_file)
    assert "identity.reasoning_effort" not in n_after.resolutions.items


def test_non_interactive_strategy_does_not_persist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Acceptance: ``--on-conflict=keep`` stores zero new resolutions."""
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    assert cli.main(["init"]) == 0

    # Trigger a real conflict.
    contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {
            "reasoning_effort": "high",
        },
    }
    neutral_file.write_text(dump_yaml(contents), encoding="utf-8")
    assert cli.main(["merge", "--on-conflict=prefer-neutral"]) == 0
    # Drift Codex away.
    codex_config = paths["home"] / ".codex" / "config.toml"
    text = codex_config.read_text()
    text = text.replace('model_reasoning_effort = "high"', 'model_reasoning_effort = "low"')
    codex_config.write_text(text)

    # KEEP must not persist anything.
    assert cli.main(["merge", "--on-conflict=keep"]) == 0
    n_after = _read_neutral(neutral_file)
    # Either empty or unchanged from before — but no NEW entries from KEEP.
    assert n_after.resolutions.items == {}


def test_resolutions_list_and_clear_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Acceptance: list/clear subcommands work end-to-end."""
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    assert cli.main(["init"]) == 0
    n = _read_neutral(neutral_file)
    n.resolutions.items["identity.reasoning_effort"] = Resolution(
        decided_at=datetime(2026, 5, 6, 18, 42, tzinfo=UTC),
        decision=ResolutionDecisionKind.TAKE_NEUTRAL,
        decision_hash="hash-a",
    )
    n.resolutions.items["identity.model[claude]"] = Resolution(
        decided_at=datetime(2026, 5, 6, 18, 43, tzinfo=UTC),
        decision=ResolutionDecisionKind.TAKE_TARGET,
        decision_target=BUILTIN_CLAUDE,
        decision_hash="hash-b",
    )
    neutral_file.write_text(
        dump_yaml(n.model_dump(mode="json", exclude_none=False)),
        encoding="utf-8",
    )

    # list
    capsys.readouterr()
    assert cli.main(["resolutions", "list"]) == 0
    out = capsys.readouterr().out
    assert "identity.reasoning_effort" in out
    assert "identity.model[claude]" in out
    assert "take_neutral" in out
    assert "take_target" in out

    # clear single (with --yes off-TTY)
    assert cli.main(["resolutions", "clear", "identity.reasoning_effort", "--yes"]) == 0
    n_after = _read_neutral(neutral_file)
    assert "identity.reasoning_effort" not in n_after.resolutions.items
    assert "identity.model[claude]" in n_after.resolutions.items

    # clear all
    assert cli.main(["resolutions", "clear", "--yes"]) == 0
    n_final = _read_neutral(neutral_file)
    assert n_final.resolutions.items == {}


def test_resolutions_list_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty resolutions table prints a friendly placeholder."""
    _setup_env(monkeypatch, tmp_path)
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["resolutions", "list"]) == 0
    out = capsys.readouterr().out
    assert "no stored resolutions" in out


def test_cli_help_lists_resolutions_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "resolutions" in out
