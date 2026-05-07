"""Wave-13 S3 — Codex authorization LCD codec.

Covers the new claims and LossWarning surfaces introduced when the LCD
schema split arrived in S1:

* ``sandbox_mode`` — losslessly round-trips for all 3 enum values, with
  the wire-side ``danger-`` prefix and hyphenation honoured.
* ``approval_policy`` — losslessly round-trips for the 4 plain-enum
  arms of upstream's ``AskForApproval`` discriminated union, again with
  hyphenation (``on-failure`` / ``on-request``) preserved on the wire.
* ``approval_policy`` granular shape (``AskForApproval4``) — Codex
  decode of a dict-shaped ``approval_policy`` value emits a typed
  LossWarning naming the pass-through escape route, doesn't crash, and
  leaves ``Authorization.approval_policy`` unset.
* ``permission_mode`` — Codex encode of a neutral ``permission_mode``
  emits a LossWarning naming the field, the cross-target asymmetry, and
  the ``sandbox_mode`` alternative; the wire section receives no
  ``permission_mode`` field as a side effect.

The pre-existing reviewer / filesystem / network / patterns surfaces
are tested in ``test_codex_reviewer_codec.py`` and the round-trip
property tests; this module only exercises the deltas.
"""

from __future__ import annotations

import pytest

from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.codecs.codex.authorization import (
    CodexAuthorizationCodec,
    CodexAuthorizationSection,
)
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.authorization import (
    ApprovalPolicy,
    Authorization,
    PermissionMode,
    SandboxMode,
)

# Wire-string expectations encoded explicitly so a future regen that
# drifts the upstream vocabulary fails here, not silently at runtime.
_SANDBOX_WIRE_BY_NEUTRAL: dict[SandboxMode, str] = {
    SandboxMode.READ_ONLY: "read-only",
    SandboxMode.WORKSPACE_WRITE: "workspace-write",
    SandboxMode.FULL_ACCESS: "danger-full-access",
}
_APPROVAL_WIRE_BY_NEUTRAL: dict[ApprovalPolicy, str] = {
    ApprovalPolicy.UNTRUSTED: "untrusted",
    ApprovalPolicy.ON_FAILURE: "on-failure",
    ApprovalPolicy.ON_REQUEST: "on-request",
    ApprovalPolicy.NEVER: "never",
}


@pytest.mark.parametrize("mode", list(SandboxMode))
def test_codex_round_trip_each_sandbox_mode_value(mode: SandboxMode) -> None:
    """Every ``SandboxMode`` member round-trips losslessly through the
    Codex codec, with the wire string honouring Codex's hyphenated
    vocabulary (in particular ``danger-full-access`` for
    ``FULL_ACCESS``)."""
    orig = Authorization(sandbox_mode=mode)
    ctx = TranspileCtx()
    section = CodexAuthorizationCodec.to_target(orig, ctx)
    assert section.sandbox_mode == _SANDBOX_WIRE_BY_NEUTRAL[mode]
    restored = CodexAuthorizationCodec.from_target(section, ctx)
    assert restored.sandbox_mode is mode
    assert ctx.warnings == [], (
        f"clean round-trip should not warn; got {[w.message for w in ctx.warnings]}"
    )


@pytest.mark.parametrize("policy", list(ApprovalPolicy))
def test_codex_round_trip_each_approval_policy_value(policy: ApprovalPolicy) -> None:
    """Every ``ApprovalPolicy`` member round-trips losslessly with the
    wire-side hyphens (``on-failure`` / ``on-request``) preserved — the
    neutral enum's underscore values must NOT leak to the wire."""
    orig = Authorization(approval_policy=policy)
    ctx = TranspileCtx()
    section = CodexAuthorizationCodec.to_target(orig, ctx)
    assert section.approval_policy == _APPROVAL_WIRE_BY_NEUTRAL[policy]
    restored = CodexAuthorizationCodec.from_target(section, ctx)
    assert restored.approval_policy is policy
    assert ctx.warnings == [], (
        f"clean round-trip should not warn; got {[w.message for w in ctx.warnings]}"
    )


def test_codex_approval_policy_unset_round_trips_clean() -> None:
    """Unset neutral ``approval_policy`` → unset wire field → unset
    round-trip; no warnings on the empty path."""
    ctx = TranspileCtx()
    section = CodexAuthorizationCodec.to_target(Authorization(), ctx)
    assert section.approval_policy is None
    restored = CodexAuthorizationCodec.from_target(section, ctx)
    assert restored.approval_policy is None
    assert ctx.warnings == []


