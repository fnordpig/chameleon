"""P2-1: per-FieldPath classification with `dict[TargetId, V]` semantics.

Today the merge engine classifies at *domain* granularity. For
`dict[TargetId, V]` fields like ``identity.model``, that produces a
false conflict on every re-merge: each target's reverse codec only
populates its own key (``{claude: ...}`` vs ``{codex: ...}``), and the
two partial dicts disagree with each other and with the composed
multi-target neutral, so the entire ``identity`` domain pseudo-conflicts
even though there is no real disagreement.

These tests pin the fix:

  * ``walk_changes(n0, n1, per_target_neutrals)`` walks the neutral
    schema field-by-field, producing one ``ChangeRecord`` per leaf.
  * For ``dict[TargetId, V]`` fields the walker emits one record per
    TargetId key, scoped to that target's own evidence (other targets
    contribute nothing for that key — they don't speak for it).
  * Backwards compat: scalar fields (e.g. ``identity.reasoning_effort``)
    still produce exactly one record.
"""

from __future__ import annotations

from chameleon._types import TargetId
from chameleon.merge.changeset import (
    ChangeOutcome,
    classify_change,
    walk_changes,
)
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.identity import Identity, ReasoningEffort
from chameleon.schema.neutral import Neutral


def _record_at(records, *segments: str, target_key: TargetId | None = None):
    """Return the unique record matching the given (path, target_key) tuple."""
    matching = [r for r in records if r.path.segments == segments and r.target_key == target_key]
    assert len(matching) == 1, (
        f"expected exactly one record at segments={segments!r} "
        f"target_key={target_key!r}; got {len(matching)} of {len(records)} total"
    )
    return matching[0]


# ---------------------------------------------------------------------
# The headline scenario from the parity-gap doc.
# ---------------------------------------------------------------------


def test_dict_targetid_field_no_false_conflict() -> None:
    """The exact false-conflict scenario the parity-gap doc calls out.

    n0 = {} (no model), n1 = {model: {claude: X, codex: Y}},
    per_target = {claude: {model: {claude: X}}, codex: {model: {codex: Y}}}.

    Each TargetId key is owned by exactly one target's reverse codec.
    Domain-granularity classification false-conflicted; per-FieldPath
    classification must see two CONSENSUAL records, one per key.
    """
    n0 = Neutral(schema_version=1)
    n1 = Neutral(
        schema_version=1,
        identity=Identity(
            model={BUILTIN_CLAUDE: "claude-sonnet-4-7", BUILTIN_CODEX: "gpt-5.4"},
        ),
    )
    per_target = {
        BUILTIN_CLAUDE: Neutral(
            schema_version=1,
            identity=Identity(model={BUILTIN_CLAUDE: "claude-sonnet-4-7"}),
        ),
        BUILTIN_CODEX: Neutral(
            schema_version=1,
            identity=Identity(model={BUILTIN_CODEX: "gpt-5.4"}),
        ),
    }

    records = walk_changes(n0, n1, per_target)

    # Two records under identity.model — one per target key.
    claude_rec = _record_at(records, "identity", "model", target_key=BUILTIN_CLAUDE)
    codex_rec = _record_at(records, "identity", "model", target_key=BUILTIN_CODEX)

    # Each is consensual: neutral and the owning target agree, and other
    # targets contribute no evidence (they cannot speak for this key).
    cl_claude = classify_change(claude_rec)
    cl_codex = classify_change(codex_rec)
    assert cl_claude.outcome is ChangeOutcome.CONSENSUAL, (
        f"expected CONSENSUAL on identity.model[claude]; got {cl_claude.outcome!r}"
    )
    assert cl_codex.outcome is ChangeOutcome.CONSENSUAL
    assert cl_claude.resolved_value == "claude-sonnet-4-7"
    assert cl_codex.resolved_value == "gpt-5.4"


# ---------------------------------------------------------------------
# Real conflict case: the conflict surfaces at the *key*, not the domain.
# ---------------------------------------------------------------------


def test_real_conflict_isolated_to_one_key() -> None:
    """When Claude disagrees with neutral on its own model entry, the
    conflict surfaces only on `identity.model[claude]` — not on the
    whole identity domain, and not on `identity.model[codex]`."""
    n0 = Neutral(schema_version=1)
    n1 = Neutral(
        schema_version=1,
        identity=Identity(
            model={BUILTIN_CLAUDE: "claude-sonnet-4-7", BUILTIN_CODEX: "gpt-5.4"},
        ),
    )
    per_target = {
        # Claude's live file says something different than neutral.
        BUILTIN_CLAUDE: Neutral(
            schema_version=1,
            identity=Identity(model={BUILTIN_CLAUDE: "claude-opus-4-7"}),
        ),
        BUILTIN_CODEX: Neutral(
            schema_version=1,
            identity=Identity(model={BUILTIN_CODEX: "gpt-5.4"}),
        ),
    }

    records = walk_changes(n0, n1, per_target)

    claude_rec = _record_at(records, "identity", "model", target_key=BUILTIN_CLAUDE)
    codex_rec = _record_at(records, "identity", "model", target_key=BUILTIN_CODEX)

    assert classify_change(claude_rec).outcome is ChangeOutcome.CONFLICT
    assert classify_change(codex_rec).outcome is ChangeOutcome.CONSENSUAL


