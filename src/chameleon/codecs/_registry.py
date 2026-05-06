"""Codec registry: keyed by (TargetId, Domains); enforces no-duplicate-terminal-paths."""

from __future__ import annotations

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import Codec
from chameleon.schema._constants import Domains


class DuplicateClaimError(ValueError):
    """Raised when two codecs for the same target claim the same terminal path."""


class CodecRegistry:
    """In-memory registry. Built-in codecs register at module import; plugin
    codecs register via target plugin's class wiring.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[TargetId, Domains], Codec] = {}
        self._claimed_paths: dict[TargetId, set[FieldPath]] = {}

    def register(self, codec: Codec) -> None:
        key = (codec.target, codec.domain)
        if key in self._by_key:
            existing = self._by_key[key]
            msg = f"codec already registered for {key[0]}/{key[1].value}: {existing!r} vs {codec!r}"
            raise DuplicateClaimError(msg)

        per_target = self._claimed_paths.setdefault(codec.target, set())
        for path in codec.claimed_paths:
            if path in per_target:
                conflicting = next(
                    (
                        c
                        for c in self._by_key.values()
                        if c.target == codec.target and path in c.claimed_paths
                    ),
                    None,
                )
                msg = (
                    f"codec {codec.target}/{codec.domain.value} claims path "
                    f"{path.render()!r} which is already claimed by "
                    f"{conflicting.target if conflicting else '?'}/"
                    f"{conflicting.domain.value if conflicting else '?'}"
                )
                raise DuplicateClaimError(msg)
        per_target.update(codec.claimed_paths)
        self._by_key[key] = codec

    def get(self, target: TargetId, domain: Domains) -> Codec | None:
        return self._by_key.get((target, domain))

    def for_target(self, target: TargetId) -> list[Codec]:
        return [c for (t, _d), c in self._by_key.items() if t == target]


__all__ = ["CodecRegistry", "DuplicateClaimError"]
