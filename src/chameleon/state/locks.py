"""Partial-ownership write discipline."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path

from chameleon.io.json import dump_json


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def _flock(path: Path) -> Iterator[None]:
    target = path if path.exists() else path.parent
    target_fd = target.open("ab" if path.exists() else "rb")
    try:
        fcntl.flock(target_fd.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(target_fd.fileno(), fcntl.LOCK_UN)
    finally:
        target_fd.close()


def partial_owned_write(
    path: Path,
    *,
    owned_keys: frozenset[str],
    update: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    """Update `path` (a JSON file) with read-modify-write discipline that
    preserves any keys outside `owned_keys` if a concurrent writer touched
    them. Caller's `update` may mutate any key; we strip non-owned-key
    changes so on-disk values for unowned keys win.
    """
    pre_bytes = path.read_bytes() if path.exists() else b"{}"
    pre_hash = _sha256(pre_bytes)

    with _flock(path):
        cur_bytes = path.read_bytes() if path.exists() else b"{}"
        cur_hash = _sha256(cur_bytes)

        cur_obj = json.loads(cur_bytes) if cur_bytes else {}
        if not isinstance(cur_obj, dict):
            cur_obj = {}

        # If concurrent modification happened, cur_obj already reflects the
        # latest on-disk state; we layer owned-key updates on top.
        _ = pre_hash, cur_hash  # diagnostic info; logging would consume here

        proposed = update(dict(cur_obj))

        merged: dict[str, object] = dict(cur_obj)
        for k in owned_keys:
            if k in proposed:
                merged[k] = proposed[k]
            elif k in merged:
                del merged[k]

        tmp = path.with_suffix(path.suffix + ".tmp")
        # Route through io.json.dump_json so the partial-owned-write path
        # honours the same `ensure_ascii=False` contract as full-owned
        # writes — non-ASCII content (em-dashes, smart quotes, emoji,
        # multilingual user content) survives round-trip.
        tmp.write_text(dump_json(merged, indent=2), encoding="utf-8")
        tmp.replace(path)


__all__ = ["partial_owned_write"]
