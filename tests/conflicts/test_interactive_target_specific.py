"""Wave-15 W15-B: ``[t]`` target-specific choice + prior-decision rendering.

Covers the InteractiveResolver-side surface of the resolution-memory
spec §6.2 (re-prompt with prior-decision context after a hash drift)
and §6.3 (the ``[t]`` choice produces a TARGET_SPECIFIC outcome).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import pytest
from rich.console import Console

from chameleon._types import FieldPath
from chameleon.merge import resolve as resolve_mod
from chameleon.merge.changeset import ChangeRecord
from chameleon.merge.conflict import Conflict
from chameleon.merge.resolve import InteractiveResolver
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains
from chameleon.schema.neutral import Resolution, ResolutionDecisionKind


def _conflict(prior: Resolution | None = None) -> Conflict:
    return Conflict(
        record=ChangeRecord(
            domain=Domains.IDENTITY,
            path=FieldPath(segments=("identity", "reasoning_effort")),
            n0="medium",
            n1="high",
            per_target={
                BUILTIN_CLAUDE: "xhigh",
                BUILTIN_CODEX: "low",
            },
        ),
        prior_decision=prior,
    )


def _quiet_console() -> Console:
    return Console(file=io.StringIO())


def _capturing_console() -> tuple[Console, io.StringIO]:
    """Return a wide console writing to a captured StringIO buffer.

    The width is forced wide so Rich does not soft-wrap the prior-decision
    line — tests check for exact substrings in the captured output.
    """
    buf = io.StringIO()
    return Console(file=buf, width=200), buf


def _patch_prompt(
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    captured_choices: list[list[str]] | None = None,
) -> None:
    def fake_ask(prompt: object, *, choices: list[str] | None = None, **_: Any) -> str:
        if captured_choices is not None:
            captured_choices.append(list(choices or []))
        return answer

    monkeypatch.setattr(resolve_mod.Prompt, "ask", staticmethod(fake_ask))


# ---------------------------------------------------------------------------
# `[t]` choice → TARGET_SPECIFIC outcome
# ---------------------------------------------------------------------------


def test_interactive_resolver_t_choice_returns_target_specific_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``[t]`` produces ``ResolverOutcome(decision=TARGET_SPECIFIC, persist=True)``."""
    _patch_prompt(monkeypatch, answer="t")
    resolver = InteractiveResolver(console=_quiet_console())
    outcome = resolver.resolve(_conflict())
    assert outcome.decision is ResolutionDecisionKind.TARGET_SPECIFIC
    assert outcome.value is None
    assert outcome.decision_target is None
    assert outcome.persist is True


def test_interactive_resolver_t_choice_in_rendered_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The set of valid choices passed to ``Prompt.ask`` includes ``"t"``."""
    captured: list[list[str]] = []
    _patch_prompt(monkeypatch, answer="t", captured_choices=captured)
    resolver = InteractiveResolver(console=_quiet_console())
    resolver.resolve(_conflict())
    assert captured, "Prompt.ask was never called"
    assert "t" in captured[0]
    # Existing choices must still be present.
    assert "k" in captured[0]
    assert "s" in captured[0]


def test_interactive_resolver_t_choice_banner_contains_target_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The choice banner advertises the new ``[t] target-specific`` choice."""
    _patch_prompt(monkeypatch, answer="t")
    console, buf = _capturing_console()
    resolver = InteractiveResolver(console=console)
    resolver.resolve(_conflict())
    out = buf.getvalue()
    assert "target-specific" in out
    # And the letter [t] is rendered in the banner explicitly.
    assert " t " in out or "[t]" in out or "t target-specific" in out


# ---------------------------------------------------------------------------
# Prior-decision rendering
# ---------------------------------------------------------------------------


