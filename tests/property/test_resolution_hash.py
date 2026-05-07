"""Invalidation-hash properties (Wave-15 §1).

The hash captures ``(n0, n1, per_target)``. Two records with the same
shape produce the same hash; perturbing any of the three inputs changes
the hash. The hash is the engine's invalidation key for stored
resolutions — these tests pin its determinism + sensitivity.
"""

from __future__ import annotations

from typing import Any

from chameleon._types import FieldPath, TargetId
from chameleon.merge.changeset import ChangeRecord
from chameleon.merge.resolutions import (
    compute_decision_hash,
    parse_resolution_key,
    render_change_path,
)
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains


def _record(
    *,
    n0: object = "medium",
    n1: object = "high",
    per_target: dict[TargetId, Any] | None = None,
) -> ChangeRecord:
    pt: dict[TargetId, Any] = per_target or {
        BUILTIN_CLAUDE: "low",
        BUILTIN_CODEX: "minimal",
    }
    return ChangeRecord(
        domain=Domains.IDENTITY,
        path=FieldPath(segments=("identity", "reasoning_effort")),
        n0=n0,
        n1=n1,
        per_target=pt,
    )


def test_same_record_produces_same_hash() -> None:
    h1 = compute_decision_hash(_record())
    h2 = compute_decision_hash(_record())
    assert h1 == h2


def test_different_n1_produces_different_hash() -> None:
    base = compute_decision_hash(_record(n1="high"))
    other = compute_decision_hash(_record(n1="xhigh"))
    assert base != other


def test_different_n0_produces_different_hash() -> None:
    base = compute_decision_hash(_record(n0="medium"))
    other = compute_decision_hash(_record(n0="low"))
    assert base != other


def test_different_per_target_produces_different_hash() -> None:
    base = compute_decision_hash(_record())
    other = compute_decision_hash(
        _record(per_target={BUILTIN_CLAUDE: "low", BUILTIN_CODEX: "high"})
    )
    assert base != other


def test_per_target_ordering_independent() -> None:
    """Hash must be insensitive to per_target dict insertion order."""
    a = compute_decision_hash(_record(per_target={BUILTIN_CLAUDE: "low", BUILTIN_CODEX: "minimal"}))
    b = compute_decision_hash(_record(per_target={BUILTIN_CODEX: "minimal", BUILTIN_CLAUDE: "low"}))
    assert a == b


def test_render_change_path_round_trips_via_parser() -> None:
    """``render_change_path`` and ``parse_resolution_key`` must round-trip."""
    plain = ChangeRecord(
        domain=Domains.IDENTITY,
        path=FieldPath(segments=("identity", "reasoning_effort")),
        n0=None,
        n1=None,
        per_target={},
    )
    keyed_target = ChangeRecord(
        domain=Domains.IDENTITY,
        path=FieldPath(segments=("identity", "model")),
        n0=None,
        n1=None,
        per_target={},
        target_key=BUILTIN_CLAUDE,
    )
    keyed_dict = ChangeRecord(
        domain=Domains.CAPABILITIES,
        path=FieldPath(segments=("capabilities", "plugin_marketplaces")),
        n0=None,
        n1=None,
        per_target={},
        dict_key="archivium-marketplace",
    )

    for rec in (plain, keyed_target, keyed_dict):
        key = render_change_path(rec)
        parsed = parse_resolution_key(key)
        assert parsed.path == rec.path
        assert parsed.target_key == rec.target_key
        assert parsed.dict_key == rec.dict_key
