"""P1-B regression tests: lifecycle.hooks as a real codec, not LossWarning-only.

The exemplar at tests/fixtures/exemplar/home/_claude/settings.json carries:

    "hooks": {
      "PreToolUse": [
        {
          "matcher": "Bash",
          "hooks": [{"type": "command", "command": "rtk hook claude"}]
        }
      ]
    }

Before P1-B that whole object dropped to pass-through (or ate a
LossWarning, depending on path). After P1-B the lifecycle codec owns
it. These tests pin:

  * Round-trip equality of single-event and multi-event hook configs.
  * The exemplar's exact shape disassembles into ``Domains.LIFECYCLE``,
    not into pass-through.
  * Codex side: a neutral ``lifecycle.hooks`` config emits a
    LossWarning that names P1-B and explains why (no Codex hooks ABI).
  * Unmodelled HookCommand types (prompt/agent/http/mcp_tool) drop with
    a LossWarning rather than crashing or silently surviving.
"""

from __future__ import annotations

import json
from pathlib import Path

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.lifecycle import (
    ClaudeLifecycleCodec,
    ClaudeLifecycleSection,
)
from chameleon.codecs.codex.lifecycle import CodexLifecycleCodec
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains
from chameleon.schema.lifecycle import (
    HookCommandShell,
    HookMatcher,
    Hooks,
    Lifecycle,
)
from chameleon.targets.claude.assembler import ClaudeAssembler

_EXEMPLAR_SETTINGS = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "exemplar"
    / "home"
    / "_claude"
    / "settings.json"
)


# ---- Round-trip --------------------------------------------------------------


def test_round_trip_pre_tool_use_bash_command() -> None:
    """The exact exemplar shape: PreToolUse / matcher=Bash / rtk hook claude."""
    orig = Lifecycle(
        hooks=Hooks(
            pre_tool_use=[
                HookMatcher(
                    matcher="Bash",
                    hooks=[HookCommandShell(command="rtk hook claude")],
                )
            ]
        )
    )
    ctx = TranspileCtx()
    section = ClaudeLifecycleCodec.to_target(orig, ctx)
    restored = ClaudeLifecycleCodec.from_target(section, ctx)

    assert restored.hooks.pre_tool_use is not None
    assert len(restored.hooks.pre_tool_use) == 1
    matcher = restored.hooks.pre_tool_use[0]
    assert matcher.matcher == "Bash"
    assert len(matcher.hooks) == 1
    cmd = matcher.hooks[0]
    assert isinstance(cmd, HookCommandShell)
    assert cmd.type == "command"
    assert cmd.command == "rtk hook claude"
    assert ctx.warnings == []


def test_round_trip_multi_event_hooks() -> None:
    """Multiple events at once must all survive round-trip."""
    orig = Lifecycle(
        hooks=Hooks(
            pre_tool_use=[
                HookMatcher(
                    matcher="Bash",
                    hooks=[HookCommandShell(command="rtk hook claude")],
                )
            ],
            post_tool_use=[
                HookMatcher(
                    matcher="Edit|Write",
                    hooks=[HookCommandShell(command="echo edited")],
                )
            ],
            stop=[
                HookMatcher(
                    matcher=None,
                    hooks=[HookCommandShell(command="cleanup.sh", timeout=30.0)],
                )
            ],
        )
    )
    ctx = TranspileCtx()
    section = ClaudeLifecycleCodec.to_target(orig, ctx)
    restored = ClaudeLifecycleCodec.from_target(section, ctx)

    assert restored.hooks.pre_tool_use is not None
    assert restored.hooks.post_tool_use is not None
    assert restored.hooks.stop is not None
    assert restored.hooks.pre_tool_use[0].hooks[0].command == "rtk hook claude"
    assert restored.hooks.post_tool_use[0].matcher == "Edit|Write"
    assert restored.hooks.stop[0].matcher is None
    assert restored.hooks.stop[0].hooks[0].timeout == 30.0


def test_round_trip_preserves_cleanup_period_days_alongside_hooks() -> None:
    """Pre-existing lifecycle field must keep working after the schema change."""
    orig = Lifecycle(
        cleanup_period_days=14,
        hooks=Hooks(session_start=[HookMatcher(hooks=[HookCommandShell(command="bootstrap.sh")])]),
    )
    ctx = TranspileCtx()
    section = ClaudeLifecycleCodec.to_target(orig, ctx)
    restored = ClaudeLifecycleCodec.from_target(section, ctx)
    assert restored.cleanup_period_days == 14
    assert restored.hooks.session_start is not None


def test_empty_hooks_does_not_emit_hooks_key() -> None:
    """A bare Lifecycle(hooks=Hooks()) must not write a ``hooks`` key on disk.

    Otherwise every neutral file would render ``"hooks": {}`` and pollute
    settings.json with empty noise.
    """
    section = ClaudeLifecycleCodec.to_target(Lifecycle(), TranspileCtx())
    assert section.hooks is None


