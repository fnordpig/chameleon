"""Resolution-memory helpers: hashing, path parsing, GC.

The resolution-memory spec records operator conflict decisions
keyed by ``FieldPath.render()``-with-discriminators (the same string
``ChangeRecord.render_path()`` produces). On the next merge the engine
recomputes a stable hash over ``(n0, n1, per_target)`` and silently
re-applies the decision iff the hash matches. This module owns the
hash, the round-trip path key, and the GC walk.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import NamedTuple

from chameleon._types import FieldPath, TargetId
from chameleon.merge.changeset import ChangeRecord, _serialize


def render_change_path(record: ChangeRecord) -> str:
    """The persisted-resolution key for a ``ChangeRecord``.

    This is exactly ``record.render_path()`` — a stable string that
    includes any per-key discriminator (``[<TargetId>]`` for
    ``dict[TargetId, V]`` leaves, ``[<dict_key>]`` for ``dict[str, V]``
    leaves). Centralising the helper here so the engine and the CLI
    agree on the key shape.
    """
    return record.render_path()


# ``FieldPath.render()`` uses dotted segments; ``render_path()`` appends
# a single ``[<discriminator>]`` for keyed-dict leaves. The parser below
# round-trips that shape — accepting both bare paths and bracketed
# discriminators — so the engine can rehydrate per-key resolution
# entries from their stored string keys.
_BRACKET_RE = re.compile(r"^(?P<base>[^\[]+)(?:\[(?P<key>[^\]]+)\])?$")


class ParsedResolutionKey(NamedTuple):
    """The structured form of a persisted-resolution key.

    Mirrors the (path, target_key, dict_key) trio on ``ChangeRecord``;
    ``target_key`` is set when the discriminator names a registered
    ``TargetId``, ``dict_key`` otherwise. A bare key (no brackets) has
    both as ``None``.
    """

    path: FieldPath
    target_key: TargetId | None
    dict_key: str | None


def parse_resolution_key(key: str) -> ParsedResolutionKey:
    """Parse a ``render_path()``-style key back into structured form.

    The discriminator inside ``[...]`` is interpreted as a ``TargetId``
    if it parses as one (i.e. it is a registered target name); otherwise
    it's treated as a ``dict[str, V]`` key. The walker emits exactly
    one of the two per record, so the same exclusivity is preserved on
    parse.
    """
    match = _BRACKET_RE.match(key)
    if match is None:
        msg = f"unparseable resolution key: {key!r}"
        raise ValueError(msg)
    base = match.group("base")
    discriminator = match.group("key")
    segments = tuple(base.split("."))
    path = FieldPath(segments=segments)
    if discriminator is None:
        return ParsedResolutionKey(path=path, target_key=None, dict_key=None)
    # Treat the discriminator as a TargetId when registered, else as a
    # raw dict key. Construction failure (unregistered name) is the
    # signal "this is a dict_key, not a target_key."
    try:
        tid = TargetId(value=discriminator)
        return ParsedResolutionKey(path=path, target_key=tid, dict_key=None)
    except ValueError:
        return ParsedResolutionKey(path=path, target_key=None, dict_key=discriminator)


def compute_decision_hash(record: ChangeRecord) -> str:
    """Stable invalidation hash over ``(n0, n1, per_target)``.

    Uses ``_serialize`` (already used by the change-walker) so the hash
    is computed over the same JSON-mode shape the classifier compares.
    Sorted-keys + UTF-8 + sha256 gives a deterministic hex digest the
    engine can compare across runs.
    """
    payload = {
        "n0": _serialize(record.n0),
        "n1": _serialize(record.n1),
        "per_target": {tid.value: _serialize(v) for tid, v in record.per_target.items()},
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ParsedResolutionKey",
    "compute_decision_hash",
    "parse_resolution_key",
    "render_change_path",
]
