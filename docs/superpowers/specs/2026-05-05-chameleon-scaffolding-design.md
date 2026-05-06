# Chameleon — Project Scaffolding Design

**Date:** 2026-05-05
**Status:** Approved (awaiting implementation plan)
**Scope:** First commit. Pure scaffolding only. No transpile logic.

## Goal

Establish the project skeleton for Chameleon: an MIT-licensed Python tool that
will eventually transpile a single neutral configuration into agent-specific
configurations for Claude Code (`settings.json`), OpenAI Codex CLI
(`config.toml`), and future agents — and round-trip changes from those
agent-specific files back into the neutral form.

This document specifies *only* the scaffolding work. The design of the neutral
schema, the transpile rules, the round-trip merge semantics, and the CLI
command surface are explicitly **out of scope** and will get their own specs.

## Non-Goals

- No neutral-config schema. Not even a stub TOML example.
- No transpile logic. No `transpilers/` package.
- No round-trip / drift-detection logic.
- No CI configuration (GitHub Actions, etc.).
- No release tooling, version-bumping helpers, or changelog automation.
- No real CLI subcommands. The CLI binary exists and prints help; that is all.

## Repository Layout

```
chameleon/
├── .git/
├── .gitignore
├── LICENSE                                          # MIT, (c) 2026 Robert Waugh
├── README.md                                        # goal, status, uv-based usage
├── AGENTS.md                                        # project conventions for agents
├── CLAUDE.md -> AGENTS.md                           # relative symlink
├── pyproject.toml                                   # hatchling, deps, ruff, pytest, ty
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-05-05-chameleon-scaffolding-design.md  # this file
├── src/
│   └── chameleon/
│       ├── __init__.py                              # __version__ = "0.0.0"
│       └── cli.py                                   # main(): prints help/version, no subcommands
├── tests/
│   └── test_smoke.py                                # imports chameleon, asserts __version__
└── skills/
    └── README.md                                    # placeholder explaining intent
```

## Tooling Configuration

### `pyproject.toml`

- **Build backend:** `hatchling` via `hatch-vcs` is *not* used; version is a literal `0.0.0` in `src/chameleon/__init__.py` and surfaced via `[project] dynamic = ["version"]` with `[tool.hatch.version] path = "src/chameleon/__init__.py"`.
- **Project metadata:**
  - `name = "chameleon"`
  - `requires-python = ">=3.12"`
  - `license = { text = "MIT" }`
  - `authors = [{ name = "Robert Waugh" }]`
  - `description = "Transpile a neutral agent configuration into Claude Code, Codex CLI, and other agent-specific formats — and back again."`
  - `readme = "README.md"`
  - `dependencies = []` (none yet — scaffolding only)
- **Entry point:**
  - `[project.scripts] chameleon = "chameleon.cli:main"`
- **Dev dependency group** (uv-style, [PEP 735]):
  - `[dependency-groups] dev = ["pytest>=8", "ruff>=0.15", "ty>=0.0.34"]`
- **Ruff config:**
  - `line-length = 100`
  - `target-version = "py312"`
  - `[tool.ruff.lint] select = ["E", "F", "I", "UP", "B", "SIM"]`
- **Pytest config:**
  - `testpaths = ["tests"]`
  - `addopts = "-ra --strict-markers --strict-config"`
- **ty config:**
  - `[tool.ty.src] root = "src"` (and `tests` included)
  - Otherwise default settings — strictness will be tuned in a later session.
- **Hatch build:**
  - `[tool.hatch.build.targets.wheel] packages = ["src/chameleon"]`

### `src/chameleon/__init__.py`

```python
__version__ = "0.0.0"
```

### `src/chameleon/cli.py`

A `main()` function suitable as the `[project.scripts]` entry point. It accepts
no real subcommands yet. When invoked it prints a one-line description, the
current version, and a short "no commands implemented yet" message, then exits
0. It uses `argparse` from the stdlib (no `click`/`typer` dependency added at
this stage).

### `tests/test_smoke.py`

