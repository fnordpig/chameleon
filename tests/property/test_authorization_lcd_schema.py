"""Wave-13 S1 — Authorization LCD schema sanity tests.

These tests pin the lowest-common-denominator vocabulary the schema
exports for the P3 unification (see
``docs/superpowers/specs/2026-05-06-p3-authorization-design.md``):

* ``SandboxMode`` (Codex-aligned, 3 values; renamed from ``DefaultMode``).
* ``PermissionMode`` (Claude-aligned subset, 3 values out of Claude's 7).
* ``ApprovalPolicy`` (Codex-aligned subset, 4 values out of the 5-arm
  ``AskForApproval`` discriminated union; the structured ``granular``
  arm is intentionally not modelled and routes through pass-through).

If a future PR widens any of these, this test forces the change to
land in the same commit — preventing schema drift between the codecs
and their published vocabulary.
"""

from __future__ import annotations

from chameleon.schema.authorization import (
    ApprovalPolicy,
    Authorization,
    PermissionMode,
    SandboxMode,
)


def test_sandbox_mode_vocabulary_is_exactly_three_values() -> None:
    assert {m.value for m in SandboxMode} == {
        "read-only",
        "workspace-write",
        "full-access",
    }


def test_permission_mode_vocabulary_is_exactly_three_values() -> None:
    assert {m.value for m in PermissionMode} == {
        "default",
        "accept_edits",
        "plan",
    }


def test_approval_policy_vocabulary_is_exactly_four_values() -> None:
    assert {m.value for m in ApprovalPolicy} == {
        "untrusted",
        "on_failure",
        "on_request",
        "never",
    }


def test_authorization_constructs_clean_with_all_modes_unset() -> None:
    auth = Authorization()
    assert auth.sandbox_mode is None
    assert auth.permission_mode is None
    assert auth.approval_policy is None
    assert auth.reviewer is None


def test_authorization_sandbox_mode_round_trips_via_model_dump() -> None:
    for mode in SandboxMode:
        orig = Authorization(sandbox_mode=mode)
        restored = Authorization.model_validate(orig.model_dump())
        assert restored.sandbox_mode is mode


def test_authorization_permission_mode_round_trips_via_model_dump() -> None:
    for mode in PermissionMode:
        orig = Authorization(permission_mode=mode)
        restored = Authorization.model_validate(orig.model_dump())
        assert restored.permission_mode is mode


def test_authorization_approval_policy_round_trips_via_model_dump() -> None:
    for policy in ApprovalPolicy:
        orig = Authorization(approval_policy=policy)
        restored = Authorization.model_validate(orig.model_dump())
        assert restored.approval_policy is policy
