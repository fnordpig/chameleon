"""Conflict resolution: interactive (TTY) and non-interactive (CLI flag)."""

from __future__ import annotations

import sys
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from chameleon._types import TargetId
from chameleon.merge.changeset import ChangeSource
from chameleon.merge.conflict import Conflict
from chameleon.schema._constants import OnConflict
from chameleon.schema.neutral import Resolution, ResolutionDecisionKind


class ResolverOutcome(BaseModel):
    """Typed return shape for ``Resolver.resolve`` (resolution-memory ).

    ``decision`` records which of the five operator choices the resolver
    made; ``value`` is the actual resolved leaf value (or ``None`` for
    SKIP / TARGET_SPECIFIC, where there is no single unified value);
    ``persist`` is set by the resolver itself — interactive resolvers
    persist their decisions; non-interactive strategies do not (the
    spec's rule: "non-interactive strategies are stateless by
    design").
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    decision: ResolutionDecisionKind
    decision_target: TargetId | None = None
    value: Any = None
    persist: bool = True


class Resolver(Protocol):
    """A resolver returns a typed ``ResolverOutcome`` describing the choice."""

    def resolve(self, conflict: Conflict) -> ResolverOutcome: ...


class Strategy(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: OnConflict
    target: TargetId | None = None


class LatestResolutionError(RuntimeError):
    """Raised when the latest-source strategy cannot choose safely."""


def on_conflict_to_strategy(raw: str) -> Strategy:
    """Parse the CLI's --on-conflict argument into a typed Strategy."""
    if raw.startswith("prefer="):
        target_name = raw.removeprefix("prefer=")
        if target_name == "neutral":
            return Strategy(kind=OnConflict.PREFER_NEUTRAL)
        if target_name == "lkg":
            return Strategy(kind=OnConflict.PREFER_LKG)
        return Strategy(kind=OnConflict.PREFER_TARGET, target=TargetId(value=target_name))

    mapping = {
        "fail": OnConflict.FAIL,
        "latest": OnConflict.LATEST,
        "keep": OnConflict.KEEP,
        "prefer-neutral": OnConflict.PREFER_NEUTRAL,
        "prefer-lkg": OnConflict.PREFER_LKG,
    }
    return Strategy(kind=mapping[raw])


def _latest_outcome(conflict: Conflict, *, persist: bool) -> ResolverOutcome:
    """Resolve a conflict by the uniquely newest changed source.

    The strategy is intentionally conservative: every changed source
    must carry a timestamp, and the maximum timestamp must be unique.
    Otherwise the caller can fall back to interactive resolution or fail
    in unattended contexts.
    """
    record = conflict.record
    candidates: list[tuple[int, ChangeSource, TargetId | None, Any]] = []
    changed_count = 0

    if record.n1 != record.n0:
        changed_count += 1
        if record.neutral_mtime_ns is not None:
            candidates.append((record.neutral_mtime_ns, ChangeSource.NEUTRAL, None, record.n1))

    for tid, val in record.per_target.items():
        if val == record.n0:
            continue
        changed_count += 1
        mtime = record.per_target_mtime_ns.get(tid)
        if mtime is not None:
            candidates.append((mtime, ChangeSource.TARGET, tid, val))

    if len(candidates) != changed_count:
        msg = f"missing timestamp for latest conflict on {record.render_path()}"
        raise LatestResolutionError(msg)

    newest_mtime = max(mtime for mtime, _src, _tid, _val in candidates)
    newest = [item for item in candidates if item[0] == newest_mtime]
    if len(newest) != 1:
        msg = f"ambiguous latest conflict on {record.render_path()}: tied newest sources"
        raise LatestResolutionError(msg)

    _mtime, src, tid, val = newest[0]
    if src is ChangeSource.NEUTRAL:
        return ResolverOutcome(
            decision=ResolutionDecisionKind.TAKE_NEUTRAL,
            value=val,
            persist=persist,
        )
    assert tid is not None
    return ResolverOutcome(
        decision=ResolutionDecisionKind.TAKE_TARGET,
        decision_target=tid,
        value=val,
        persist=persist,
    )


