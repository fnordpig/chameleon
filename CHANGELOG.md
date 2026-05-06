# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

### V0 Limitations

- `authorization`, `lifecycle`, `interface`, `governance` codecs are
  stubbed; the typed schema accepts these domains but live transpile
  is deferred (§15.1–§15.4 of the design spec).
- Project-scope neutral file (`.chameleon/neutral.yaml`) and
  `chameleon profile use` are deferred.
- Interactive conflict resolution UI is not yet implemented; V0 only
  accepts non-interactive `--on-conflict` strategies.
- Merge classification operates at domain granularity rather than
  per-FieldPath; subsequent merges of `dict[TargetId, V]` fields
  (like `identity.model`) detect benign cross-target "drift" because
  each target's reverse-codec produces only its own entry. Use
  `--on-conflict=keep` for idempotent re-runs until per-FieldPath
  classification ships alongside the authorization codec.
- The codex `_generated.py` regeneration via the standalone Rust
  binary at `tools/sync-schemas/codex/` cannot resolve the
  `tokio-tungstenite` `proxy` feature requirement at the pinned
  codex-rs SHA when fetched as a git dependency. Workaround:
  `tools/sync-schemas/sync.py codex` requires `CODEX_RS_PATH`
  pointing at a local codex-rs checkout; it drops a one-shot example
  inside that workspace, builds and runs it under the workspace's
  `Cargo.lock`, captures stdout, and deletes the example.
- Tested on Linux + macOS only; Windows untested.
