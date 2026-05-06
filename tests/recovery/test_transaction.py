from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.state.transaction import (
    MergeTransaction,
    TransactionStore,
    transaction_id,
)

pytestmark = pytest.mark.recovery


def test_transaction_id_is_uuid_string() -> None:
    tx = transaction_id()
    assert len(tx) == 36  # uuid4 hex with hyphens


def test_round_trip(tmp_path: Path) -> None:
    store = TransactionStore(tmp_path)
    tx = MergeTransaction(
        merge_id=transaction_id(),
        started_at=datetime.now(tz=UTC),
        target_ids=[BUILTIN_CLAUDE, BUILTIN_CODEX],
        neutral_lkg_hash_after="abc123",
        partial_owned_hashes={"~/.claude.json": "deadbeef"},
    )
    store.write(tx)
    listed = store.entries()
    assert len(listed) == 1
    assert listed[0].merge_id == tx.merge_id


def test_clear_removes_marker(tmp_path: Path) -> None:
    store = TransactionStore(tmp_path)
    tx = MergeTransaction(
        merge_id=transaction_id(),
        started_at=datetime.now(tz=UTC),
        target_ids=[BUILTIN_CLAUDE],
        neutral_lkg_hash_after="x",
        partial_owned_hashes={},
    )
    store.write(tx)
    store.clear(tx.merge_id)
    assert store.entries() == []