def test_interactive_resolver_renders_prior_decision_take_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TAKE_TARGET prior renders 'Prior decision' + the target name + caveat."""
    _patch_prompt(monkeypatch, answer="s")  # any valid choice; output is what we test
    console, buf = _capturing_console()
    resolver = InteractiveResolver(console=console)
    prior = Resolution(
        decided_at=datetime(2026, 5, 6, 18, 42, tzinfo=UTC),
        decision=ResolutionDecisionKind.TAKE_TARGET,
        decision_target=BUILTIN_CLAUDE,
        decision_hash="hash-x",
    )
    resolver.resolve(_conflict(prior=prior))
    out = buf.getvalue()
    assert "Prior decision" in out
    assert "claude" in out
    assert "values have changed since" in out


def test_interactive_resolver_renders_prior_decision_take_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TAKE_NEUTRAL prior renders 'Prior decision' + 'neutral' + caveat."""
    _patch_prompt(monkeypatch, answer="s")
    console, buf = _capturing_console()
    resolver = InteractiveResolver(console=console)
    prior = Resolution(
        decided_at=datetime(2026, 5, 6, 18, 42, tzinfo=UTC),
        decision=ResolutionDecisionKind.TAKE_NEUTRAL,
        decision_hash="hash-y",
    )
    resolver.resolve(_conflict(prior=prior))
    out = buf.getvalue()
    assert "Prior decision" in out
    assert "neutral" in out
    assert "values have changed since" in out


def test_interactive_resolver_renders_prior_decision_target_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TARGET_SPECIFIC prior renders 'target-specific' in the prior line."""
    _patch_prompt(monkeypatch, answer="s")
    console, buf = _capturing_console()
    resolver = InteractiveResolver(console=console)
    prior = Resolution(
        decided_at=datetime(2026, 5, 6, 18, 42, tzinfo=UTC),
        decision=ResolutionDecisionKind.TARGET_SPECIFIC,
        decision_hash="hash-z",
    )
    resolver.resolve(_conflict(prior=prior))
    out = buf.getvalue()
    assert "Prior decision" in out
    assert "target-specific" in out
    assert "values have changed since" in out


def test_interactive_resolver_renders_prior_decision_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SKIP prior renders 'skipped' (or equivalent) in the prior line."""
    _patch_prompt(monkeypatch, answer="s")
    console, buf = _capturing_console()
    resolver = InteractiveResolver(console=console)
    prior = Resolution(
        decided_at=datetime(2026, 5, 6, 18, 42, tzinfo=UTC),
        decision=ResolutionDecisionKind.SKIP,
        decision_hash="hash-q",
    )
    resolver.resolve(_conflict(prior=prior))
    out = buf.getvalue()
    assert "Prior decision" in out
    assert "skipped" in out


def test_interactive_resolver_no_prior_no_prior_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a prior decision, no 'Prior decision' line is rendered."""
    _patch_prompt(monkeypatch, answer="s")
    console, buf = _capturing_console()
    resolver = InteractiveResolver(console=console)
    resolver.resolve(_conflict(prior=None))
    out = buf.getvalue()
    assert "Prior decision" not in out


def test_interactive_resolver_prior_does_not_prefill_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prior decisions are informational only: ``Prompt.ask`` must still be called.

    The operator must explicitly answer the prompt — the prior must not
    short-circuit the resolver into auto-applying the previously-chosen
    decision.
    """
    call_count = [0]

    def fake_ask(_: object, *, choices: list[str] | None = None, **__: Any) -> str:
        call_count[0] += 1
        return "n"  # take neutral

    monkeypatch.setattr(resolve_mod.Prompt, "ask", staticmethod(fake_ask))
    resolver = InteractiveResolver(console=_quiet_console())
    prior = Resolution(
        decided_at=datetime(2026, 5, 6, 18, 42, tzinfo=UTC),
        decision=ResolutionDecisionKind.TAKE_TARGET,
        decision_target=BUILTIN_CLAUDE,
        decision_hash="hash-x",
    )
    outcome = resolver.resolve(_conflict(prior=prior))
    assert call_count[0] == 1, "Prompt.ask must be invoked even when a prior exists"
    # Operator answered 'n' (take neutral), NOT the prior's TAKE_TARGET.
    assert outcome.decision is ResolutionDecisionKind.TAKE_NEUTRAL
