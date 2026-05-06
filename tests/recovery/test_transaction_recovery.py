"""End-to-end transaction-marker recovery tests.

Pins the design intent of `MergeTransaction` + `TransactionStore`:
the engine writes a marker before any live target file is mutated,
clears it on success, and `chameleon doctor` surfaces stale markers
left behind by an interrupted run. The same fixtures exercise
`MergeTransaction.partial_owned_hashes` for the partial-owned files
listed by an assembler (e.g. `~/.claude.json`).

Several tests here are `xfail(strict=True)` against the V0+ engine —
the marker plumbing is declared in `state.transaction` and the doctor
already enumerates `tx_store.entries()`, but the engine itself does
not yet wire `MergeEngine.merge()` through `tx_store.write()` /
`tx_store.clear()`. Wave-6 land-the-engine-changes flips these to
real assertions; until then the test surface documents the contract
the engine must satisfy.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chameleon._types import FileOwnership
from chameleon.cli import main as cli_main
from chameleon.merge.engine import MergeEngine, MergeRequest
from chameleon.merge.resolve import Strategy
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, OnConflict
from chameleon.state.paths import StatePaths
from chameleon.state.transaction import (
    MergeTransaction,
    TransactionStore,
    transaction_id,
)
from chameleon.targets._registry import TargetRegistry

pytestmark = pytest.mark.recovery


REPO = Path(__file__).resolve().parent.parent.parent
FIXTURE_HOME = REPO / "tests" / "fixtures" / "exemplar" / "home"


@pytest.fixture
def exemplar_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    """Mirror `tests/integration/test_exemplar_smoke.py`'s sandboxed HOME.

    Same shape as that fixture so its $HOME / XDG_* contract is the
    canonical one — recovery tests piggyback on the exemplar fixture
    rather than rolling a parallel rig.
    """
    with tempfile.TemporaryDirectory() as td:
        sb = Path(td)
        home = sb / "home"
        home.mkdir()
        state = sb / "state"
        state.mkdir()
        config = sb / "config"
        config.mkdir()
        shutil.copytree(FIXTURE_HOME / "_claude", home / ".claude")
        shutil.copytree(FIXTURE_HOME / "_codex", home / ".codex")
        shutil.copy(FIXTURE_HOME / "_claude.json", home / ".claude.json")

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config))

        yield {"home": home, "state": state, "config": config}


def _resolve_paths(env: dict[str, Path]) -> StatePaths:
    """Materialise the StatePaths the CLI / engine would derive from $XDG_*.

    Mirrors `chameleon.cli._resolve_paths` without going through argparse.
    """
    return StatePaths.resolve()


def _run_cli(args: list[str], env: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    """Invoke `chameleon` as a subprocess (matches integration smoke style)."""
    sub_env = {
        **os.environ,
        "HOME": str(env["home"]),
        "XDG_STATE_HOME": str(env["state"]),
        "XDG_CONFIG_HOME": str(env["config"]),
    }
    return subprocess.run(
        ["uv", "run", "chameleon", *args],
        cwd=REPO,
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )


def _seed_neutral_and_lkg(env: dict[str, Path]) -> None:
    """Run `chameleon init` once so neutral.yaml + LKG exist for the next merge."""
    out = _run_cli(["init"], env)
    assert out.returncode == 0, f"init failed: {out.stderr[-300:]}"


# --------------------------------------------------------------------------
# 1. Marker is written BEFORE any live target file is touched.
# --------------------------------------------------------------------------


def test_transaction_marker_written_before_live_file_writes(
    exemplar_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crash-inject the live-write path; assert a marker is on disk afterwards."""
    _seed_neutral_and_lkg(exemplar_env)

    paths = _resolve_paths(exemplar_env)
    tx_store_dir = paths.tx_dir

    # Force a crash on the very first live file write the engine attempts.
    # The engine writes through `Path.write_bytes`; we trip after the first
    # call by replacing the bound method with a counting wrapper. After the
    # crash the marker MUST be on disk (engine wrote it earlier in the
    # pipeline) and the live file MUST NOT have changed.
    call_count = {"n": 0}
    original_write_bytes = Path.write_bytes

    def crashy_write_bytes(self: Path, data: bytes) -> int:
        call_count["n"] += 1
        # Allow neutral / LKG / state-repo internals to write freely; only
        # block the live target paths under $HOME so we simulate a crash
        # MID-merge. Live paths are inside the sandboxed home tree.
        home_str = str(exemplar_env["home"])
        if str(self).startswith(home_str) and call_count["n"] >= 1:
            msg = "simulated crash mid-merge"
            raise RuntimeError(msg)
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", crashy_write_bytes)

    targets = TargetRegistry.discover()
    engine = MergeEngine(targets=targets, paths=paths, strategy=Strategy(kind=OnConflict.KEEP))

    with pytest.raises(RuntimeError, match="simulated crash"):
        engine.merge(MergeRequest())

    monkeypatch.setattr(Path, "write_bytes", original_write_bytes)

    assert tx_store_dir.exists(), "engine did not create tx_dir before crashing"
    markers = list(tx_store_dir.glob("*.toml"))
    assert markers, (
        f"no marker on disk after mid-merge crash; tx_dir={tx_store_dir} contents="
        f"{[p.name for p in tx_store_dir.iterdir()] if tx_store_dir.exists() else 'missing'}"
    )