Two tests:
1. `test_package_importable` — `import chameleon` works and `chameleon.__version__` is a non-empty string.
2. `test_cli_main_runs` — calling `chameleon.cli.main([])` returns `0` (or completes without raising).

These exist solely to prove that `uv run pytest` is wired up and the package is
importable from the `src/` layout.

### `.gitignore`

Standard Python + uv + IDE + macOS combo:
- `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `build/`, `dist/`
- `.venv/` (uv project venv); `uv.lock` is *not* ignored — we commit it for reproducible dev envs
- `.pytest_cache/`, `.ruff_cache/`, `.ty_cache/`
- `.vscode/`, `.idea/`
- `.DS_Store`

### `LICENSE`

Standard MIT license text. Copyright line: `Copyright (c) 2026 Robert Waugh`.

### `README.md`

Sections:
1. **Chameleon** — one-paragraph elevator pitch (neutral config in, agent-specific configs out, round-trippable).
2. **Status** — "Early-stage scaffolding. No transpile logic implemented yet. The neutral configuration schema is intentionally undefined at this stage; see `docs/superpowers/specs/` for design decisions in flight."
3. **Why TOML for the neutral format** — short paragraph: Codex is already TOML-native, TOML→JSON is trivial, comments survive round-trip. (Aspirational, not yet implemented.)
4. **Running** — `uv sync`, `uv run chameleon`, `uv run pytest`, `uv run ruff check`, `uv run ty check`. Explicitly tells contributors: do not invoke `pip` or bare `python`; everything goes through `uv run`.
5. **License** — MIT, link to `LICENSE`.

### `AGENTS.md`

Project conventions for any agent (Claude, Codex, future). Sections:
1. **Project goal** — same one-paragraph pitch.
2. **Runtime assumption** — every command runs through `uv run`. Do not invoke `pip`, `python`, or `pytest` directly.
3. **Verification gates** — before claiming work is done: `uv run ruff check`, `uv run ty check`, `uv run pytest`. All three must pass.
4. **Search tooling** — prefer ripvec / semantic search over raw grep when exploring code semantically. (Aligns with the user's organization-level instruction.)
5. **Round-trip orientation** — short reminder that the eventual goal is bidirectional: agent-specific changes must be reabsorbable into the neutral form.

### `CLAUDE.md`

A relative symlink: `CLAUDE.md -> AGENTS.md`. Created with `ln -s AGENTS.md CLAUDE.md` from inside the repo root so the symlink target is relative.

### `skills/README.md`

A placeholder explaining that this directory will eventually hold Claude Code
skills shipped *with* the project — likely workflow skills for transpile
testing, schema validation, and round-trip verification. Empty otherwise; no
skills exist yet.

## Initial Commit

A single commit on `main`:

```
chore: scaffold chameleon project

Initial scaffolding only. No transpile logic. Establishes the
project layout, tooling config (uv, hatchling, ruff, ty, pytest),
licensing, and agent-conventions documentation. The neutral-config
schema and transpile rules are deferred to subsequent design specs.
```

The design doc itself (this file) is committed separately as the very first
commit, before scaffolding begins, so it can serve as the reference spec for
the scaffolding commit. Two commits total at the end of this work:

1. `docs: add scaffolding design spec`
2. `chore: scaffold chameleon project`

## Verification

The scaffolding is complete when, from a fresh clone:

```sh
uv sync
uv run chameleon --help        # prints help, exits 0
uv run pytest                  # both smoke tests pass
uv run ruff check              # clean
uv run ty check                # clean
```

All four commands must succeed before the scaffold commit is made.

## Open Questions Deferred to Future Specs

- Neutral configuration schema (TOML keys, types, allowed values).
- Mapping rules from neutral → Claude `settings.json`.
- Mapping rules from neutral → Codex `config.toml`.
- Round-trip merge strategy: how to detect, attribute, and absorb drift.
- CLI subcommand surface (`init`, `apply`, `sync`, `diff`, `validate`?).
- Login-time integration: shell hook? `direnv`? `launchd`? `systemd --user`?
- Plugin model for adding future agents.
- Configuration discovery rules (project vs. user vs. machine scope).

These are intentionally not answered here.
