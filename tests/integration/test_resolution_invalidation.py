"""Interactive resolution-memory acceptance tests.

Two end-to-end scenarios:

1. **Different disagreement, same path: re-prompt with prior shown.**
   First merge picks ``[a]`` (claude wins) and persists a Resolution.
   Mutate one target's live file. Second merge re-prompts AND the
   re-prompted call's rendered prompt mentions the prior decision.

2. **Interactive ``[t]`` choice produces a TARGET_SPECIFIC outcome.**
   First merge picks ``[t]``. The resulting neutral.yaml has the
   unified path unset, has ``targets.<tid>.target_specific[<path>]``
   populated for each target, and a Resolution with
   ``decision=TARGET_SPECIFIC``.

Both tests drive ``cli.main`` end-to-end with monkeypatched
``Prompt.ask`` and ``stdin_is_a_tty`` so the InteractiveResolver path
runs without a real TTY.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from chameleon import cli
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge import resolve as resolve_mod
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.neutral import Neutral, ResolutionDecisionKind


def _setup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    state.mkdir()
    config.mkdir()
    home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("HOME", str(home))
    return {"state": state, "config": config, "home": home}


def _read_neutral(path: Path) -> Neutral:
    return Neutral.model_validate(load_yaml(path))


def _force_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend stdin is a TTY so ``cli._resolver_from_args`` picks the InteractiveResolver."""
    monkeypatch.setattr(resolve_mod, "stdin_is_a_tty", lambda: True)
    # ``cli`` re-imports the symbol; patch both binding sites.
    monkeypatch.setattr(cli, "stdin_is_a_tty", lambda: True)


def _redirect_resolver_console(monkeypatch: pytest.MonkeyPatch, buf: io.StringIO) -> None:
    """Force every ``InteractiveResolver`` instance to render into ``buf``.

    The resolver constructs a ``Console(stderr=True)`` by default; in a
    test we want Rich output captured into a known buffer so we can
    assert on what the operator would have seen.
    """
    real_init = resolve_mod.InteractiveResolver.__init__

    def init_with_buffered_console(
        self: resolve_mod.InteractiveResolver,
        console: Console | None = None,
    ) -> None:
        real_init(self, console=Console(file=buf, width=200))

    monkeypatch.setattr(
        resolve_mod.InteractiveResolver,
        "__init__",
        init_with_buffered_console,
    )


def _patch_prompt_with_capture(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[str],
    captured_prompts: list[object],
) -> Iterator[str]:
    """Patch ``Prompt.ask`` to return ``answers`` in order; capture each prompt arg."""
    answer_iter = iter(answers)

    def fake_ask(prompt: object, *_: Any, **__: Any) -> str:
        captured_prompts.append(prompt)
        return next(answer_iter)

    monkeypatch.setattr(resolve_mod.Prompt, "ask", staticmethod(fake_ask))
    return answer_iter


def _tie_latest_mtimes(*paths: Path) -> None:
    """Make ``latest`` ambiguous so these tests exercise the interactive fallback."""
    timestamp_ns = 2_000_000_000
    for path in paths:
        if path.exists():
            os.utime(path, ns=(timestamp_ns, timestamp_ns))


def _tie_target_latest_mtimes(paths: dict[str, Path]) -> None:
    home = paths["home"]
    _tie_latest_mtimes(
        home / ".claude" / "settings.json",
        home / ".claude.json",
        home / ".codex" / "config.toml",
        home / ".codex" / "requirements.toml",
    )


# ---------------------------------------------------------------------------
# — re-prompt with prior decision after hash drift
# ---------------------------------------------------------------------------


