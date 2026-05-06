"""B2 regression — capabilities reconciler must produce stable, sorted output.

Prior to the fix, ``reconcile_plugins`` built the per-target union via
plain ``dict.setdefault`` / iteration, so the result key order tracked
the insertion order of whichever target the engine happened to read
first. A second ``chameleon merge --on-conflict=keep`` run could then
re-read the targets in a different order and produce byte-different
files even though no semantic content changed.

The fix is to sort the produced union (and the disagreement list) by
key so the output is order-independent of the per-target input ordering.

This test is the unit-level analogue of the smoke test
``test_keep_merge_is_byte_idempotent``: both fail on main; both pass
after the fix.
"""

from __future__ import annotations

from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.capabilities import PluginEntry, reconcile_plugins


def _entries(*keys: str) -> dict[str, PluginEntry]:
    return {k: PluginEntry(enabled=True) for k in keys}


def test_reconcile_plugins_union_is_sorted_by_key() -> None:
    """Union is sorted by key regardless of per-target input order."""

    claude_keys = ("zeta@m", "alpha@m", "mike@m")
    codex_keys = ("alpha@m", "bravo@m", "yankee@m")

    union, _ = reconcile_plugins(
        {
            BUILTIN_CLAUDE: _entries(*claude_keys),
            BUILTIN_CODEX: _entries(*codex_keys),
        }
    )

    expected_order = sorted(set(claude_keys) | set(codex_keys))
    assert list(union.keys()) == expected_order, "reconcile_plugins union must be sorted by key"


def test_reconcile_plugins_independent_of_per_target_iteration_order() -> None:
    """Calling the reconciler with different per-target *insertion* orders
    yields the SAME output (key order and values).
    """

    keys = ("ripvec@example-user-plugins", "alpha@m", "tracemeld@example-user-plugins")

    claude_a = _entries(*keys)
    codex_a = _entries(*reversed(keys))

    claude_b = _entries(*reversed(keys))
    codex_b = _entries(*keys)

    union_a, _ = reconcile_plugins({BUILTIN_CLAUDE: claude_a, BUILTIN_CODEX: codex_a})
    union_b, _ = reconcile_plugins({BUILTIN_CLAUDE: claude_b, BUILTIN_CODEX: codex_b})

    assert list(union_a.keys()) == list(union_b.keys()), (
        "reconcile_plugins must produce the same key order regardless of per-target insertion order"
    )
    # And both are sorted.
    assert list(union_a.keys()) == sorted(union_a.keys())


def test_reconcile_plugins_disagreements_sorted_by_key() -> None:
    """Disagreement records are produced in sorted order, too."""

    # Three keys disagree across targets; one only-in-claude (no disagreement).
    claude = {
        "zeta@m": PluginEntry(enabled=True),
        "alpha@m": PluginEntry(enabled=False),
        "mike@m": PluginEntry(enabled=True),
        "claude-only@m": PluginEntry(enabled=True),
    }
    codex = {
        "zeta@m": PluginEntry(enabled=False),
        "alpha@m": PluginEntry(enabled=True),
        "mike@m": PluginEntry(enabled=False),
    }

    _, disagreements = reconcile_plugins({BUILTIN_CLAUDE: claude, BUILTIN_CODEX: codex})

    keys_in_order = [d.plugin_key for d in disagreements]
    assert keys_in_order == sorted(keys_in_order), (
        "PluginDisagreement records must be emitted in sorted key order"
    )
    # And we got exactly the three disagreeing keys (claude-only excluded).
    assert set(keys_in_order) == {"zeta@m", "alpha@m", "mike@m"}