class NonInteractiveResolver:
    """Resolve conflicts according to a CLI-supplied Strategy.

    Non-interactive strategies never persist — see resolution-memory
    spec. Each invocation produces a ``ResolverOutcome`` with
    ``persist=False``.
    """

    def __init__(self, strategy: Strategy) -> None:
        self.strategy = strategy

    def resolve(self, conflict: Conflict) -> ResolverOutcome:
        record = conflict.record
        kind = self.strategy.kind
        if kind is OnConflict.FAIL:
            msg = (
                f"conflict on {record.render_path()}: "
                f"neutral={record.n1!r} per_target={record.per_target!r}"
            )
            raise RuntimeError(msg)
        if kind is OnConflict.LATEST:
            return _latest_outcome(conflict, persist=False)
        if kind is OnConflict.KEEP:
            return ResolverOutcome(
                decision=ResolutionDecisionKind.SKIP,
                value=None,
                persist=False,
            )
        if kind is OnConflict.PREFER_NEUTRAL:
            return ResolverOutcome(
                decision=ResolutionDecisionKind.TAKE_NEUTRAL,
                value=record.n1,
                persist=False,
            )
        if kind is OnConflict.PREFER_LKG:
            return ResolverOutcome(
                decision=ResolutionDecisionKind.TAKE_LKG,
                value=record.n0,
                persist=False,
            )
        if kind is OnConflict.PREFER_TARGET:
            assert self.strategy.target is not None
            return ResolverOutcome(
                decision=ResolutionDecisionKind.TAKE_TARGET,
                decision_target=self.strategy.target,
                value=record.per_target.get(self.strategy.target),
                persist=False,
            )
        msg = f"unknown OnConflict {kind!r}"
        raise RuntimeError(msg)


class LatestResolver:
    """Try newest-source resolution, then optionally delegate on ambiguity."""

    def __init__(self, fallback: Resolver | None = None) -> None:
        self.fallback = fallback

    def resolve(self, conflict: Conflict) -> ResolverOutcome:
        try:
            return _latest_outcome(conflict, persist=False)
        except LatestResolutionError:
            if self.fallback is None:
                raise
            return self.fallback.resolve(conflict)


