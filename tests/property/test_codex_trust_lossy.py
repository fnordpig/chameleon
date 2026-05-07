"""A-TRUST — Codex governance Trust list lossy-collapse warnings.

Wave-9 Agent A documented two cases where the neutral ``Trust`` schema
can express information that the Codex wire shape physically cannot
represent:

* **Duplicate paths within a single list.** Codex stores trust as
  ``projects.<path>.trust_level`` — a ``dict`` keyed by path string,
  so a second occurrence of the same path collapses onto the first
  wire slot (``Trust.duplicate_paths``).
* **The same path in both ``trusted_paths`` and ``untrusted_paths``.**
  A wire ``projects.<path>`` row holds exactly one ``trust_level``;
  whichever list the codec serialises last clobbers the other
  (``Trust.both_trusted_and_untrusted``).

Both are documented-lossy axes that the round-trip orientation in
``CLAUDE.md`` requires us to surface via :class:`LossWarning` rather
than silently drop. These tests pin the warning contracts so a future
refactor cannot accidentally degrade them back to silent collapse.
"""

from __future__ import annotations

from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.codecs.codex.governance import CodexGovernanceCodec
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.governance import Governance, Trust


def _atrust_warnings(ctx: TranspileCtx) -> list[LossWarning]:
    """Filter ``ctx.warnings`` to the A-TRUST-tagged subset."""
    return [
        w
        for w in ctx.warnings
        if isinstance(w, LossWarning)
        and w.target == BUILTIN_CODEX
        and w.domain is Domains.GOVERNANCE
        and "A-TRUST" in w.message
    ]


def test_clean_trust_lists_emit_no_atrust_warning() -> None:
    """Sanity check — non-pathological ``Trust`` lists must not warn.

    The lossy-detection branch is deliberately additive: a clean input
    (no duplicates within a list, no path in both lists) must produce
    zero A-TRUST LossWarnings, otherwise every well-formed merge would
    spam the operator.
    """
    model = Governance.model_construct(
        trust=Trust.model_construct(
            trusted_paths=["/srv/a", "/srv/b"],
            untrusted_paths=["/srv/c"],
        )
    )
    ctx = TranspileCtx()
    CodexGovernanceCodec.to_target(model, ctx)
    assert _atrust_warnings(ctx) == []


def test_duplicate_within_trusted_paths_warns() -> None:
    """A path repeated inside ``trusted_paths`` collapses to a single
    wire key — the codec must emit exactly one
    ``Trust.duplicate_paths`` warning naming the offending path."""
    model = Governance.model_construct(
        trust=Trust.model_construct(trusted_paths=["/srv/a", "/srv/a", "/srv/b"]),
    )
    ctx = TranspileCtx()
    CodexGovernanceCodec.to_target(model, ctx)
    dup_warnings = [w for w in _atrust_warnings(ctx) if "duplicate_paths" in w.message]
    assert len(dup_warnings) == 1, (
        f"expected exactly one Trust.duplicate_paths warning; "
        f"got {[w.message for w in ctx.warnings]}"
    )
    assert "/srv/a" in dup_warnings[0].message


def test_duplicate_within_untrusted_paths_warns() -> None:
    """Symmetric to the trusted case — an untrusted-side duplicate
    must also surface the collapse, with the path named."""
    model = Governance.model_construct(
        trust=Trust.model_construct(untrusted_paths=["/srv/x", "/srv/x"]),
    )
    ctx = TranspileCtx()
    CodexGovernanceCodec.to_target(model, ctx)
    dup_warnings = [w for w in _atrust_warnings(ctx) if "duplicate_paths" in w.message]
    assert len(dup_warnings) == 1
    assert "/srv/x" in dup_warnings[0].message


def test_path_in_both_lists_warns_and_untrusted_wins() -> None:
    """A path in both ``trusted_paths`` and ``untrusted_paths`` produces
    a single ``Trust.both_trusted_and_untrusted`` warning AND, because
    the codec serialises ``untrusted_paths`` second, the wire row's
    ``trust_level`` is ``"untrusted"`` (last-write-wins). Both halves
    of the contract — the warning and the deterministic winner — are
    pinned here so an accidental reorder of the encode loops can't
    silently change the answer."""
    model = Governance.model_construct(
        trust=Trust.model_construct(
            trusted_paths=["/srv/conflict"], untrusted_paths=["/srv/conflict"]
        ),
    )
    ctx = TranspileCtx()
    section = CodexGovernanceCodec.to_target(model, ctx)
    both_warnings = [w for w in _atrust_warnings(ctx) if "both_trusted_and_untrusted" in w.message]
    assert len(both_warnings) == 1, (
        f"expected exactly one Trust.both_trusted_and_untrusted warning; "
        f"got {[w.message for w in ctx.warnings]}"
    )
    assert "/srv/conflict" in both_warnings[0].message
    assert section.projects["/srv/conflict"].trust_level == "untrusted"


def test_duplicate_and_both_emit_distinct_warnings() -> None:
    """When BOTH lossy categories trigger on the same input, the codec
    must emit two distinct ``LossWarning`` instances (one per category)
    so the operator can tell them apart in the merge banner. Collapsing
    them into a single combined warning would hide one axis of loss."""
    model = Governance.model_construct(
        trust=Trust.model_construct(
            trusted_paths=["/srv/a", "/srv/a"],
            untrusted_paths=["/srv/a"],
        ),
    )
    ctx = TranspileCtx()
    CodexGovernanceCodec.to_target(model, ctx)
    atrust = _atrust_warnings(ctx)
    dup = [w for w in atrust if "duplicate_paths" in w.message]
    both = [w for w in atrust if "both_trusted_and_untrusted" in w.message]
    assert len(dup) == 1, (
        f"expected one Trust.duplicate_paths warning; got {[w.message for w in atrust]}"
    )
    assert len(both) == 1, (
        f"expected one Trust.both_trusted_and_untrusted warning; got {[w.message for w in atrust]}"
    )
    # And they are physically distinct LossWarning instances.
    assert dup[0] is not both[0]


def test_duplicates_across_both_lists_only_warns_once_per_category() -> None:
    """Multiple duplicate paths across the two lists still collapse to
    one ``Trust.duplicate_paths`` warning (carrying both offenders) and
    one ``Trust.both_trusted_and_untrusted`` warning if the same path
    happens to appear in both — emitting one warning per offending path
    would scale linearly with config size and drown the operator."""
    model = Governance.model_construct(
        trust=Trust.model_construct(
            trusted_paths=["/srv/a", "/srv/a", "/srv/b", "/srv/b"],
            untrusted_paths=["/srv/c", "/srv/c"],
        ),
    )
    ctx = TranspileCtx()
    CodexGovernanceCodec.to_target(model, ctx)
    dup = [w for w in _atrust_warnings(ctx) if "duplicate_paths" in w.message]
    assert len(dup) == 1
    # All three offending paths must be named in the single message.
    assert "/srv/a" in dup[0].message
    assert "/srv/b" in dup[0].message
    assert "/srv/c" in dup[0].message
