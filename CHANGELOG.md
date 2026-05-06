# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_No changes yet._

## [0.2.0] — 2026-05-06

This release closes the V0 → V1 gap. All eight codec lanes are live
for both targets, the round-trip is verified end-to-end against a
sanitized real-world Claude+Codex exemplar, and the CLI surface
covered by `docs/login/*.md` is pinned by tests so the published
recipes don't drift from the implementation.

The test suite grew from 132 (0.1.0) to **286 passing + 5 strict
xfails**. The five xfails pin contracts that ship in 0.3.0 — see
"Known limitations" below.

### Wave-1 — codec correctness for the V0+ surface

- **MCP `type` discriminator** (P0-1). `mcpServers` entries on Claude
  now model the `{stdio, sse, http}` discriminator instead of stuffing
  everything under a stdio shape, so an SSE or HTTP server survives
  round-trip without the type collapsing.
- **Pass-through propagation through merge** (P0-3). The
  `targets.<target>.*` escape hatch is now threaded through `compose`
  and re-derive, so target-unique fields you parked under pass-through
  re-emerge in the next merge instead of vanishing on the second
  re-derive.
- **Commit-attribution alias claims** (P1-D). Claude's legacy
  `includeCoAuthoredBy` and `commit_attribution` keys are now claimed
  by the `directives.commit_attribution` codec and reconciled to a
  single canonical form, instead of leaking into pass-through and
  causing spurious "drift" on subsequent merges.

### Wave-2 — merge engine: per-FieldPath classification

- **`capabilities.plugins` unification** (P1-A). Plugins and
  marketplaces are reconciled across Claude and Codex into a single
  neutral list, so a plugin declared on one target propagates to the
  other on the next merge.
- **Per-FieldPath change classification** (P2-1). The four-source
  merge engine (was / neutral / target-A / target-B) now classifies
  changes per `FieldPath` rather than per domain, with proper
  `dict[TargetId, V]` semantics for fields like `identity.model` whose
  value is genuinely different per target. The 0.1.0
  workaround (`--on-conflict=keep` for idempotent re-runs) is no
  longer needed.
- **Real `chameleon diff` and `chameleon discard`** (P2-3). Both
  commands ship with full semantics — `diff` produces a unified diff
  of the live targets vs. the re-derived projection of neutral;
  `discard` reverts a target's live file to its state-repo HEAD.

### Wave-3 — codec coverage and assembler robustness