def test_invalidation_reprompts_with_prior_decision_visible(  # noqa: PLR0915 — full e2e
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """acceptance: hash drift re-prompts; the prompt shows the prior decision.

    Sequence:
        1. Bootstrap + initial clean merge.
        2. Drift Codex's live config → conflict on identity.reasoning_effort.
        3. Run merge interactively; operator picks ``[a]`` (claude wins).
           Resolution(TAKE_TARGET, claude) persisted with hash H₁.
        4. Mutate Claude's live config to a NEW value → the
           per-target / N₁ shape changes; hash drifts to H₂ ≠ H₁.
        5. Re-run merge interactively; operator picks ``[a]`` again.
           Assert ``Prompt.ask`` was called a SECOND time (re-prompt) and
           the captured rendered output for that call mentions the prior
           decision.
    """
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    # 1. Bootstrap & initial clean merge.
    assert cli.main(["init"]) == 0
    contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {"reasoning_effort": "high"},
    }
    neutral_file.write_text(dump_yaml(contents), encoding="utf-8")
    assert cli.main(["merge", "--on-conflict=prefer-neutral"]) == 0

    # 2. Drift BOTH targets away from neutral and clear the unified
    # value — this gives a real CONFLICT (two distinct non-LKG voices
    # with no operator-authored opinion). Mirrors the pattern in
    # ``test_target_specific_resolution.py``.
    claude_settings = paths["home"] / ".claude" / "settings.json"
    claude_text = claude_settings.read_text()
    claude_text = claude_text.replace('"effortLevel": "high"', '"effortLevel": "medium"')
    claude_settings.write_text(claude_text)

    codex_config = paths["home"] / ".codex" / "config.toml"
    codex_text = codex_config.read_text()
    codex_text = codex_text.replace(
        'model_reasoning_effort = "high"', 'model_reasoning_effort = "low"'
    )
    codex_config.write_text(codex_text)
    _tie_target_latest_mtimes(paths)

    cleared: dict[str, object] = {"schema_version": 1, "identity": {}}
    neutral_file.write_text(dump_yaml(cleared), encoding="utf-8")

    # Force interactive path & buffer the resolver's Console so we can
    # introspect what the operator saw.
    _force_interactive(monkeypatch)
    rendered = io.StringIO()
    _redirect_resolver_console(monkeypatch, rendered)

    # 3. First interactive merge: operator picks 'a' → first
    # changed-from-N₀ target. Both targets are drifted; insertion order
    # in per_target is claude-first, so 'a' = claude here.
    captured_prompts: list[object] = []
    _patch_prompt_with_capture(monkeypatch, ["a"], captured_prompts)
    assert cli.main(["merge"]) == 0
    assert len(captured_prompts) == 1, "expected exactly one prompt on first merge"

    n_after_first = _read_neutral(neutral_file)
    res_first = n_after_first.resolutions.items.get("identity.reasoning_effort")
    assert res_first is not None, "first interactive merge should persist a Resolution"
    assert res_first.decision is ResolutionDecisionKind.TAKE_TARGET
    first_hash = res_first.decision_hash

    # 4. Mutate BOTH targets to NEW distinct non-LKG values so the
    # ChangeRecord shape (n0, n1, per_target) is genuinely different
    # from merge1's. This ensures (a) the walker sees a real CONFLICT
    # again (≥2 distinct non-LKG voices) and (b) the engine's
    # recomputed hash won't match ``first_hash`` — driving the
    # re-prompt path.
    #
    # After merge1 both live files were brought to "medium" (TAKE_TARGET
    # propagates the chosen value to all targets). Now drift them apart.
    claude_text = claude_settings.read_text()
    claude_text = claude_text.replace('"effortLevel": "medium"', '"effortLevel": "low"')
    claude_settings.write_text(claude_text)
    codex_text = codex_config.read_text()
    codex_text = codex_text.replace(
        'model_reasoning_effort = "medium"', 'model_reasoning_effort = "minimal"'
    )
    codex_config.write_text(codex_text)
    _tie_target_latest_mtimes(paths)
    # Also clear the unified value again — the previous merge applied
    # the operator's TAKE_TARGET decision to composed, which would
    # otherwise be re-authored on the second merge as N₁. We preserve
    # the persisted Resolution by serializing the post-merge1 neutral
    # with only ``identity.reasoning_effort`` removed; clobbering the
    # whole file with ``cleared`` would also wipe ``resolutions``,
    # collapsing this back into a no-prior-decision case.
    n_to_write = n_after_first.model_copy(deep=True)
    n_to_write.identity.reasoning_effort = None
    neutral_file.write_text(
        dump_yaml(n_to_write.model_dump(mode="json", exclude_none=False)),
        encoding="utf-8",
    )

    # 5. Re-run merge. The hash mismatched → engine emits a Conflict
    # whose ``prior_decision`` is populated; resolver renders the prior.
    rendered.truncate(0)
    rendered.seek(0)
    captured_prompts_2: list[object] = []
    _patch_prompt_with_capture(monkeypatch, ["a"], captured_prompts_2)
    assert cli.main(["merge"]) == 0
    assert len(captured_prompts_2) == 1, "expected re-prompt on second merge after hash drift"

    out = rendered.getvalue()
    # The prompt explicitly mentions the prior decision and the caveat.
    assert "Prior decision" in out
    assert "values have changed since" in out

    # And the new resolution's hash differs from the first (the engine
    # re-persisted with the operator's fresh decision under the new hash).
    n_after_second = _read_neutral(neutral_file)
    res_second = n_after_second.resolutions.items.get("identity.reasoning_effort")
    assert res_second is not None
    assert res_second.decision_hash != first_hash, (
        "second merge should persist a fresh Resolution under the new hash"
    )


