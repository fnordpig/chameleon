"""Wave-13 S2 — Claude authorization codec, LCD-aligned.

Pins the LCD principle for the Claude side:

* ``permission_mode`` is Claude's axis: bijective with
  ``permissions.defaultMode`` for the 3 LCD values
  (``DEFAULT``/``ACCEPT_EDITS``/``PLAN``). All three round-trip cleanly.
* ``permissions.defaultMode`` values outside the LCD subset
  (``auto``/``dontAsk``/``bypassPermissions``/``delegate``) emit a
  typed ``LossWarning`` on decode and route to pass-through.
* ``sandbox_mode`` is a Codex-only axis: encoding through Claude emits a
  typed ``LossWarning`` and produces no wire output for that field.
* ``approval_policy`` is Codex-only: same disposition as
  ``sandbox_mode``.

Existing reviewer/filesystem/network/pattern coverage lives in
``test_deferred_domains.py`` and the per-codec round-trip fuzz; this
file owns the LCD-axis surface specifically.
"""

from __future__ import annotations

import pytest

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.authorization import (
    ClaudeAuthorizationCodec,
    ClaudeAuthorizationSection,
)
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.authorization import (
    ApprovalPolicy,
    Authorization,
    PermissionMode,
    SandboxMode,
)

# ---- permission_mode bijection (LCD lossless side) -------------------------


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_permission_mode_round_trips_via_codec(mode: PermissionMode) -> None:
    """All 3 LCD ``PermissionMode`` values bijectively map through the codec."""
    orig = Authorization(permission_mode=mode)
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(orig, ctx)
    restored = ClaudeAuthorizationCodec.from_target(section, ctx)
    assert restored.permission_mode is mode
    # No LossWarning fires on the LCD-modeled values.
    assert not any(
        w.domain is Domains.AUTHORIZATION and "permission_mode" in w.message for w in ctx.warnings
    )


def test_permission_mode_default_writes_default_wire_value() -> None:
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(
        Authorization(permission_mode=PermissionMode.DEFAULT), ctx
    )
    assert section.permissions.defaultMode == "default"


def test_permission_mode_accept_edits_writes_camelcase_wire_value() -> None:
    """Wire alignment: neutral ``accept_edits`` → wire ``acceptEdits``."""
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(
        Authorization(permission_mode=PermissionMode.ACCEPT_EDITS), ctx
    )
    assert section.permissions.defaultMode == "acceptEdits"


def test_permission_mode_plan_writes_plan_wire_value() -> None:
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(
        Authorization(permission_mode=PermissionMode.PLAN), ctx
    )
    assert section.permissions.defaultMode == "plan"


# ---- Decode: unmodeled wire values warn but do not crash -------------------


@pytest.mark.parametrize("wire_value", ["auto", "dontAsk", "bypassPermissions", "delegate"])
def test_unmodeled_default_mode_emits_loss_warning(wire_value: str) -> None:
    """Claude wire values outside the LCD subset warn and do not crash."""
    section = ClaudeAuthorizationSection()
    section.permissions.defaultMode = wire_value
    ctx = TranspileCtx()
    auth = ClaudeAuthorizationCodec.from_target(section, ctx)
    # The neutral field is None — the value did not enter the schema.
    assert auth.permission_mode is None
    # A typed LossWarning fires, naming the offending wire value and
    # pointing the operator at the pass-through escape hatch.
    matching = [
        w
        for w in ctx.warnings
        if w.domain is Domains.AUTHORIZATION
        and w.target == BUILTIN_CLAUDE
        and wire_value in w.message
    ]
    assert len(matching) == 1, f"expected exactly one LossWarning naming {wire_value!r}"
    assert "PermissionMode" in matching[0].message
    assert "pass-through" in matching[0].message or "targets.claude" in matching[0].message


# ---- Encode: sandbox_mode is Codex-only — Claude warns and drops ----------


@pytest.mark.parametrize("mode", list(SandboxMode))
def test_sandbox_mode_encode_emits_loss_warning_and_drops(mode: SandboxMode) -> None:
    """Codex-only axis: Claude codec drops on encode with a typed warning."""
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(Authorization(sandbox_mode=mode), ctx)
    # No wire field is set for sandbox_mode (the codec does not synthesise).
    assert section.permissions.defaultMode is None
    # A typed LossWarning fires, naming sandbox_mode and pointing the
    # operator at the Codex side / Claude's permission_mode axis.
    matching = [
        w
        for w in ctx.warnings
        if w.domain is Domains.AUTHORIZATION
        and w.target == BUILTIN_CLAUDE
        and "sandbox_mode" in w.message
    ]
    assert len(matching) == 1
    assert "Codex-only" in matching[0].message
    assert "permission_mode" in matching[0].message


def test_sandbox_mode_decode_never_synthesises() -> None:
    """A wire payload with no ``defaultMode`` produces no ``sandbox_mode``."""
    section = ClaudeAuthorizationSection()
    ctx = TranspileCtx()
    auth = ClaudeAuthorizationCodec.from_target(section, ctx)
    assert auth.sandbox_mode is None


# ---- Encode: approval_policy is Codex-only — Claude warns and drops -------


@pytest.mark.parametrize("policy", list(ApprovalPolicy))
def test_approval_policy_encode_emits_loss_warning_and_drops(
    policy: ApprovalPolicy,
) -> None:
    """Codex-only axis: Claude codec drops on encode with a typed warning."""
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(Authorization(approval_policy=policy), ctx)
    # The codec does not invent a wire field for approval_policy.
    assert section.permissions.defaultMode is None
    matching = [
        w
        for w in ctx.warnings
        if w.domain is Domains.AUTHORIZATION
        and w.target == BUILTIN_CLAUDE
        and "approval_policy" in w.message
    ]
    assert len(matching) == 1
    assert "Codex-only" in matching[0].message


def test_approval_policy_decode_never_synthesises() -> None:
    section = ClaudeAuthorizationSection()
    ctx = TranspileCtx()
    auth = ClaudeAuthorizationCodec.from_target(section, ctx)
    assert auth.approval_policy is None


# ---- Co-occurrence: all three Codex-only fields warn independently ---------


def test_sandbox_and_approval_policy_warn_independently() -> None:
    """Encoding both Codex-only fields produces two independent warnings."""
    ctx = TranspileCtx()
    ClaudeAuthorizationCodec.to_target(
        Authorization(
            sandbox_mode=SandboxMode.WORKSPACE_WRITE,
            approval_policy=ApprovalPolicy.ON_REQUEST,
        ),
        ctx,
    )
    sandbox_warns = [w for w in ctx.warnings if "sandbox_mode" in w.message]
    approval_warns = [w for w in ctx.warnings if "approval_policy" in w.message]
    assert len(sandbox_warns) == 1
    assert len(approval_warns) == 1


def test_permission_mode_coexists_with_dropped_codex_only_axes() -> None:
    """Setting permission_mode alongside Codex-only fields still round-trips it."""
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(
        Authorization(
            permission_mode=PermissionMode.PLAN,
            sandbox_mode=SandboxMode.READ_ONLY,
            approval_policy=ApprovalPolicy.NEVER,
        ),
        ctx,
    )
    restored = ClaudeAuthorizationCodec.from_target(section, ctx)
    assert restored.permission_mode is PermissionMode.PLAN
    assert restored.sandbox_mode is None
    assert restored.approval_policy is None
