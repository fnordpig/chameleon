"""Claude codec for the lifecycle domain.

V0 surface:
  cleanup_period_days  ↔ cleanupPeriodDays
  hooks                ↔ hooks  (P1-B, parity-gap.md)

Hooks are first-class as of the section serializes to/from
Claude's documented event-keyed shape (PascalCase event names ->
list of matcher objects). Only the ``command`` HookCommand variant is
modelled in V0; encountering ``prompt`` / ``agent`` / ``http`` /
``mcp_tool`` at disassembly emits a LossWarning and drops the entry.

The remaining lifecycle surface (telemetry, history) sits behind
LossWarning paths until the dedicated lifecycle spec lands.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.lifecycle import (
    HookCommand,
    HookCommandShell,
    HookMatcher,
    Hooks,
    Lifecycle,
)


class _ClaudeHookCommandShell(BaseModel):
    """The Claude on-disk shape of a hook entry.

    ``type`` and ``command`` are both optional at the validation layer
    so that operators with non-``command`` types (``prompt``, ``agent``,
    ``http``, ``mcp_tool``) parse cleanly — the codec then inspects
    ``type`` and drops non-``command`` variants with a LossWarning
    rather than crashing on a missing ``command`` field.

    ``extra="allow"`` mirrors the upstream schema and lets fields we
    don't model (e.g. ``async``, ``shell``, ``if``) carry forward via
    Pydantic's extras storage rather than vanishing on round-trip.
    """

    model_config = ConfigDict(extra="allow")

    type: str = "command"
    command: str | None = None
    timeout: float | None = None


class _ClaudeHookMatcher(BaseModel):
    """One matcher entry within a Claude hooks event."""

    model_config = ConfigDict(extra="allow")

    matcher: str | None = None
    hooks: list[_ClaudeHookCommandShell] = Field(default_factory=list)


# Mapping between neutral snake_case event names and Claude PascalCase
# wire names. Single source of truth for both directions of the codec
# AND for the lifecycle_keys assembler routing.
_NEUTRAL_TO_WIRE: dict[str, str] = {
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "notification": "Notification",
    "user_prompt_submit": "UserPromptSubmit",
    "stop": "Stop",
    "subagent_stop": "SubagentStop",
    "pre_compact": "PreCompact",
    "session_start": "SessionStart",
    "session_end": "SessionEnd",
}
_WIRE_TO_NEUTRAL: dict[str, str] = {wire: neutral for neutral, wire in _NEUTRAL_TO_WIRE.items()}


class ClaudeHooksSection(BaseModel):
    """The Claude ``hooks`` object — event-keyed map of matcher lists.

    Field names use the upstream PascalCase wire keys directly; ``noqa:
    N815`` suppresses naming-convention noise. The assembler dumps with
    ``exclude_none=True`` so unset events vanish from settings.json.
    """

    model_config = ConfigDict(extra="allow")

    PreToolUse: list[_ClaudeHookMatcher] | None = None
    PostToolUse: list[_ClaudeHookMatcher] | None = None
    Notification: list[_ClaudeHookMatcher] | None = None
    UserPromptSubmit: list[_ClaudeHookMatcher] | None = None
    Stop: list[_ClaudeHookMatcher] | None = None
    SubagentStop: list[_ClaudeHookMatcher] | None = None
    PreCompact: list[_ClaudeHookMatcher] | None = None
    SessionStart: list[_ClaudeHookMatcher] | None = None
    SessionEnd: list[_ClaudeHookMatcher] | None = None


class ClaudeLifecycleSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    cleanupPeriodDays: int | None = None  # noqa: N815
    hooks: ClaudeHooksSection | None = None


class ClaudeLifecycleCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.LIFECYCLE
    target_section: ClassVar[type[BaseModel]] = ClaudeLifecycleSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("cleanupPeriodDays",)),
            FieldPath(segments=("hooks",)),
        }
    )

    @staticmethod
    def to_target(model: Lifecycle, ctx: TranspileCtx) -> ClaudeLifecycleSection:
        section = ClaudeLifecycleSection()
        if model.cleanup_period_days is not None:
            section.cleanupPeriodDays = model.cleanup_period_days
        hooks_section = _hooks_to_target(model.hooks)
        if hooks_section is not None:
            section.hooks = hooks_section
        if model.telemetry.exporter is not None or model.telemetry.endpoint is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CLAUDE,
                    message="lifecycle.telemetry not propagated to Claude in V0",
                )
            )
        # lifecycle.history.persistence has no Claude analogue
        # at the settings-file level. Claude does expose
        # CLAUDE_CODE_SKIP_PROMPT_HISTORY as an environment variable that
        # toggles transcript writes entirely, but that lives under the
        # ``env`` key (owned by ClaudeEnvironmentCodec) and is a binary
        # on/off rather than the persistence enum's ``save-all``/``none``
        # axis. Surface the drop as a typed warning rather than re-route
        # through env (which would require cross-codec coupling).
        if model.history.persistence is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        f"lifecycle.history.persistence "
                        f"({model.history.persistence.value!r}) has no Claude "
                        "settings.json analogue (the closest analogue is the "
                        "CLAUDE_CODE_SKIP_PROMPT_HISTORY env var, owned by the "
                        "environment codec); dropping during to_target."
                    ),
                    field_path=FieldPath(segments=("history", "persistence")),
                )
            )
        if model.history.max_bytes is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "lifecycle.history.max_bytes has no Claude settings.json "
                        "analogue; dropping during to_target."
                    ),
                    field_path=FieldPath(segments=("history", "max_bytes")),
                )
            )
        return section

    @staticmethod
    def from_target(section: ClaudeLifecycleSection, ctx: TranspileCtx) -> Lifecycle:
        hooks = _hooks_from_target(section.hooks, ctx) if section.hooks is not None else Hooks()
        return Lifecycle(cleanup_period_days=section.cleanupPeriodDays, hooks=hooks)


def _hooks_to_target(hooks: Hooks) -> ClaudeHooksSection | None:
    """Render neutral Hooks -> ClaudeHooksSection. Returns None if empty.

    Empty (no events set) means the operator hasn't configured hooks;
    we skip writing the key to settings.json entirely.
    """
    out = ClaudeHooksSection()
    any_set = False
    for neutral_name, wire_name in _NEUTRAL_TO_WIRE.items():
        matchers = getattr(hooks, neutral_name, None)
        if matchers is None:
            continue
        rendered = [_matcher_to_target(m) for m in matchers]
        setattr(out, wire_name, rendered)
        any_set = True
    return out if any_set else None


def _matcher_to_target(m: HookMatcher) -> _ClaudeHookMatcher:
    return _ClaudeHookMatcher(
        matcher=m.matcher,
        hooks=[_command_to_target(c) for c in m.hooks],
    )


def _command_to_target(c: HookCommand) -> _ClaudeHookCommandShell:
    # Only the shell-command variant is modelled in V0.
    assert isinstance(c, HookCommandShell)
    return _ClaudeHookCommandShell(
        type=c.type,
        command=c.command,
        timeout=c.timeout,
    )


def _hooks_from_target(section: ClaudeHooksSection, ctx: TranspileCtx) -> Hooks:
    hooks = Hooks()
    for wire_name, neutral_name in _WIRE_TO_NEUTRAL.items():
        raw = getattr(section, wire_name, None)
        if raw is None:
            continue
        converted = [_matcher_from_target(m, wire_name, ctx) for m in raw]
        setattr(hooks, neutral_name, converted)
    # Surface any extras the schema doesn't model so the operator can
    # see they aren't propagated in V0.
    extras = getattr(section, "model_extra", None) or {}
    for extra_event in extras:
        ctx.warn(
            LossWarning(
                domain=Domains.LIFECYCLE,
                target=BUILTIN_CLAUDE,
                message=(
                    f"hooks event {extra_event!r} not modelled in V0; "
                    "extending the neutral Hooks schema is the right fix"
                ),
                field_path=FieldPath(segments=("hooks", extra_event)),
            )
        )
    return hooks


def _matcher_from_target(m: _ClaudeHookMatcher, wire_event: str, ctx: TranspileCtx) -> HookMatcher:
    commands: list[HookCommand] = []
    for raw_cmd in m.hooks:
        # raw_cmd was validated against `_ClaudeHookCommandShell`, which
        # uses ``extra="allow"`` and a default ``type="command"``. If the
        # operator wrote a non-command type, ``raw_cmd.type`` carries
        # that string and we drop with a LossWarning rather than coerce.
        if raw_cmd.type != "command":
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        f"hook event {wire_event!r}: HookCommand type "
                        f"{raw_cmd.type!r} not modelled in V0 (P1-B "
                        "supports `command`); dropping"
                    ),
                    field_path=FieldPath(segments=("hooks", wire_event)),
                )
            )
            continue
        if raw_cmd.command is None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        f"hook event {wire_event!r}: type=command entry "
                        "missing required `command` field; dropping"
                    ),
                    field_path=FieldPath(segments=("hooks", wire_event)),
                )
            )
            continue
        commands.append(
            HookCommandShell(type="command", command=raw_cmd.command, timeout=raw_cmd.timeout)
        )
    return HookMatcher(matcher=m.matcher, hooks=commands)


__all__ = ["ClaudeHooksSection", "ClaudeLifecycleCodec", "ClaudeLifecycleSection"]
