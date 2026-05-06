# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Interactive conflict resolution** (`InteractiveResolver`). When
  stdin is a TTY and `--on-conflict` was not specified, conflicts
  surface as a 4-source `rich.Table` (was / neutral / per-target) with
  one-letter choices to take any source, revert to last-known-good, or
  skip. Non-interactive flows still use `Strategy`-based resolution
  via `--on-conflict={fail,keep,prefer-neutral,prefer-lkg,prefer=<target>}`.
- **All eight codec lanes live** for both targets (`authorization`,
  `lifecycle`, `interface`, `governance` are no longer
  `NotImplementedError` stubs). Each codec maps the V0 thin slice
  declared in the design spec and emits typed `LossWarning`s for the
  upstream surfaces it does not propagate, rather than silently
  dropping data:
  - **Claude authorization** — `default_mode`, filesystem allow/deny,
 *11network allow/deny + local-binding, and the
 *11`{allow,ask,deny}_patterns` lists.
  - **Codex authorization** — `default_mode` ↔ `sandbox_mode`,
 *11`filesystem.allow_write` ↔ `[sandbox_workspace_write].writable_roots`.
 *11Pattern lists `LossWarning`-only (Codex's named profile model is
 *11the subject of a follow-on spec).
  - **Lifecycle** — `cleanup_period_days` (Claude),
 *11`history.{persistence,max_bytes}` (Codex).
  - **Interface** — fullscreen / status line / voice (Claude),
 *11`tui.theme` / `tui.alternate_screen` / `file_opener` (Codex).
  - **Governance** — `updates.{channel,minimum_version}` (Claude),
 *11`[features]` + `[projects.<path>].trust_level` (Codex).
- **Self-contained codex codegen.** `tools/sync-schemas/sync.py codex`
  now auto-clones `openai/codex` at the pinned SHA into
  `vendor/codex-rs/` (gitignored), drops the schema-dump example
  inside that workspace where its `Cargo.lock` resolves transitive
  deps, captures stdout, removes the example. No `CODEX_RS_PATH`
  environment variable required.
- **GitHub Actions CI** at `.github/workflows/ci.yml`. Runs the four
  verification gates (`ruff check`, `ruff format --check`, `ty check`,
  `pytest`) on a 2×2 matrix of `{ubuntu-latest, macos-latest}` ×
  `{python 3.12, python 3.13}`. The schema-sync pipeline is
  intentionally excluded from CI.
- **`Resolver` Protocol** for the merge engine, accepting either a
  `Strategy` (non-interactive) or an `InteractiveResolver`.
  `MergeEngine` keeps its `strategy=` constructor for back-compat.

### Changed

- `MergeEngine.__init__` now accepts `resolver=` in addition to
  `strategy=`. The CLI auto-picks `InteractiveResolver` when stdin is
  a TTY and `--on-conflict` was not specified, falling back to
  `NonInteractiveResolver(Strategy(FAIL))` otherwise.
- Both target assemblers route the new authorization, lifecycle,
  interface, and governance keys through `disassemble` /
  `assemble`. The schema-drift exemption list is unchanged
  (`claude/capabilities` only — its `mcpServers` claim still spans
  files outside the modelled `settings.json`).

### Removed

- `tests/property/test_claude_stubs.py` and
  `tests/property/test_codex_stubs.py`. Replaced by
  `tests/property/test_deferred_domains.py` exercising real
  round-trips on the formerly-stub codecs.

## [0.1.0] — 2026-05-05

### Added

- Eight-domain neutral schema (identity, directives, capabilities,
  authorization, environment, lifecycle, interface, governance) with
  profiles overlay and per-target pass-through namespace.
- V0 codecs: `identity`, `directives.commit_attribution` +
  `directives.system_prompt_file`, `capabilities.mcp_servers`,
  `environment.variables` for both Claude and Codex targets. Stub
  codecs raise `NotImplementedError` for the four deferred domains.
- Upstream-canonized typing pipeline: `tools/sync-schemas/` with
  pinned schemastore.org Claude schema and a Rust example that dumps
  codex-rs `ConfigToml` via `schemars`; both produce vendored
  `_generated.py` Pydantic models.
- Per-target git state-repos at `$XDG_STATE_HOME/chameleon/targets/`.
- Four-source merge engine with conflict classification and
  non-interactive resolution (FAIL / KEEP / PREFER_TARGET /
  PREFER_NEUTRAL / PREFER_LKG).
- Transaction markers and login-time notices for unattended runs.
- Partial-ownership concurrency discipline for `~/.claude.json`.
- CLI: `init`, `merge`, `status`, `diff`, `log`, `adopt`, `discard`,
  `validate`, `doctor`, `targets list`.
- 132 tests across unit, property, integration, conflicts, recovery,
  concurrency, schema_drift, and typing audit suites.

### V0 Limitations (carried forward into Unreleased)

- Merge classification operates at domain granularity rather than
  per-FieldPath; subsequent merges of `dict[TargetId, V]` fields
  (like `identity.model`) detect benign cross-target "drift" because
  each target's reverse-codec produces only its own entry. Use
  `--on-conflict=keep` for idempotent re-runs until per-FieldPath
  classification ships alongside the deeper authorization codec.
- `chameleon diff` and `chameleon discard` ship as stubs; full
  semantics are pending design.
- Project-scope neutral file (`.chameleon/neutral.yaml`) and
  `chameleon profile use` are deferred.
- Tested on Linux + macOS only; Windows untested (`fcntl`-based
  partial-ownership writes are POSIX-only by design).
