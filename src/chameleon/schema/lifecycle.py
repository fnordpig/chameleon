"""lifecycle domain — events around agent actions (hooks, telemetry, history).

Hooks (P1-B, parity-gap.md):
  Real Claude `~/.claude/settings.json` files carry an ``hooks`` object
  whose keys are PascalCase event names (``PreToolUse``,
  ``PostToolUse``, ``Stop``, ``SessionStart``, ...) and whose values are
  lists of ``HookMatcher`` entries — each with an optional ``matcher``
  regex against the tool name and a list of ``HookCommand`` actions.

  V0 thin slice: we model the ``command``-typed handler — the shape the
  exemplar uses (``{"type": "command", "command": "rtk hook claude"}``).
  Upstream Claude additionally documents ``prompt``, ``agent``, ``http``,
  and ``mcp_tool`` types; encountering those in disassembly produces a
  ``LossWarning`` and the entry is dropped (the operator's neutral file
  stays clean rather than carrying half-modelled action variants).
  When a real-world need for those richer types lands, extend this
  union — do not lower the cutoff or drop fixture-shaped data.

  Codex side: Codex does not currently expose a hooks ABI in its
  published config schema. Setting ``lifecycle.hooks`` in neutral and
  rendering to Codex emits a ``LossWarning`` referencing P1-B; the
  Codex-side codec lands when upstream publishes a hooks schema.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class HistoryPersistence(Enum):
    SAVE_ALL = "save-all"
    NONE = "none"


class History(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persistence: HistoryPersistence | None = None
    max_bytes: int | None = Field(default=None, ge=0)


class TelemetryExporter(Enum):
    NONE = "none"
    OTLP_HTTP = "otlp-http"
    OTLP_GRPC = "otlp-grpc"


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exporter: TelemetryExporter | None = None
    endpoint: str | None = None


class HookCommandShell(BaseModel):
    """The ``type: command`` hook variant — a shell command invocation.

    This is the only HookCommand variant V0 fully models; other upstream
    types (``prompt``, ``agent``, ``http``, ``mcp_tool``) trigger a
    LossWarning at disassembly time. ``extra="allow"`` lets us survive
    minor upstream additions (e.g. a new optional field) without losing
    the field on round-trip — Pydantic preserves it on the model.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["command"] = "command"
    command: str
    timeout: float | None = None


# Discriminated union over the supported HookCommand types. V0 ships
# only the ``command`` variant; other upstream variants are explicitly
# unmodelled and dropped at disassembly with a LossWarning. When a future
# ticket extends the union, add the new variant model above and append it
# to the Annotated union below.
HookCommand = Annotated[HookCommandShell, Field(discriminator="type")]


class HookMatcher(BaseModel):
    """One matcher within an event — pairs a tool-name regex with handlers.

    ``matcher`` is an optional regex against the tool/event context (per
    Claude docs: ``"Bash"`` matches the Bash tool, ``"Edit|Write"``
    matches either, ``None`` matches all). ``hooks`` is the action list.
    """

    model_config = ConfigDict(extra="allow")

    matcher: str | None = None
    hooks: list[HookCommand] = Field(default_factory=list)


class Hooks(BaseModel):
    """Event-keyed hook bindings.

    Field names are snake_case in neutral; targets that need PascalCase
    (Claude) or other casings translate in their codec. The set of
    events here mirrors Claude's documented hook events; targets with
    smaller event sets emit a LossWarning for the unsupported ones.

    ``extra="allow"`` keeps unmodelled upstream events round-tripping
    via Pydantic's extras storage rather than vanishing.
    """

    model_config = ConfigDict(extra="allow")

    pre_tool_use: list[HookMatcher] | None = None
    post_tool_use: list[HookMatcher] | None = None
    notification: list[HookMatcher] | None = None
    user_prompt_submit: list[HookMatcher] | None = None
    stop: list[HookMatcher] | None = None
    subagent_stop: list[HookMatcher] | None = None
    pre_compact: list[HookMatcher] | None = None
    session_start: list[HookMatcher] | None = None
    session_end: list[HookMatcher] | None = None


class Lifecycle(BaseModel):
    """Lifecycle events — hooks, telemetry, history, cleanup."""

    model_config = ConfigDict(extra="forbid")

    hooks: Hooks = Field(default_factory=Hooks)
    history: History = Field(default_factory=History)
    telemetry: Telemetry = Field(default_factory=Telemetry)
    cleanup_period_days: int | None = Field(default=None, ge=0)


__all__ = [
    "History",
    "HistoryPersistence",
    "HookCommand",
    "HookCommandShell",
    "HookMatcher",
    "Hooks",
    "Lifecycle",
    "Telemetry",
    "TelemetryExporter",
]
