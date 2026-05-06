# Agent Conventions for Chameleon

These instructions apply to any AI agent working on this repository
(Claude Code, Codex CLI, future agents). `CLAUDE.md` is a symlink to
this file.

## Project goal

Chameleon is an MIT-licensed Python tool that maintains a single neutral
YAML configuration and bidirectionally synchronizes it with Claude Code
and Codex CLI. Round-trip is the design centerpiece: when an agent
edits its own config at runtime, Chameleon detects the drift, resolves
any conflict, absorbs the change into neutral, and re-derives every
other target.

See `docs/superpowers/specs/2026-05-05-chameleon-design.md` for the
full design.

## Runtime assumption

Every command runs through `uv run`. Bare `python`, `pip`, and `pytest`
invocations are anti-patterns. Use:

- `uv sync` to install dependencies
- `uv run pytest` to run tests
- `uv run ruff check` and `uv run ruff format --check` for lint/format
- `uv run ty check` for type checking
- `uv run chameleon ...` to invoke the CLI

## Verification gates

Before claiming any task complete, ALL four must pass:

1. `uv run ruff check`
2. `uv run ruff format --check`
3. `uv run ty check`
4. `uv run pytest`

The `tests/typing_audit.py` test enforces the "everything is typed —
no strings" rule. Do not weaken it; if it fires, fix the production
code, not the test.

## Search tooling

Prefer ripvec / semantic search over raw grep when exploring code
semantically. The repo is indexed at the repo level.

## Round-trip orientation

Every codec must round-trip its inputs. The canonical test is
`from_target(to_target(x)) == canonicalize(x)` for all valid `x`.
If a codec is genuinely lossy, mark the lossy axes explicitly and
emit a `LossWarning` at runtime — never silently drop data.

The pass-through namespace (`targets.<target>.*` in neutral) is the
escape hatch for genuine target-unique features. It is parametric
over target so target-native types (TOML datetimes, structured enums
from `_generated.py`) survive round-trip.

## Conflict UX

A-or-B per neutral key. No inline editing, no three-way text merging.
If a future feature needs richer resolution, design it as a separate
spec, not a runtime patch.

## Schema discipline

The neutral schema is centrally defined in `src/chameleon/schema/`.
Codecs adapt to the schema; codecs do not redefine it. The
generated `_generated.py` files under `src/chameleon/codecs/<target>/`
are check-in artefacts produced by `tools/sync-schemas/sync.py` —
never hand-edit them; bump `tools/sync-schemas/pins.toml` and
regenerate instead.