# ---- Assembler routing -------------------------------------------------------


def test_exemplar_hooks_route_to_lifecycle_not_passthrough() -> None:
    """The real exemplar settings.json's ``hooks`` block must land in
    Domains.LIFECYCLE after disassembly, not in the pass-through bag.

    This is the core P1-B routing assertion: the assembler's
    ``lifecycle_keys`` set must include ``hooks``.
    """
    raw_bytes = _EXEMPLAR_SETTINGS.read_bytes()
    files = {ClaudeAssembler.SETTINGS_JSON: raw_bytes}
    per_domain, passthrough = ClaudeAssembler.disassemble(files)

    assert "hooks" not in passthrough, (
        "hooks is routing to pass-through, but P1-B promotes it to a real codec"
    )
    assert Domains.LIFECYCLE in per_domain
    section = per_domain[Domains.LIFECYCLE]
    assert isinstance(section, ClaudeLifecycleSection)
    assert section.hooks is not None
    assert section.hooks.PreToolUse is not None
    assert len(section.hooks.PreToolUse) == 1
    matcher = section.hooks.PreToolUse[0]
    assert matcher.matcher == "Bash"
    assert matcher.hooks[0].command == "rtk hook claude"


def test_exemplar_hooks_round_trip_to_neutral_and_back_to_disk() -> None:
    """End-to-end: exemplar bytes -> neutral Lifecycle -> Claude bytes.

    The PreToolUse/Bash/rtk-hook-claude shape must survive the full
    bytes-in / neutral / bytes-out cycle. A re-serialized section
    written by the assembler must contain the same hook entry.
    """
    raw_bytes = _EXEMPLAR_SETTINGS.read_bytes()
    files = {ClaudeAssembler.SETTINGS_JSON: raw_bytes}
    per_domain, _passthrough = ClaudeAssembler.disassemble(files)

    section = per_domain[Domains.LIFECYCLE]
    assert isinstance(section, ClaudeLifecycleSection)
    neutral = ClaudeLifecycleCodec.from_target(section, TranspileCtx())
    re_section = ClaudeLifecycleCodec.to_target(neutral, TranspileCtx())

    out_files = ClaudeAssembler.assemble(
        per_domain={Domains.LIFECYCLE: re_section},
        passthrough={},
    )
    written = json.loads(out_files[ClaudeAssembler.SETTINGS_JSON].decode("utf-8"))
    assert "hooks" in written
    assert written["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert written["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "rtk hook claude"
    assert written["hooks"]["PreToolUse"][0]["hooks"][0]["type"] == "command"


# ---- Lossy variants ----------------------------------------------------------


def test_unmodelled_hook_command_type_drops_with_loss_warning() -> None:
    """A ``type: prompt`` (or other non-command) entry must drop with a
    LossWarning naming P1-B, not crash and not silently survive.

    This holds the V0 cutoff line: the operator gets told the entry
    isn't propagated, instead of getting a confusing partial render.
    """
    raw = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "prompt", "prompt": "Did you mean to run that?"},
                    ],
                }
            ]
        }
    }
    section = ClaudeLifecycleSection.model_validate(raw)
    ctx = TranspileCtx()
    restored = ClaudeLifecycleCodec.from_target(section, ctx)

    assert restored.hooks.pre_tool_use is not None
    # The matcher survives; the unmodelled command was dropped.
    assert restored.hooks.pre_tool_use[0].hooks == []
    assert any(
        w.target == BUILTIN_CLAUDE and "P1-B" in w.message and "prompt" in w.message
        for w in ctx.warnings
    ), [w.message for w in ctx.warnings]


# ---- Codex side --------------------------------------------------------------


def test_codex_hooks_emits_loss_warning_referencing_p1b() -> None:
    """Codex has no hooks ABI — neutral hooks must produce a typed
    LossWarning that says so and references P1-B."""
    neutral = Lifecycle(
        hooks=Hooks(
            pre_tool_use=[
                HookMatcher(
                    matcher="Bash",
                    hooks=[HookCommandShell(command="rtk hook codex")],
                )
            ]
        )
    )
    ctx = TranspileCtx()
    CodexLifecycleCodec.to_target(neutral, ctx)
    matching = [w for w in ctx.warnings if w.target == BUILTIN_CODEX and "P1-B" in w.message]
    assert matching, [w.message for w in ctx.warnings]
    assert any("Codex" in w.message and "hooks" in w.message for w in matching)


def test_codex_empty_hooks_emits_no_warning() -> None:
    """A bare Lifecycle() (no hook events) must not trigger a Codex
    hooks LossWarning — that would be noise on every default config."""
    ctx = TranspileCtx()
    CodexLifecycleCodec.to_target(Lifecycle(), ctx)
    assert not any("hooks" in w.message for w in ctx.warnings)
