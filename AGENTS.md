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

Before claiming any task complete, ALL four must pass locally:

1. `uv run ruff check`
2. `uv run ruff format --check`
3. `uv run ty check`
4. `uv run pytest`

The `tests/typing_audit.py` test enforces the "everything is typed —
no strings" rule. Do not weaken it; if it fires, fix the production
code, not the test.

The same four gates run in GitHub Actions on every push and pull
request (`.github/workflows/ci.yml`) on a `{ubuntu, macos} × {3.12,
3.13}` matrix. A green local run is necessary but not sufficient —
matrix differences (filesystem casing, line endings, Python minor
behaviour) are caught by CI. Wait for the matrix before merging.

CI does **not** run `tools/sync-schemas/`. Refreshing the
upstream-canonized schemas is a deliberate operator action; the
`_generated.py` artefacts are vendored. If you bump
`tools/sync-schemas/pins.toml`, regenerate locally and commit the
artefact in the same PR.

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

## Pre-commit hook (opt-in)

A defensive guard against committing raw git conflict markers lives at
`tools/pre-commit-no-conflict-markers.sh`. It is **not** auto-installed
— operators opt in. To enable it for your local clone:

```sh
ln -s ../../tools/pre-commit-no-conflict-markers.sh .git/hooks/pre-commit
```

The hook inspects only the staged diff and rejects the commit (fast,
locally) if any added line is a raw conflict marker. The same rule is
enforced server-side by the `no-conflict-markers` workflow and by
`tests/integration/test_no_conflict_markers.py` — three layers, one
rule. The hook is the cheapest of the three; install it.

## Schema discipline

The neutral schema is centrally defined in `src/chameleon/schema/`.
Codecs adapt to the schema; codecs do not redefine it. The
generated `_generated.py` files under `src/chameleon/codecs/<target>/`
are check-in artefacts produced by `tools/sync-schemas/sync.py` —
never hand-edit them; bump `tools/sync-schemas/pins.toml` and
regenerate instead.
