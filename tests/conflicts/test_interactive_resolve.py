from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from chameleon._types import FieldPath
from chameleon.merge import resolve as resolve_mod
from chameleon.merge.changeset import ChangeRecord
from chameleon.merge.conflict import Conflict
from chameleon.merge.resolve import InteractiveResolver
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains


def _conflict() -> Conflict:
    return Conflict(
        record=ChangeRecord(
            domain=Domains.IDENTITY,
            path=FieldPath(segments=("model",)),
            n0="claude-sonnet-4-6",
            n1="claude-sonnet-4-7",
            per_target={
                BUILTIN_CLAUDE: "claude-opus-4-7",
                BUILTIN_CODEX: "gpt-5-pro",
            },
        ),
    )


def _quiet_console() -> Console:
    return Console(file=io.StringIO())


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


def test_interactive_resolver_take_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []
    _patch_prompt(monkeypatch, answer="n", captured_choices=captured)
    resolver = InteractiveResolver(console=_quiet_console())
    result = resolver.resolve(_conflict())
    assert result == "claude-sonnet-4-7"
    assert captured
    assert "n" in captured[0]
    assert "k" in captured[0]
    assert "s" in captured[0]


def test_interactive_resolver_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_prompt(monkeypatch, answer="s")
    resolver = InteractiveResolver(console=_quiet_console())
    assert resolver.resolve(_conflict()) is None


def test_interactive_resolver_revert_to_lkg(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_prompt(monkeypatch, answer="k")
    resolver = InteractiveResolver(console=_quiet_console())
    assert resolver.resolve(_conflict()) == "claude-sonnet-4-6"


def test_interactive_resolver_pick_first_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_prompt(monkeypatch, answer="a")
    resolver = InteractiveResolver(console=_quiet_console())
    # Insertion order: per_target dict has claude first → letter `a`.
    assert resolver.resolve(_conflict()) == "claude-opus-4-7"