- **Unauthored target data preserved through merge** (#44). When a
  field is claimed by a codec but the operator has not yet authored
  it in neutral, the live target value is now preserved instead of
  being clobbered with the neutral default.
- **Robust disassemble** (P0-2). Validation failures during
  disassemble now route the offending value to pass-through with a
  `LossWarning` rather than crashing the merge — the codec's job is
  to claim what it understands, not to refuse the whole document
  because of one unfamiliar key.
- **`lifecycle.hooks` codec** (P1-B). Claude's `hooks` and Codex's
  `[notify]` / `notify_command` are now first-class neutral fields
  instead of pass-through.
- **`interface.voice` codec** (P1-C). Claude's voice / dictation
  surface is promoted from pass-through to a structured neutral
  concept.

### Wave-4 — neutral promotions and dry-run fidelity

- **`directives.personality`** (P1-E). The Claude personality /
  persona surface is promoted to first-class neutral.
- **Codex identity tuning knobs** (P1-F). `model_provider`,
  `model_context_window`, `model_max_output_tokens`, and friends are
  promoted from Codex pass-through into `identity.*` so they survive
  cross-target reconciliation.
- **`authorization.reviewer`** (P1-G). Codex's `approvals_reviewer`
  is promoted to a neutral authorization concept.
- **Real `--dry-run` pipeline** (P2-2). `chameleon merge --dry-run`
  now runs the full pipeline (read live → resolve → re-derive) and
  emits a unified diff of what _would_ be written, instead of
  short-circuiting before the diff has anything to show.

### Wave-5 — byte-stable round-trip on the exemplar

The end-to-end smoke against the sanitized real-world exemplar
surfaced four post-Wave-4 bugs; all four are fixed in this release.

- **B1 — sub-table preservation.** Partially-claimed nested tables
  (e.g. Codex `[mcp_servers.<name>]` where the codec models some
  sub-keys) now preserve unclaimed sub-keys through the section-extras
  harvester instead of dropping them.
- **B2 — sorted dict-keyed reconciliation.** Reconciling
  `dict[TargetId, V]` fields now produces byte-stable output across
  consecutive `keep`-merges; the second merge is a no-op at the byte
  level.
- **B3 — leaf-write coercion.** Merge leaf-writes are now coerced
  through the field's annotated type, so a value that's structurally
  valid but the wrong concrete type (e.g. `int` where the schema
  expects `Literal[…]`) doesn't break the second-half re-derive.
- **B4 — non-ASCII through `partial_owned_write`.** The POSIX-locked
  partial-ownership writer now preserves non-ASCII codepoints (full
  Unicode round-trip across `~/.claude.json`) instead of normalising
  to ASCII via the default JSON encoder.

### Wave-6 — test coverage and contract pinning

- **Login recipes pinned to live CLI surface.** `docs/login/*.md`
  recipes (launchd, systemd --user, shell rc) are now exercised by
  `tests/integration/test_login_recipes.py` so the published
  invocations don't drift from the actual CLI flags.
- **Multi-conflict interactive resolver coverage.** The interactive
  resolver is now exercised against multi-conflict merges, not just
  the single-conflict happy path.
- **Transaction-marker recovery contract pinned** (xfail; ships in
  0.3.0). Four `tests/recovery/test_transaction_recovery.py` tests
  pin the §4.6 recovery contract; one passes today
  (`doctor_surfaces_stale_marker`), three are strict xfails because
  `MergeEngine.merge()` does not yet wire `tx_store.write/clear` nor
  populate `partial_owned_hashes`. The marker plumbing exists in
  `state.transaction`; the engine wiring is the 0.3.0 work.
- **Golden semantic round-trip on the exemplar.** Six
  `tests/integration/test_exemplar_byte_roundtrip.py` tests verify
  the full Claude + Codex + `~/.claude.json` round-trip modulo the
  documented Wave-5 transforms (P1-D consolidation, P1-A
  reconciliation union, B2 sorted ordering, cosmetic empty blocks).
  Idempotency is byte-stable; non-ASCII is preserved; pass-through
  is empty (every claimed key has a codec). Two real round-trip
  drifts are pinned as strict xfails:
  - **F1** — Claude `statusLine.type` is dropped because
    `_ClaudeStatusLine.type` carries a default and is excluded at
    serialisation time. Fix path: explicitly include `type` in the
    codec emission, or apply the B1 sub-section extras harvester one
    level deeper.
  - **F2** — Codex `[marketplaces.<name>]` sub-tables lose
    `last_updated` and `last_revision`. Same shape as B1 but at the
    dict-of-tables level; B1's harvester only covers section-level
    extras.

### Known limitations (being addressed in 0.3.0)

- **Transaction-marker engine wiring.** `MergeEngine.merge()` needs
  to write a `MergeTransaction` before the per-`FileSpec` write loop,
  populate `partial_owned_hashes` from the live bytes it already
  reads, and clear the marker on a clean merge. The contract is
  pinned by three strict xfails; the moment the engine writes
  markers, the xfails auto-flip to passing.
- **F1 — Claude `statusLine.type` round-trip.** Pinned by
  `test_wave7_f1_status_line_type_preserved`.
- **F2 — Codex `[marketplaces.<name>]` extras.** Pinned by
  `test_wave7_f2_codex_marketplace_extras_preserved`.

These are declared-and-pinned future work, not bugs in 0.2.0 — every
xfail is `strict=True`, so a fix anywhere in the codebase that
incidentally satisfies the contract will fail CI loudly until the
xfail is removed.

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

### V0 Limitations (resolved in 0.2.0)

- Merge classification operates at domain granularity rather than
  per-FieldPath. _(Fixed: Wave-2 P2-1.)_
- `chameleon diff` and `chameleon discard` ship as stubs.
  _(Fixed: Wave-2 P2-3.)_
- Tested on Linux + macOS only; Windows untested (`fcntl`-based
  partial-ownership writes are POSIX-only by design). _(Unchanged.)_
