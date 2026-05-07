"""``authorization.reviewer`` round-trip + cross-codec parity.

Codex's ``approvals_reviewer`` (one of ``user`` / ``auto_review`` /
``guardian_subagent``) is promoted to a neutral ``authorization.reviewer``
field. Claude has no in-config equivalent, so the Claude codec must emit
a ``LossWarning`` referencing P1-G whenever this neutral field is set.

Tests in this module:

* round-trip of every documented vocabulary value through the Codex codec
* an unrecognized wire string at the section level → LossWarning + drop
* end-to-end: disassembling the exemplar populates ``authorization.reviewer``
  with the expected enum member (assembler-routing regression)
* Claude side: setting ``authorization.reviewer`` and encoding to Claude
  emits a single P1-G-tagged ``LossWarning``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.codecs.claude.authorization import ClaudeAuthorizationCodec
from chameleon.codecs.codex._generated import ApprovalsReviewer
from chameleon.codecs.codex.authorization import (
    CodexAuthorizationCodec,
    CodexAuthorizationSection,
)
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains
from chameleon.schema.authorization import Authorization, Reviewer
from chameleon.targets.codex.assembler import CodexAssembler

FIXTURE_HOME = Path(__file__).parent.parent / "fixtures" / "exemplar" / "home"


@pytest.mark.parametrize("reviewer", list(Reviewer))
def test_codex_round_trip_each_reviewer_value(reviewer: Reviewer) -> None:
    """Every documented vocabulary value survives a full to/from cycle.

    The neutral enum mirrors the upstream ``ApprovalsReviewer`` byte-for-byte;
    if a future schema regen drops or renames a member, this test fails
    explicitly rather than silently dropping data.
    """
    orig = Authorization(reviewer=reviewer)
    ctx = TranspileCtx()
    section = CodexAuthorizationCodec.to_target(orig, ctx)
    # Wire string MUST equal the upstream enum's value, not the Python
    # member name — we serialize against the Codex TOML vocabulary, not
    # the neutral one.
    assert section.approvals_reviewer is not None
    assert section.approvals_reviewer == ApprovalsReviewer(reviewer.value).value
    restored = CodexAuthorizationCodec.from_target(section, ctx)
    assert restored.reviewer is reviewer
    assert ctx.warnings == [], (
        f"clean round-trip should not warn; got {[w.message for w in ctx.warnings]}"
    )


def test_codex_round_trip_empty_reviewer() -> None:
    """Unset neutral reviewer → unset wire field → unset round-trip."""
    ctx = TranspileCtx()
    section = CodexAuthorizationCodec.to_target(Authorization(), ctx)
    assert section.approvals_reviewer is None
    restored = CodexAuthorizationCodec.from_target(section, ctx)
    assert restored.reviewer is None


def test_codex_unknown_wire_value_warns_and_drops() -> None:
    """An ``approvals_reviewer`` string outside the documented vocabulary
    must trigger a typed P1-G LossWarning (so the operator sees the loss
    in the merge banner) and produce a neutral with ``reviewer is None``
    (so we never silently invent a vocabulary member).
    """
    section = CodexAuthorizationSection(approvals_reviewer="not-a-real-reviewer")
    ctx = TranspileCtx()
    auth = CodexAuthorizationCodec.from_target(section, ctx)
    assert auth.reviewer is None
    reviewer_warnings = [
        w
        for w in ctx.warnings
        if isinstance(w, LossWarning)
        and w.target == BUILTIN_CODEX
        and w.domain is Domains.AUTHORIZATION
        and "approvals_reviewer" in w.message
    ]
    assert len(reviewer_warnings) == 1, (
        "expected exactly one approvals_reviewer LossWarning for the unknown "
        f"wire value; got {[w.message for w in ctx.warnings]}"
    )
    assert "not-a-real-reviewer" in reviewer_warnings[0].message


def test_exemplar_disassemble_populates_reviewer() -> None:
    """End-to-end assembler regression: the exemplar's
    ``approvals_reviewer = "guardian_subagent"`` must survive disassemble
    routing into the authorization domain (not pass-through) AND map to
    ``Reviewer.GUARDIAN_SUBAGENT`` after the codec runs.

    This is the wiring-gap test — a codec change with no assembler-side
    ``authorization_keys`` update would silently send the field to
    pass-through and the neutral side would never learn the value. Mirrors
    the analogous P1-A / P1-D end-to-end checks in
    ``tests/integration/test_exemplar_disassemble.py``.
    """
    config_bytes = (FIXTURE_HOME / "_codex" / "config.toml").read_bytes()
    domains, passthrough = CodexAssembler.disassemble({CodexAssembler.CONFIG_TOML: config_bytes})
    assert "approvals_reviewer" not in passthrough, (
        "``approvals_reviewer`` must be claimed by the authorization "
        "codec, not leaked to pass-through"
    )
    auth_section = domains.get(Domains.AUTHORIZATION)
    assert isinstance(auth_section, CodexAuthorizationSection)
    assert auth_section.approvals_reviewer == "guardian_subagent"
    # And the codec resolves it to the typed neutral enum.
    ctx = TranspileCtx()
    auth = CodexAuthorizationCodec.from_target(auth_section, ctx)
    assert auth.reviewer is Reviewer.GUARDIAN_SUBAGENT


def test_claude_emits_p1g_loss_warning_when_reviewer_set() -> None:
    """Claude has no in-config approvals-reviewer concept, so encoding a
    neutral with ``reviewer`` set must emit a typed P1-G LossWarning. The
    Claude target_section must remain reviewer-free (no smuggling).
    """
    orig = Authorization(reviewer=Reviewer.GUARDIAN_SUBAGENT)
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(orig, ctx)
    # The target_section model has no reviewer field; encoding must NOT
    # gain one as a side effect of P1-G.
    assert "reviewer" not in section.model_dump()
    reviewer_warnings = [
        w
        for w in ctx.warnings
        if isinstance(w, LossWarning)
        and w.target == BUILTIN_CLAUDE
        and w.domain is Domains.AUTHORIZATION
        and "reviewer" in w.message
    ]
    assert len(reviewer_warnings) == 1, (
        "expected exactly one reviewer LossWarning on the Claude side; "
        f"got {[w.message for w in ctx.warnings]}"
    )
    # And the value should appear in the message so the operator can
    # actually see what was dropped.
    assert "guardian_subagent" in reviewer_warnings[0].message


def test_claude_no_warning_when_reviewer_unset() -> None:
    """Sanity check that the new branch only fires when the field is set —
    no spurious warnings on any default Authorization."""
    ctx = TranspileCtx()
    ClaudeAuthorizationCodec.to_target(Authorization(), ctx)
    p1g_warnings = [w for w in ctx.warnings if isinstance(w, LossWarning) and "P1-G" in w.message]
    assert p1g_warnings == []