# ---------------------------------------------------------------------------
# — interactive [t] choice produces TARGET_SPECIFIC outcome end-to-end
# ---------------------------------------------------------------------------


def test_interactive_t_choice_persists_target_specific_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """acceptance: interactive ``[t]`` → unified unset, per-target preserved.

    Sequence:
        1. Bootstrap + initial clean merge.
        2. Drift BOTH targets to different non-LKG values, and clear the
           operator's authored neutral value at the path. → CONFLICT.
        3. Run merge interactively; operator picks ``[t]``.
        4. Assert: resolution persisted with TARGET_SPECIFIC; unified
           neutral path is unset; each target's value preserved in
           ``targets.<tid>.target_specific``.
    """
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    # 1. Bootstrap & initial clean merge.
    assert cli.main(["init"]) == 0
    contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {"reasoning_effort": "high"},
    }
    neutral_file.write_text(dump_yaml(contents), encoding="utf-8")
    assert cli.main(["merge", "--on-conflict=prefer-neutral"]) == 0

    # 2. Drift both targets and clear the unified value.
    claude_settings = paths["home"] / ".claude" / "settings.json"
    claude_text = claude_settings.read_text()
    claude_text = claude_text.replace('"effortLevel": "high"', '"effortLevel": "medium"')
    claude_settings.write_text(claude_text)

    codex_config = paths["home"] / ".codex" / "config.toml"
    codex_text = codex_config.read_text()
    codex_text = codex_text.replace(
        'model_reasoning_effort = "high"', 'model_reasoning_effort = "low"'
    )
    codex_config.write_text(codex_text)
    _tie_target_latest_mtimes(paths)

    cleared: dict[str, object] = {"schema_version": 1, "identity": {}}
    neutral_file.write_text(dump_yaml(cleared), encoding="utf-8")

    # 3. Force interactive path; operator picks 't' (target-specific).
    _force_interactive(monkeypatch)
    rendered = io.StringIO()
    _redirect_resolver_console(monkeypatch, rendered)
    captured_prompts: list[object] = []
    _patch_prompt_with_capture(monkeypatch, ["t"], captured_prompts)

    assert cli.main(["merge"]) == 0
    assert len(captured_prompts) == 1, "expected exactly one prompt for the [t] choice"

    # 4. Verify's three observable effects.
    n_after = _read_neutral(neutral_file)

    # (a) Resolution persisted with TARGET_SPECIFIC.
    res = n_after.resolutions.items.get("identity.reasoning_effort")
    assert res is not None
    assert res.decision is ResolutionDecisionKind.TARGET_SPECIFIC

    # (b) Unified neutral path is unset.
    assert n_after.identity.reasoning_effort is None

    # (c) Each target's value preserved in target_specific.
    claude_bag = n_after.targets.get(BUILTIN_CLAUDE)
    codex_bag = n_after.targets.get(BUILTIN_CODEX)
    assert claude_bag is not None
    assert codex_bag is not None
    assert claude_bag.target_specific.get("identity.reasoning_effort") == "medium"
    assert codex_bag.target_specific.get("identity.reasoning_effort") == "low"