def test_codex_decode_unknown_approval_policy_string_warns_and_drops() -> None:
    """An ``approval_policy`` wire string outside the LCD vocabulary
    must trigger a typed LossWarning and leave the neutral side unset
    rather than inventing a value."""
    section = CodexAuthorizationSection(approval_policy="totally-made-up")
    ctx = TranspileCtx()
    auth = CodexAuthorizationCodec.from_target(section, ctx)
    assert auth.approval_policy is None
    matching = [
        w
        for w in ctx.warnings
        if isinstance(w, LossWarning)
        and w.target == BUILTIN_CODEX
        and w.domain is Domains.AUTHORIZATION
        and "totally-made-up" in w.message
    ]
    assert len(matching) == 1


def test_codex_decode_granular_approval_policy_emits_loss_warning() -> None:
    """The ``AskForApproval4`` granular arm decodes as a TOML inline
    table → a dict on the section. The codec must:

    * not crash on the dict shape
    * emit exactly one Codex-AUTHORIZATION ``LossWarning`` naming the
      pass-through escape route
    * leave ``Authorization.approval_policy`` unset (LCD discipline:
      we do NOT invent a neutral projection of the granular form)
    """
    granular: dict[str, object] = {
        "granular": {
            "patches": True,
            "shell_writes": False,
        }
    }
    section = CodexAuthorizationSection(approval_policy=granular)
    ctx = TranspileCtx()
    auth = CodexAuthorizationCodec.from_target(section, ctx)
    assert auth.approval_policy is None
    granular_warnings = [
        w
        for w in ctx.warnings
        if isinstance(w, LossWarning)
        and w.target == BUILTIN_CODEX
        and w.domain is Domains.AUTHORIZATION
        and "granular" in w.message
    ]
    assert len(granular_warnings) == 1
    msg = granular_warnings[0].message
    # The message must point operators at the pass-through escape — that's
    # the actual remediation path under LCD discipline.
    assert "targets.codex" in msg
    assert "permissions" in msg


@pytest.mark.parametrize("pmode", list(PermissionMode))
def test_codex_encode_permission_mode_emits_loss_warning(pmode: PermissionMode) -> None:
    """Encoding a neutral with ``permission_mode`` set on the Codex
    side must emit exactly one typed LossWarning per merge — this is
    a Claude-only axis with no Codex wire equivalent. The wire section
    must not gain a ``permission_mode`` field as a side effect; it must
    not appear in the codec's claimed paths either (we don't claim
    something we drop)."""
    orig = Authorization(permission_mode=pmode)
    ctx = TranspileCtx()
    section = CodexAuthorizationCodec.to_target(orig, ctx)
    # No smuggling: section model has no permission_mode field.
    assert "permission_mode" not in section.model_dump()
    matching = [
        w
        for w in ctx.warnings
        if isinstance(w, LossWarning)
        and w.target == BUILTIN_CODEX
        and w.domain is Domains.AUTHORIZATION
        and "permission_mode" in w.message
    ]
    assert len(matching) == 1, (
        "expected exactly one permission_mode-tagged LossWarning; "
        f"got {[w.message for w in ctx.warnings]}"
    )
    msg = matching[0].message
    # The message must orient the operator: name the cross-target axis,
    # the asymmetry, and the Codex-side alternative.
    assert "Claude-only" in msg
    assert "sandbox_mode" in msg


def test_codex_encode_no_permission_mode_no_warning() -> None:
    """Sanity: the LossWarning fires only on a set ``permission_mode``,
    not on every encode. A default ``Authorization()`` must produce no
    permission_mode-tagged warnings."""
    ctx = TranspileCtx()
    CodexAuthorizationCodec.to_target(Authorization(), ctx)
    matching = [
        w for w in ctx.warnings if isinstance(w, LossWarning) and "permission_mode" in w.message
    ]
    assert matching == []


def test_codex_authorization_claimed_paths_includes_approval_policy() -> None:
    """Wave-13 S3 wiring: the codec claims ``approval_policy`` so the
    static no-silent-upstream-drops audit credits it as 'claimed'
    (not pass-through). This is the assertion the audit relies on."""
    paths = {p.render() for p in CodexAuthorizationCodec.claimed_paths}
    assert "approval_policy" in paths


def test_codex_authorization_claimed_paths_excludes_permission_mode() -> None:
    """LCD discipline: ``permission_mode`` is dropped on Codex encode
    via LossWarning — it is NOT claimed by this codec (we claim only
    fields we actually translate)."""
    paths = {p.render() for p in CodexAuthorizationCodec.claimed_paths}
    assert "permission_mode" not in paths