def test_two_keys_consensually_change_independently() -> None:
    """Both targets agree with neutral on their respective keys."""
    n0 = Neutral(
        schema_version=1,
        identity=Identity(
            model={BUILTIN_CLAUDE: "claude-old", BUILTIN_CODEX: "gpt-old"},
        ),
    )
    n1 = Neutral(
        schema_version=1,
        identity=Identity(
            model={BUILTIN_CLAUDE: "claude-new", BUILTIN_CODEX: "gpt-new"},
        ),
    )
    per_target = {
        BUILTIN_CLAUDE: Neutral(
            schema_version=1,
            identity=Identity(model={BUILTIN_CLAUDE: "claude-new"}),
        ),
        BUILTIN_CODEX: Neutral(
            schema_version=1,
            identity=Identity(model={BUILTIN_CODEX: "gpt-new"}),
        ),
    }

    records = walk_changes(n0, n1, per_target)
    claude_rec = _record_at(records, "identity", "model", target_key=BUILTIN_CLAUDE)
    codex_rec = _record_at(records, "identity", "model", target_key=BUILTIN_CODEX)

    cl_claude = classify_change(claude_rec)
    cl_codex = classify_change(codex_rec)
    assert cl_claude.outcome is ChangeOutcome.CONSENSUAL
    assert cl_codex.outcome is ChangeOutcome.CONSENSUAL
    assert cl_claude.resolved_value == "claude-new"
    assert cl_codex.resolved_value == "gpt-new"


# ---------------------------------------------------------------------
# Backwards compat: scalars still produce a single record per leaf.
# ---------------------------------------------------------------------


def test_scalar_field_classifies_at_leaf() -> None:
    """`identity.reasoning_effort` is a scalar; one record at that path."""
    n0 = Neutral(schema_version=1)
    n1 = Neutral(
        schema_version=1,
        identity=Identity(reasoning_effort=ReasoningEffort.HIGH),
    )
    per_target = {
        BUILTIN_CLAUDE: Neutral(
            schema_version=1,
            identity=Identity(reasoning_effort=ReasoningEffort.HIGH),
        ),
        BUILTIN_CODEX: Neutral(
            schema_version=1,
            identity=Identity(reasoning_effort=ReasoningEffort.HIGH),
        ),
    }

    records = walk_changes(n0, n1, per_target)
    rec = _record_at(records, "identity", "reasoning_effort")
    cl = classify_change(rec)
    assert cl.outcome is ChangeOutcome.CONSENSUAL
    # Serialized form (Enum -> .value) is what the classifier compares on.
    assert cl.resolved_value == "high"


def test_nested_dict_targetid_field_under_endpoint() -> None:
    """`identity.endpoint.base_url` is a nested `dict[TargetId, str]`.

    Confirms the walker descends into nested Pydantic models before
    splitting on TargetId keys (not just top-level domain fields).
    """
    n0 = Neutral(schema_version=1)
    n1 = Neutral(
        schema_version=1,
        identity=Identity(),
    )
    n1.identity.endpoint.base_url = {BUILTIN_CLAUDE: "https://claude.ai"}

    per_target = {
        BUILTIN_CLAUDE: Neutral(schema_version=1),
        BUILTIN_CODEX: Neutral(schema_version=1),
    }
    per_target[BUILTIN_CLAUDE].identity.endpoint.base_url = {BUILTIN_CLAUDE: "https://claude.ai"}

    records = walk_changes(n0, n1, per_target)
    rec = _record_at(records, "identity", "endpoint", "base_url", target_key=BUILTIN_CLAUDE)
    assert classify_change(rec).outcome is ChangeOutcome.CONSENSUAL


def test_unchanged_domain_emits_no_records() -> None:
    """An entirely unchanged tree should produce an empty list."""
    n0 = Neutral(
        schema_version=1,
        identity=Identity(reasoning_effort=ReasoningEffort.MEDIUM),
    )
    n1 = Neutral(
        schema_version=1,
        identity=Identity(reasoning_effort=ReasoningEffort.MEDIUM),
    )
    per_target = {
        BUILTIN_CLAUDE: Neutral(
            schema_version=1,
            identity=Identity(reasoning_effort=ReasoningEffort.MEDIUM),
        ),
        BUILTIN_CODEX: Neutral(
            schema_version=1,
            identity=Identity(reasoning_effort=ReasoningEffort.MEDIUM),
        ),
    }
    records = walk_changes(n0, n1, per_target)
    # Either no records, or every record classifies UNCHANGED.
    for rec in records:
        assert classify_change(rec).outcome is ChangeOutcome.UNCHANGED, (
            f"unexpected change at {rec.path.render()} target_key={rec.target_key!r}: "
            f"n0={rec.n0!r} n1={rec.n1!r} per_target={rec.per_target!r}"
        )