# --------------------------------------------------------------------------
# 2. Doctor surfaces a stale marker (works today after the doctor patch).
# --------------------------------------------------------------------------


def test_doctor_surfaces_stale_marker(
    exemplar_env: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pre-seed a marker via TransactionStore.write(); doctor must report it."""
    paths = _resolve_paths(exemplar_env)
    paths.tx_dir.mkdir(parents=True, exist_ok=True)
    store = TransactionStore(paths.tx_dir)

    tx = MergeTransaction(
        merge_id=transaction_id(),
        started_at=datetime.now(tz=UTC),
        target_ids=[BUILTIN_CLAUDE, BUILTIN_CODEX],
        neutral_lkg_hash_after="cafebabe",
        partial_owned_hashes={"~/.claude.json": "deadbeef"},
    )
    store.write(tx)

    rc = cli_main(["doctor"])
    out = capsys.readouterr()
    combined = out.out + out.err

    assert rc == 1, f"doctor exit code should be 1 with a stale marker; got {rc}"
    assert "transactions" in combined, f"doctor stdout did not mention transactions: {combined!r}"
    assert tx.merge_id in combined, (
        f"doctor stdout did not mention the stale merge_id={tx.merge_id}: {combined!r}"
    )


# --------------------------------------------------------------------------
# 3. A successful merge clears stale markers (Wave-6 recovery contract).
# --------------------------------------------------------------------------


def test_recovery_clears_stale_marker_on_clean_merge(
    exemplar_env: dict[str, Path],
) -> None:
    """Pre-seed a stale marker, run a clean merge, expect the marker gone."""
    _seed_neutral_and_lkg(exemplar_env)

    paths = _resolve_paths(exemplar_env)
    paths.tx_dir.mkdir(parents=True, exist_ok=True)
    store = TransactionStore(paths.tx_dir)

    stale = MergeTransaction(
        merge_id=transaction_id(),
        started_at=datetime.now(tz=UTC),
        target_ids=[BUILTIN_CLAUDE],
        neutral_lkg_hash_after="00",
        partial_owned_hashes={},
    )
    store.write(stale)
    assert any(paths.tx_dir.glob("*.toml")), "fixture failed to seed stale marker"

    out = _run_cli(["merge", "--on-conflict=keep"], exemplar_env)
    assert out.returncode == 0, f"clean merge failed: {out.stderr[-300:]}"

    leftover = list(paths.tx_dir.glob("*.toml"))
    assert leftover == [], (
        f"clean merge left {len(leftover)} stale marker(s) behind: {[p.name for p in leftover]}"
    )


# --------------------------------------------------------------------------
# 4. partial_owned_hashes carries pre-merge file hashes during the merge.
# --------------------------------------------------------------------------


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_partial_owned_hashes_track_intermediate_state(
    exemplar_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During merge, the marker must capture pre-merge hashes of partial-owned files."""
    _seed_neutral_and_lkg(exemplar_env)

    paths = _resolve_paths(exemplar_env)
    targets = TargetRegistry.discover()

    # Compute the pre-merge hash of the partial-owned file (~/.claude.json).
    pre_hash_by_path: dict[str, str] = {}
    for tid in targets.target_ids():
        target_cls = targets.get(tid)
        if target_cls is None:
            continue
        for spec in target_cls.assembler.files:
            if spec.ownership is not FileOwnership.PARTIAL:
                continue
            live = Path(os.path.expanduser(spec.live_path))
            if live.exists():
                pre_hash_by_path[spec.live_path] = _sha256_path(live)

    assert pre_hash_by_path, "exemplar fixture has no partial-owned files; assembler changed?"

    # Capture the marker the engine writes by intercepting TransactionStore.write.
    captured: list[MergeTransaction] = []
    original_write = TransactionStore.write

    def capture_write(self: TransactionStore, tx: MergeTransaction) -> None:
        captured.append(tx)
        original_write(self, tx)

    monkeypatch.setattr(TransactionStore, "write", capture_write)

    engine = MergeEngine(targets=targets, paths=paths, strategy=Strategy(kind=OnConflict.KEEP))
    result = engine.merge(MergeRequest())
    assert result.exit_code == 0

    assert captured, "engine never called TransactionStore.write() during merge"
    marker = captured[0]
    for live_path, expected_hash in pre_hash_by_path.items():
        assert live_path in marker.partial_owned_hashes, (
            f"marker missing partial-owned entry for {live_path}; "
            f"got keys={list(marker.partial_owned_hashes)}"
        )
        assert marker.partial_owned_hashes[live_path] == expected_hash, (
            f"marker hash mismatch for {live_path}: "
            f"expected {expected_hash}, got {marker.partial_owned_hashes[live_path]}"
        )
