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

**0.4.0 — pre-1.0, parity-gap DAG closed: LCD authorization shipped, zero strict xfails.**

All eight schema domains — `identity`, `directives`, `capabilities`,
`environment`, `authorization`, `lifecycle`, `interface`,
`governance` — have working codecs for both targets, with documented
`LossWarning`s where the two targets genuinely diverge. The original
parity-gap DAG is closed: Wave-13's LCD (lowest-common-denominator)
authorization scheme covers the structurally-common subset
(`permission_mode` Claude-side, `sandbox_mode` and `approval_policy`
Codex-side) losslessly on its claiming target and emits typed
`LossWarning`s on cross-target encode. The rich cases — Claude's
`Bash(...)` pattern-language permissions, Codex's named
`[permissions.<name>]` profiles, Codex's granular discriminated-union
approval — are intentionally NOT translated; they ride pass-through
byte-faithfully and do not propagate cross-target. See
`docs/superpowers/specs/2026-05-06-p3-authorization-design.md` for
the design exploration.

The 0.4.0 verification posture is **exhaustive proof + property fuzz**:

- **2119/2119** upstream wire fields statically accounted for (no
  silent drops audit, unchanged from 0.3.0; Codex `claimed
  171 → 178`, `pass-through 528 → 521` reflects the LCD prefix-claims).
- **Every finite-domain enum / `Literal` reachable from the neutral
  schema** proved bijective by enumeration, including Wave-13's new
  `PermissionMode` (Claude) and `ApprovalPolicy` (Codex), and the
  renamed `SandboxMode` (Codex).
- **Six Hypothesis-driven fuzzer families** covering per-codec
  round-trip, cross-target unification differential, pass-through
  deep-nesting, the merge engine state machine, and Unicode broadside.

The test suite is **463 passing + 35 skipped + 70 fuzz tests
(deselected by default)**. **Zero strict-xfails** remain on the
default suite. The fuzz suite runs nightly in CI under
`uv run pytest -m fuzz`. See `CHANGELOG.md` for the per-wave
breakdown and `docs/superpowers/specs/2026-05-05-chameleon-design.md`
for the architecture.

### What's verified end-to-end

`tests/integration/test_exemplar_byte_roundtrip.py` runs the full
operator workflow against a sanitized real-world Claude+Codex exemplar
(`tests/fixtures/exemplar/`) and asserts:

- **Semantic round-trip.** `chameleon init && chameleon merge
  --on-conflict=keep` on the live Claude `settings.json`, Codex
  `config.toml`, and `~/.claude.json` produces the same semantic
  content modulo the documented Wave-5 transforms (P1-D
  legacy-attribution consolidation, P1-A capabilities reconciliation
  union, B2 sorted-by-key dict ordering, cosmetic empty blocks).
- **Byte-stable idempotency.** Two consecutive `keep`-merges produce
  byte-identical target files.
- **Full Unicode preservation.** Every non-ASCII codepoint in the
  original `~/.claude.json` survives the merge.
- **Zero unexpected pass-through.** After Wave-4, every claimed key
  has a real codec — `targets.<target>.items` is empty after
  round-trip.

`tests/integration/test_login_recipes.py` pins the login recipes
shipped in `docs/login/*.md` to the live CLI surface so the published
invocations don't drift.

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
| `chameleon diff` | Unified diff of live targets vs. re-derived projection of neutral |
| `chameleon discard <target>` | Revert a target's live file to its state-repo HEAD |
| `chameleon merge --dry-run` | Run the full pipeline and emit the diff without writing |

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
