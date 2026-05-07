"""Wave-10 §15.x — Claude codec coverage for the 5 unclaimed enum slots.

Sibling agent owns the matching Codex side. This file verifies the
Claude side only:

  1. identity.auth.method (AuthMethod) — full bidirectional map onto
     Claude's forceLoginMethod (claudeai/console). Wave-11 §15.x
     reconciliation shrank AuthMethod from 6 values to 2 after
     confirming neither upstream exposes BEDROCK/VERTEX/AZURE/NONE
     as a login-method enum value, so all remaining values now
     round-trip lossily-free on Claude.
  2. directives.verbosity (Verbosity) — LossWarning only; Claude has no
     persistent verbosity setting.
  3. capabilities.web_search (Literal) — LossWarning only; Claude gates
     web search via permissions tool patterns, not a tri-state axis.
  4. environment.inherit (InheritPolicy) — LossWarning only; Claude
     inherits the parent shell environment unconditionally.
  5. lifecycle.history.persistence (HistoryPersistence) — LossWarning
     only; the closest analogue is the CLAUDE_CODE_SKIP_PROMPT_HISTORY
     env var, owned by the environment codec.
"""

from __future__ import annotations

from typing import Literal, cast

import pytest

from chameleon._types import FieldPath
from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.codecs.claude.directives import ClaudeDirectivesCodec
from chameleon.codecs.claude.environment import ClaudeEnvironmentCodec
from chameleon.codecs.claude.identity import ClaudeIdentityCodec, ClaudeIdentitySection
from chameleon.codecs.claude.lifecycle import ClaudeLifecycleCodec
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.capabilities import Capabilities
from chameleon.schema.directives import Directives, Verbosity
from chameleon.schema.environment import Environment, InheritPolicy
from chameleon.schema.identity import AuthMethod, Identity, IdentityAuth
from chameleon.schema.lifecycle import History, HistoryPersistence, Lifecycle

# ---- 1. identity.auth.method -----------------------------------------------


@pytest.mark.parametrize(
    ("neutral", "wire"),
    [(AuthMethod.OAUTH, "claudeai"), (AuthMethod.API_KEY, "console")],
)
def test_identity_auth_method_round_trips_supported_values(neutral: AuthMethod, wire: str) -> None:
    orig = Identity(auth=IdentityAuth(method=neutral))
    ctx = TranspileCtx()
    section = ClaudeIdentityCodec.to_target(orig, ctx)
    assert section.force_login_method == wire
    assert ctx.warnings == []
    restored = ClaudeIdentityCodec.from_target(section, ctx)
    assert restored.auth.method is neutral


def test_identity_auth_method_enum_reconciled_to_two_values() -> None:
    """Wave-11 §15.x reconciliation pin: ``AuthMethod`` carries exactly the
    two values that both upstream login-method enums model.

    The original neutral schema had six values (OAUTH/API_KEY/BEDROCK/
    VERTEX/AZURE/NONE) on the speculative theory that Chameleon could
    expose multi-cloud provider lanes through a single auth-method axis.
    Inspection of both ``_generated.py`` files showed only OAUTH and
    API_KEY have any wire reality; the rest produced a LossWarning on
    every target without changing observable behaviour.

    This test fails loudly if a future change re-adds a value Chameleon
    cannot honestly round-trip on either target. If a future upstream
    grows a third login method, add it here AND mirror the codec mapping
    on whichever target newly supports it; do NOT add neutral values
    that no codec can claim.
    """
    assert {m.value for m in AuthMethod} == {"oauth", "api-key"}


def test_identity_api_key_helper_round_trips() -> None:
    orig = Identity(auth=IdentityAuth(api_key_helper="/bin/keys.sh"))
    ctx = TranspileCtx()
    section = ClaudeIdentityCodec.to_target(orig, ctx)
    assert section.api_key_helper == "/bin/keys.sh"
    restored = ClaudeIdentityCodec.from_target(section, ctx)
    assert restored.auth.api_key_helper == "/bin/keys.sh"


def test_identity_unknown_force_login_method_warns_on_decode() -> None:
    # A real ~/.claude/settings.json may carry a wire value that's outside
    # the two-element ForceLoginMethod enum (e.g. a future vendor adds one
    # before we regen). Drop with a typed warning rather than crash.
    section = ClaudeIdentitySection.model_validate({"forceLoginMethod": "futurevendor"})
    ctx = TranspileCtx()
    restored = ClaudeIdentityCodec.from_target(section, ctx)
    assert restored.auth.method is None
    assert any(
        "forceLoginMethod" in w.message and "futurevendor" in w.message for w in ctx.warnings
    )


