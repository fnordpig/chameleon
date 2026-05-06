# Chameleon

> One neutral configuration; many AI coding agents. Bidirectional.

Chameleon transpiles a single neutral YAML configuration into Claude
Code's `settings.json` (+ `~/.claude.json`) and OpenAI Codex CLI's
`config.toml` (and back again). When an agent edits its own
configuration at runtime, Chameleon detects the drift, prompts you to
resolve any conflict (interactively or via a configured strategy), and
re-derives every other agent's view so your intent stays consistent
across tools.

## Status

Pre-1.0 but functional end-to-end. All eight schema domains —
`identity`, `directives`, `capabilities`, `environment`,
`authorization`, `lifecycle`, `interface`, `governance` — have working
codecs for both targets, with documented `LossWarning`s where the two
targets genuinely diverge. The richer authorization surface (Claude's
`Bash(...)` permission patterns ↔ Codex's named `[permissions.<name>]`
profiles) ships as `LossWarning`-only for now and gets its own design
spec. See `CHANGELOG.md` for the full V0+ delta and `docs/superpowers/
specs/2026-05-05-chameleon-design.md` for the architecture.

## Why YAML?

The neutral form is YAML so that operator-authored comments and anchors
survive a round-trip; both target formats (Claude's JSON and Codex's
TOML) are also written via formatters that preserve key order — and
TOML preserves comments — so `git diff` on live target files stays
informative rather than noisy.

## Quick start

Every command goes through `uv` — this is a uv-wrapped project.

```sh
uv sync
uv run chameleon init     # writes ~/.config/chameleon/neutral.yaml
$EDITOR ~/.config/chameleon/neutral.yaml
uv run chameleon merge    # transpiles into ~/.claude/settings.json + ~/.codex/config.toml
```

A minimal neutral file:

```yaml
schema_version: 1
identity:
  reasoning_effort: high
  model:
    claude: claude-sonnet-4-7
    codex: gpt-5.4
environment:
  variables:
    CI: "true"
```

After `chameleon merge`:

- `~/.claude/settings.json` contains `"model": "claude-sonnet-4-7"`,
  `"effortLevel": "high"`, `"env": {"CI": "true"}`.
- `~/.codex/config.toml` contains `model = "gpt-5.4"`,
  `model_reasoning_effort = "high"`, and the env under
  `[shell_environment_policy.set]`.

If a target's live file drifts (say you edit `~/.codex/config.toml`
directly), the next `merge` detects it. On a TTY without an explicit
`--on-conflict`, you get an interactive 4-source diff (was / neutral /
per-target) with one-letter choices to take any source, revert to
last-known-good, or skip. Non-interactive runs use
`--on-conflict={fail,keep,prefer-neutral,prefer-lkg,prefer=<target>}`.

## CLI

| Command | Purpose |
|---|---|
| `chameleon init` | Bootstrap the neutral file and per-target state-repos |
| `chameleon merge` | Run the round-trip: read live targets → resolve conflicts → re-derive |
| `chameleon status` | Show drift between live target files and state-repo HEADs |
| `chameleon log <target>` | Show merge / adopt history for one target |
| `chameleon adopt <target>` | Pull a target's live state into neutral |
| `chameleon validate` | Lint the neutral file against the Pydantic schema |
| `chameleon doctor` | Surface stale transaction markers and login-time notices |
| `chameleon targets list` | List registered targets (built-ins + plugins) |
| `chameleon diff` / `discard` | V0 stubs — full semantics deferred |

## Login-time integration

`docs/login/` contains drop-in recipes for `launchd` (macOS),
`systemd --user` (Linux), and shell `rc` snippets. The recommended
non-interactive invocation for unattended runs is
`chameleon merge --on-conflict=fail --quiet`; failures are persisted
as typed `LoginNotice` records that `chameleon doctor` surfaces on
your next interactive shell.

## Plugin authoring

Adding a third agent target is a plugin install away — see
`docs/plugins/authoring.md`. A target plugin ships a `Target` class
(eight codecs + an assembler), registers via the
`chameleon.targets` entry point, and vendors its own upstream-derived
`_generated.py` Pydantic model.

## Development

```sh
uv run pytest             # full test suite (unit / property / integration / conflicts / ...)
uv run ruff check
uv run ruff format --check
uv run ty check
```

CI runs all four gates on Linux + macOS for Python 3.12 and 3.13 — see
`.github/workflows/ci.yml`. CI deliberately does **not** exercise the
schema-sync pipeline (Rust + network); refresh upstream-canonized
schemas locally:

```sh
uv run --group schema-sync python tools/sync-schemas/sync.py claude
uv run --group schema-sync python tools/sync-schemas/sync.py codex   # auto-clones codex-rs
```

The codex sync auto-clones `openai/codex` at the SHA pinned in
`tools/sync-schemas/pins.toml` into `vendor/codex-rs/` (gitignored), so
you don't need a manual checkout — just a working `cargo` and network.

See `AGENTS.md` for full contribution conventions (also symlinked at
`CLAUDE.md` for Claude Code's per-repo guidance).

## License

MIT — see `LICENSE`.
