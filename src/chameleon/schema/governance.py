"""governance domain — rules about rules (managed config, trust, updates).

V0: typed schema only; codecs deferred.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UpdatesChannel(Enum):
    STABLE = "stable"
    LATEST = "latest"


class Trust(BaseModel):
    """Per-path trust assertions, canonicalised to match the cross-target wire shape.

    Both targets that today consume ``Trust`` (Codex's
    ``[projects."<path>"].trust_level`` map; future wire encodings on
    Claude that key by path) represent trust-state as a path-keyed map,
    NOT as ordered lists with duplicates. The neutral schema therefore
    canonicalises on construction so ``Trust`` round-trips through any
    such target without surfacing as engine-visible drift on a re-merge:

    * Each list is deduplicated, preserving order of first occurrence.
    * If a path appears in BOTH ``trusted_paths`` and
      ``untrusted_paths``, ``untrusted_paths`` wins and the entry is
      removed from ``trusted_paths``. This matches the Codex codec's
      write order (trusted first, then untrusted overwrites the same
      ``[projects."<path>"]`` table) — picking the same precedence in
      neutral makes the engine's classify/compose/re-derive flow
      idempotent on adversarial inputs that the  state-machine
      fuzz surfaces.

    The canonicalisation is silent (no warnings emitted): operators who
    deliberately author duplicates or overlap will see the canonical
    form on the next ``chameleon merge`` regardless. The fuzz test that
    catches the idempotency violation exists precisely so this stays
    the cheapest place to enforce the invariant.
    """

    model_config = ConfigDict(extra="forbid")
    trusted_paths: list[str] = Field(default_factory=list)
    untrusted_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _canonicalise_paths(self) -> Trust:
        # Dedupe each list, preserving order of first occurrence. ``dict``
        # insertion order is the canonical "ordered set" idiom in Python
        # and keeps the canonicalisation a stable, byte-deterministic
        # operation across runs.
        untrusted = list(dict.fromkeys(self.untrusted_paths))
        untrusted_set = set(untrusted)
        # Untrusted wins on overlap (matches Codex codec write order:
        # trusted first, untrusted overwrites). Removing the overlap from
        # trusted_paths happens DURING the dedup so a path repeated in
        # trusted_paths but also present in untrusted_paths reduces to
        # zero entries on the trusted side, not one.
        trusted = [p for p in dict.fromkeys(self.trusted_paths) if p not in untrusted_set]
        # Mutate in place rather than returning a new instance: Pydantic
        # ``mode="after"`` validators run on a constructed model and the
        # canonical idiom is to assign through ``self`` so any caller
        # holding the returned model sees the canonical state.
        if trusted != self.trusted_paths:
            self.trusted_paths = trusted
        if untrusted != self.untrusted_paths:
            self.untrusted_paths = untrusted
        return self


class Updates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: UpdatesChannel | None = None
    minimum_version: str | None = None


class Governance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    managed: dict[str, str] = Field(default_factory=dict)
    trust: Trust = Field(default_factory=Trust)
    updates: Updates = Field(default_factory=Updates)
    features: dict[str, bool] = Field(default_factory=dict)


__all__ = ["Governance", "Trust", "Updates", "UpdatesChannel"]
