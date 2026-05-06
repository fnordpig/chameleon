"""Typed merge transactions and recovery detection (§4.6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import TargetId
from chameleon.io.toml import dump_toml, load_toml


class MergeTransaction(BaseModel):
    """Persistent record of an in-flight merge.

    Written before the merge writes any live target file and removed
    after `neutral.lkg.yaml` is updated. Persisted markers indicate
    interruption; doctor surfaces them.
    """

    model_config = ConfigDict(frozen=True)

    merge_id: str
    started_at: datetime
    target_ids: list[TargetId]
    neutral_lkg_hash_after: str
    partial_owned_hashes: dict[str, str] = Field(default_factory=dict)


def transaction_id() -> str:
    return str(uuid.uuid4())


class TransactionStore:
    def __init__(self, dir_path: Path) -> None:
        self.dir = dir_path

    def write(self, tx: MergeTransaction) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{tx.merge_id}.toml"
        path.write_text(dump_toml(tx.model_dump(mode="json")), encoding="utf-8")

    def entries(self) -> list[MergeTransaction]:
        if not self.dir.exists():
            return []
        out: list[MergeTransaction] = []
        for path in sorted(self.dir.glob("*.toml")):
            raw = load_toml(path)
            out.append(MergeTransaction.model_validate(dict(raw)))
        return out

    def clear(self, merge_id: str) -> None:
        path = self.dir / f"{merge_id}.toml"
        if path.exists():
            path.unlink()


__all__ = ["MergeTransaction", "TransactionStore", "transaction_id"]