class InteractiveResolver:
    """Prompt the operator on a TTY for each conflict, per design spec.

    Renders the four sources (was/now/per-target) as a table and accepts
    a single-character choice: [n] take neutral, [a]/[b] take a target,
    [k] revert to N₀ (last-known-good), [s] skip (leave unresolved).

     W15-B extends the prompt with a [t] target-specific choice
    and renders prior decisions as defaults; this base implementation
    only owns the unwrapping into a typed ``ResolverOutcome`` so the
    engine has a single interface contract regardless of which resolver
    ran.
    """

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(stderr=True)
        self._printed_preamble = False

    def _print_preamble_once(self) -> None:
        if self._printed_preamble:
            return
        self._printed_preamble = True
        self.console.print(
            "[bold]Chameleon is reconciling agent configuration.[/]\n\n"
            "Sources:\n"
            "  - checked-in neutral: neutral.yaml\n"
            "  - Claude Code live config: ~/.claude/settings.json and ~/.claude.json\n"
            "  - Codex live config: ~/.codex/config.toml\n\n"
            "A setting changed in more than one place and Chameleon cannot safely pick one.\n"
            "Choose:\n"
            "  n  take neutral\n"
            "  a/b/c...  take a target\n"
            "  k  revert to last-known-good\n"
            "  t  keep target-specific values\n"
            "  s  skip for now"
        )

    @staticmethod
    def _format_prior_decision(prior: Resolution) -> str:
        """Render a one-line summary of a prior decision (W15-B ).

        Used to give the operator context when re-prompting because the
        invalidation hash drifted. Informational only — the operator must
        still answer the prompt explicitly; the prior is NOT pre-filled.
        """
        decision = prior.decision
        if decision is ResolutionDecisionKind.TAKE_TARGET:
            target_name = (
                prior.decision_target.value if prior.decision_target is not None else "target"
            )
            chose = f"[bold]{target_name}[/]"
        elif decision is ResolutionDecisionKind.TAKE_NEUTRAL:
            chose = "[bold]neutral[/]"
        elif decision is ResolutionDecisionKind.TAKE_LKG:
            chose = "[bold]revert to N₀ (last-known-good)[/]"
        elif decision is ResolutionDecisionKind.TARGET_SPECIFIC:
            chose = "[bold]target-specific (per-target preserved)[/]"
        else:  # SKIP
            chose = "[bold]skipped[/]"
        when = prior.decided_at.strftime("%Y-%m-%d %H:%M")
        return (
            f"[bold yellow]Prior decision[/]: chose {chose} on {when} (values have changed since)"
        )

    def resolve(self, conflict: Conflict) -> ResolverOutcome:
        record = conflict.record
        path_label = record.render_path()
        self._print_preamble_once()

        # Render the four sources with letter codes
        table = Table(
            title=f"[bold red]conflict[/] on [cyan]{path_label}[/]",
            title_justify="left",
            show_header=True,
            header_style="bold",
        )
        table.add_column("key", style="dim")
        table.add_column("source")
        table.add_column("value")

        choices: dict[str, tuple[ChangeSource, TargetId | None, Any]] = {}
        # N₀ context
        table.add_row("·", "was (N₀)", repr(record.n0))
        # N₁ if changed
        if record.n1 != record.n0:
            choices["n"] = (ChangeSource.NEUTRAL, None, record.n1)
            table.add_row("[bold]n[/]", "neutral (N₁)", repr(record.n1))
        # Per-target if changed
        letter_pool = ["a", "b", "c", "d", "e", "f"]
        used = set(choices.keys())
        for tid, val in record.per_target.items():
            if val == record.n0:
                # Unchanged from N₀ — show as context, not a choice
                table.add_row("·", f"{tid.value} (unchanged)", repr(val))
                continue
            for letter in letter_pool:
                if letter not in used:
                    choices[letter] = (ChangeSource.TARGET, tid, val)
                    used.add(letter)
                    table.add_row(f"[bold]{letter}[/]", tid.value, repr(val))
                    break

        self.console.print(table)
        # Render the prior decision as informational context above the
        # choice banner when the engine populated one (W15-B ). The
        # prior does NOT pre-fill the choice — the operator must answer.
        if conflict.prior_decision is not None:
            self.console.print(self._format_prior_decision(conflict.prior_decision))
        self.console.print(
            "[dim]choose: "
            + " / ".join(f"[bold]{letter}[/]" for letter in choices)
            + " / [bold]k[/] revert to N₀"
            " / [bold]t[/] target-specific"
            " / [bold]s[/] skip[/]"
        )

        valid = [*choices.keys(), "k", "t", "s"]
        choice = Prompt.ask(
            f"resolve [cyan]{path_label}[/]",
            choices=valid,
            console=self.console,
        )

        # Translate the raw choice into a typed ResolverOutcome. Interactive
        # decisions persist by default (resolution-memory spec ).
        if choice == "s":
            return ResolverOutcome(
                decision=ResolutionDecisionKind.SKIP,
                value=None,
                persist=True,
            )
        if choice == "k":
            return ResolverOutcome(
                decision=ResolutionDecisionKind.TAKE_LKG,
                value=record.n0,
                persist=True,
            )
        if choice == "t":
            # TARGET_SPECIFIC: no unified value; the engine patches each
            # target's overlay with that target's current value. The
            # resolver carries no specific decision_target — the engine
            # handles per-target preservation symmetrically.
            return ResolverOutcome(
                decision=ResolutionDecisionKind.TARGET_SPECIFIC,
                decision_target=None,
                value=None,
                persist=True,
            )
        src, tid, val = choices[choice]
        if src is ChangeSource.NEUTRAL:
            return ResolverOutcome(
                decision=ResolutionDecisionKind.TAKE_NEUTRAL,
                value=val,
                persist=True,
            )
        # ChangeSource.TARGET → tid is set by construction.
        assert tid is not None
        return ResolverOutcome(
            decision=ResolutionDecisionKind.TAKE_TARGET,
            decision_target=tid,
            value=val,
            persist=True,
        )


def stdin_is_a_tty() -> bool:
    """Whether stdin attaches to a TTY (used to gate interactive resolvers)."""
    return sys.stdin.isatty()


__all__ = [
    "InteractiveResolver",
    "LatestResolutionError",
    "LatestResolver",
    "NonInteractiveResolver",
    "Resolver",
    "ResolverOutcome",
    "Strategy",
    "on_conflict_to_strategy",
    "stdin_is_a_tty",
]