# ---- 2. directives.verbosity -----------------------------------------------


@pytest.mark.parametrize("v", list(Verbosity))
def test_directives_verbosity_warns(v: Verbosity) -> None:
    orig = Directives(verbosity=v)
    ctx = TranspileCtx()
    ClaudeDirectivesCodec.to_target(orig, ctx)
    assert any(
        w.domain is Domains.DIRECTIVES
        and w.target == BUILTIN_CLAUDE
        and w.field_path == FieldPath(segments=("verbosity",))
        for w in ctx.warnings
    )


def test_directives_verbosity_unset_does_not_warn() -> None:
    ctx = TranspileCtx()
    ClaudeDirectivesCodec.to_target(Directives(), ctx)
    # The unset case must not emit a verbosity warning. Other warnings
    # may exist for unrelated reasons; we filter to verbosity scope.
    assert not any(w.field_path == FieldPath(segments=("verbosity",)) for w in ctx.warnings)


# ---- 3. capabilities.web_search --------------------------------------------


@pytest.mark.parametrize(
    "ws",
    [
        cast(Literal["cached", "live", "disabled"], "cached"),
        cast(Literal["cached", "live", "disabled"], "live"),
        cast(Literal["cached", "live", "disabled"], "disabled"),
    ],
)
def test_capabilities_web_search_warns(ws: Literal["cached", "live", "disabled"]) -> None:
    orig = Capabilities(web_search=ws)
    ctx = TranspileCtx()
    ClaudeCapabilitiesCodec.to_target(orig, ctx)
    assert any(
        w.domain is Domains.CAPABILITIES
        and w.target == BUILTIN_CLAUDE
        and w.field_path == FieldPath(segments=("web_search",))
        for w in ctx.warnings
    )


def test_capabilities_web_search_unset_does_not_warn() -> None:
    ctx = TranspileCtx()
    ClaudeCapabilitiesCodec.to_target(Capabilities(), ctx)
    assert not any(w.field_path == FieldPath(segments=("web_search",)) for w in ctx.warnings)


# ---- 4. environment.inherit ------------------------------------------------


@pytest.mark.parametrize("policy", list(InheritPolicy))
def test_environment_inherit_warns(policy: InheritPolicy) -> None:
    orig = Environment(inherit=policy)
    ctx = TranspileCtx()
    section = ClaudeEnvironmentCodec.to_target(orig, ctx)
    # Variables still flow through — the warning is scoped to inherit only.
    assert section.env == {}
    assert any(
        w.domain is Domains.ENVIRONMENT
        and w.target == BUILTIN_CLAUDE
        and w.field_path == FieldPath(segments=("inherit",))
        for w in ctx.warnings
    )


def test_environment_variables_still_round_trip_when_inherit_set() -> None:
    orig = Environment(variables={"FOO": "bar"}, inherit=InheritPolicy.CORE)
    ctx = TranspileCtx()
    section = ClaudeEnvironmentCodec.to_target(orig, ctx)
    restored = ClaudeEnvironmentCodec.from_target(section, ctx)
    assert restored.variables == {"FOO": "bar"}


# ---- 5. lifecycle.history.persistence --------------------------------------


@pytest.mark.parametrize("p", list(HistoryPersistence))
def test_lifecycle_history_persistence_warns(p: HistoryPersistence) -> None:
    orig = Lifecycle(history=History(persistence=p))
    ctx = TranspileCtx()
    ClaudeLifecycleCodec.to_target(orig, ctx)
    assert any(
        w.domain is Domains.LIFECYCLE
        and w.target == BUILTIN_CLAUDE
        and w.field_path == FieldPath(segments=("history", "persistence"))
        for w in ctx.warnings
    )


def test_lifecycle_history_max_bytes_warns() -> None:
    orig = Lifecycle(history=History(max_bytes=1024))
    ctx = TranspileCtx()
    ClaudeLifecycleCodec.to_target(orig, ctx)
    assert any(w.field_path == FieldPath(segments=("history", "max_bytes")) for w in ctx.warnings)
