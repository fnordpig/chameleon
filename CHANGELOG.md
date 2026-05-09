# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_No changes yet._

## [0.5.3] — 2026-05-09

Fixes from issue triage around plugin compatibility and Codex feature
compatibility.

### Fixed

- **Claude plugins are now runtime-vetted before write.** Entries in
  `capabilities.plugins` now drop on write (or reverse-load) when they
  reference a marketplace not declared in `plugin_marketplaces`/built-ins or
  when they are not present in the local Claude `installed_plugins.json`
  cache. This removes stale Codex-only plugin keys from Claude `enabledPlugins`
  and prevents startup failures like `Plugin foo@bar is not cached at (not
  recorded)`.

- **Codex deprecated `codex_hooks` feature normalized to `hooks`.**
  When emitting Codex config, `codex_hooks` is migrated to canonical `hooks`
  automatically; when reading, the deprecated key is mapped back to
  `features.hooks` to keep startup and schema writes warning-free.

## [0.5.2] — 2026-05-07

Two operator-visible bug fixes for plugin-loading errors observed after
running `chameleon merge` against a real Claude Code + Codex CLI pair.

### Fixed

- **GitHub-URL marketplaces now canonicalize to `kind="github"` on
  disassemble.** Pre-fix, hand-authoring `source: "git"` with a
  `https://github.com/...` URL on Claude (or just a raw github URL in
  a Codex `[marketplaces.<name>]` table) produced neutral `kind="git"`,
  while the same intent expressed on the other target's native form
  produced `kind="github"`. `classify_change` then surfaced the cross-
  target divergence as a `CONFLICT`, and resolution (interactive choice,
  `OnConflict.LATEST`, or persisted prior decision) could pick the
  lower-detail `kind="git"` shape — collapsing Claude's structured
  `{source: "github", repo: "owner/name"}` into `{source: "git", url:
  "https://github.com/owner/name.git"}` on re-derive. Both codec
  disassemble paths now parse the URL via the new
  `chameleon.codecs._url.parse_github_url` helper and promote
  canonical `https://github.com/<owner>/<name>(.git)?` and
  `git@github.com:<owner>/<name>(.git)?` forms to neutral
  `kind="github"`. Custom SSH aliases (`git@github-org:...`),
  `gist.github.com` URLs, and sub-paths are intentionally NOT promoted
  — they carry operator intent the canonical `github` shape cannot
  express. Matches the documented design intent on
  `PluginMarketplaceSource`: "the neutral form normalizes to a small
  `kind` discriminator that round-trips both" (highest detail in
  neutral; target-preferred shape at emit). One-time canonicalization
  effect: any `~/.claude/settings.json` `source: "git"` entries pointing
  at github.com URLs are rewritten to `source: "github"` on next sync,
  and Codex marketplace tables gain `chameleon_kind = "github"` /
  `chameleon_repo = "..."` round-trip hints.

- **Claude `enabledPlugins` no longer accumulates unresolvable plugin
  keys.** Codex tolerates `[plugins."<name>@<marketplace>"]` keys whose
  `<marketplace>` is not declared as a `[marketplaces.<marketplace>]`
  table — they're inert until resolved. Pre-fix, chameleon's cross-
  target unification carried those Codex-only keys through to Claude's
  `enabledPlugins`, where Claude reads them at startup and crashes
  with `error: Plugin foo@bar is not cached at (not recorded)` because
  it consults its marketplace cache and finds nothing. The Claude
  capabilities codec now filters at assemble time: an
  `enabledPlugins[k]` is only emitted when `k`'s `@marketplace`
  component is in `model.plugin_marketplaces` or in the small
  built-ins set (`claude-plugins-official`, `anthropic-agent-skills`).
  Dropped keys are surfaced via a single `LossWarning` listing every
  affected plugin and its missing marketplace so operators can either
  declare the marketplace or accept the drop.

## [0.5.1] — 2026-05-07

A documentation-and-tag release for two operator-visible bug fixes
that landed inside the 0.5.0 commit but were not called out in 0.5.0's
CHANGELOG. Code is identical to the 0.5.0 SHA `d6a0220`; this entry
catalogues the fixes so operators can see what changed.

### Fixed

- **Codex `model_catalog_path` no longer accretes machine-specific
  absolute paths into neutral.** Reading
  `model_catalog_json = "/Users/<operator>/.codex/model-catalog-600k.json"`
  from a Codex `config.toml` previously stored the absolute path
  verbatim in `identity.model_catalog_path`. A neutral.yaml that round-
  tripped on one machine would then fail to apply cleanly on another
  operator's machine (or even the same operator on a different host)
  because the absolute path wouldn't resolve. The Codex identity
  codec's `from_target` now collapses paths under the current
  operator's `$HOME` to a `~`-prefixed form before they land in
  neutral. Encode is unchanged — `os.path.expanduser` runs at the
  Codex live-write boundary, so what's written to disk is still an
  absolute path the Codex CLI can read directly.

- **Target-side deletions now propagate forward into neutral instead
  of being overwritten on every merge.** Previously, if an operator
  removed a setting from `~/.codex/config.toml` (or `~/.claude/settings.json`)
  the engine treated the codec's default-valued return as "silence"
  rather than "the target cleared this," which meant the next merge
  would force the old value back to disk. Codecs can now declare
  `neutral_claimed_paths` — a frozenset of neutral leaves the codec
  asserts ownership over. When a per-target reverse-codec returns the
  schema default for a claimed path, the engine treats that as real
  evidence of intent (deletion) rather than absence of evidence.
  Combined with `--on-conflict=latest` and a target file with a more
  recent mtime than neutral, the deletion is preserved through the
  merge cycle. Both Claude and Codex identity codecs ship with
  `neutral_claimed_paths` populated for their first-class identity
  fields (model, reasoning_effort, thinking, auth.method,
  api_key_helper for Claude; model, reasoning_effort, context_window,
  compact_threshold, model_catalog_path, auth.method for Codex).

### Tests

- New `tests/integration/test_target_deletions.py`: end-to-end
  scenario removing `model_context_window`,
  `model_auto_compact_token_limit`, and `model_catalog_json` from a
  Codex config and asserting the deletion survives the next merge
  rather than being clobbered by neutral.
- Added round-trip coverage in
  `tests/property/test_codex_identity_extras_codec.py` for the
  `~`-collapse path so a regression here surfaces at the codec level
  before the integration smoke notices.

## [0.5.0] — 2026-05-07

A merge-UX release. The merge engine now remembers what the operator
decided, exposes new resolution strategies, ships a real verbose mode,
and the CLI's `init`, `status`, and `--verbose` flags all do what their
names imply for the first time. Plus shipping infrastructure: PyPI
publish via OIDC trusted publishing on tag, and a weekly upstream
schema drift workflow that opens regenerate-PRs when Claude or Codex
upstreams move.

### Added — merge ergonomics

- **Resolution memory.** Every interactive decision persists to
  `neutral.yaml` under a typed `resolutions:` block, keyed by
  `FieldPath`. Each entry includes a `decision_hash` over the
  conflicting `(N₀, N₁, per_target)` tuple. Next merge: same hash →
  silent auto-apply (no re-prompt); different hash → re-prompt with the
  prior decision shown as informational context plus a "values have
  changed since" caveat. Stale resolutions are GC'd at end-of-merge.
- **`[t]` target-specific resolution.** New choice in the interactive
  resolver. Disables cross-target propagation for a path by removing it
  from unified neutral and writing per-target values to
  `targets.<tid>.target_specific[<path>]`. Engine patches each codec's
  per-target neutral with the target-specific value before `to_target`.
- **`OnConflict.LATEST` strategy.** Non-interactive resolution that
  picks the source with the most recent mtime. Useful for cron-driven
  login-time merges where the most recent edit (regardless of side)
  should win without prompting.
- **`chameleon resolutions list|clear` CLI.** Operator escape hatches —
  enumerate stored decisions with hash status (current / stale /
  missing-record), or remove one or all.

### Added — release infrastructure

- **PyPI publish via OIDC.** `release.yml` now runs on `v*.*.*` tag
  push: gates → build → publish via PyPI Trusted Publishing. No
  long-lived `PYPI_API_TOKEN` secret needed; configure once at
  pypi.org under Publishing → Trusted Publishers.
- **Weekly upstream schema drift workflow.** `.github/workflows/schema-sync.yml`
  runs Mondays 07:00 UTC. Resolves the current Claude (schemastore)
  and Codex (codex-rs) tip SHAs, regenerates `_generated.py`, runs the
  four gates including the static no-silent-drops audit, and opens a
  PR if anything diverged. Failing gates surface as a labelled PR for
  operator review rather than failing silently between manual syncs.

### Fixed

- **`chameleon init --dry-run` is now actually side-effect-free.**
  Pre-fix it wrote `~/.config/chameleon/neutral.yaml` regardless of the
  flag (only the merge step respected dry-run). Post-fix the entire
  init handler short-circuits with a description of what it would do.
- **`chameleon status` is now actually informative.** Pre-fix it ran a
  dry-run merge and printed the merge summary — usually just
  `"merge: nothing to do"` when clean, conveying nothing useful.
  Post-fix it reports neutral file presence, per-target drift
  (clean / drift), pending notice counts, and unresolved transactions.
  Exit 0 if everything's clean and nothing pending; exit 1 otherwise.
- **`--verbose` is no longer a dead flag.** Pre-fix it was parsed by
  every subcommand but consumed by zero code paths — operators set it
  expecting more output and got nothing. Post-fix it emits a
  pre-merge preamble (state_root, neutral path, registered targets),
  per-target LossWarning tally with merge_id, and surfaces stale
  notices and transactions.

### Changed

- **`--no-warn` flag added to every subcommand.** Suppresses the
  LossWarning errata that print to stderr after a merge. Useful for
  non-interactive runs that surface warnings via doctor instead.
- **README rewrite.** Opens with a concrete `chameleon status` example
  showing real output, replaces the per-wave status section with
  capability-stated form ("the tool now does X" instead of
  "Wave-N delivered X").
- **Login-recipe docs refreshed.** `docs/login/{launchd,systemd,zlogin}.md`
  use `--on-conflict=fail` consistently and document `--no-warn`.
- **Comment scrub.** Removed temporal session-droppings from comments
  and docstrings across `src/` and `tests/` (~370 line-hits across 84
  files). Replaced wave/section parentheticals with the substantive
  technical content they were tagging. Bug-class shorthand in code
  comments retained where the surrounding text expands the label.

### Tests

- 130 (0.1.0) → 286 (0.2.0) → 415 (0.3.0) → 463 (0.4.0) → **510 passing
  + 35 skipped + 70 fuzz (deselected)** at 0.5.0.
- Three regression tests rewritten to filter `LossWarning` messages on
  substantive content (`"approvals_reviewer"`, `"personality"`,
  `"hook"`) instead of session tags. Same behavioral contract; the
  filter is now durable across cleanups.

### Known limitations

- **Schema migrations not yet implemented.** `schema_version: 1` only.
  The first version bump will need a migration path; design exists,
  implementation does not.
- **No `chameleon-test-kit` published helper for plugin authors.**
  The plugin authoring guide assumes it exists; no actual package
  shipped. Real blocker for anyone writing a third target codec.
- **Windows still untested.** `fcntl.flock` is POSIX-only by design.
  Unchanged from 0.1.0.

## [0.4.0] — 2026-05-06

This release closes the **last open architectural node** from the
original parity-gap DAG: P3 — authorization unification. Wave-13
ships an LCD (lowest-common-denominator) authorization scheme that
gives every operator on either target lossless coverage of the
small structurally-common subset of authorization concepts, while
preserving target-native richness (Claude pattern-language
permissions, Codex named-profile permissions, granular approval
discriminated unions) byte-faithfully via the existing Wave-5 / Wave-7
pass-through infrastructure.

The design call: **chameleon does NOT translate** Claude's
pattern-language permissions to/from Codex's named-profile
permissions. The DSL stays where it lives. The LCD scheme handles
the common case (a `permission_mode` on Claude; a `sandbox_mode` and
an `approval_policy` on Codex) cleanly; the rich case rides
pass-through. See `docs/superpowers/specs/2026-05-06-p3-authorization-design.md`
for the full design exploration.

### Added — LCD authorization scheme

- **`PermissionMode` enum (3 values, Claude-aligned).** `default`,
  `accept_edits`, `plan`. Claimed losslessly by the Claude
  authorization codec on the wire path `permissions.defaultMode`
  (with the schema's camelCase mapping). Bijective round-trip
  proven by the enum-exhaustion catalog.
- **`ApprovalPolicy` enum (4 values, Codex-aligned).** `untrusted`,
  `on_failure`, `on_request`, `never`. Claimed losslessly by the
  Codex authorization codec, with the correct hyphen-vs-underscore
  wire handling (`on-failure` / `on-request` on the wire,
  `on_failure` / `on_request` in neutral). Bijective round-trip
  proven.
- **Bijective round-trip on each enum on its claiming target.** The
  `enum_exhaustion` static catalog automatically picks up the new
  claimers; the formerly-skipped `permission_mode` (Claude),
  `sandbox_mode` (Codex), and `approval_policy` (Codex) cases now
  PASSED.
- **Typed `LossWarning` instances on cross-target encode.** Authoring
  `permission_mode` and merging through the Codex codec emits a
  clear, helpful warning naming the Claude-only nature of the axis
  (and vice versa for `sandbox_mode` / `approval_policy` going through
  Claude).
- **Decode of unmodeled wire values routes to pass-through.** Claude's
  four unmodeled `defaultMode` values (`auto`, `dontAsk`,
  `bypassPermissions`, `delegate`) route to
  `targets.claude.items["permissions"]` with a typed `LossWarning`
  rather than crashing or silently dropping. Codex's `granular`
  discriminated-union approval shape routes the same way to
  `targets.codex.items["approval_policy"]` pass-through with a typed
  `LossWarning`.

### Changed

- **`DefaultMode` renamed to `SandboxMode`.** The previous name was
  always Codex-shaped — its 3 values mirror Codex's wire `SandboxMode`
  enum exactly, including the `danger-full-access` wire mapping for
  the `FULL_ACCESS` neutral value. The rename is honest. Field on
  `Authorization` renamed `default_mode` → `sandbox_mode`. Mechanical
  fix for any neutral-YAML consumer; the enum's semantics and wire
  representation are unchanged.
- **LCD design call: no DSL translation.** Chameleon does NOT
  translate Claude's pattern-language permissions
  (`Bash(...)`, regex patterns, `autoModeClassifierPrompt`, etc.) to
  or from Codex's named-profile permissions
  (`[permissions.<name>]` tables, multi-profile setups). The DSL
  stays target-native; pass-through carries the rich cases byte-
  faithfully via the Wave-5/7 pass-through namespace, parametric
  over target.

### Fixed

- **A-TRUST tests now use `model_construct`.** The W11-3 trust tests
  called `Trust(...)` directly, but Wave-11's D-IDEM model_validator
  dedupes path lists at construction — making the codec's
  `LossWarning` path unreachable through the public constructor.
  Switching the affected tests to `model_construct` bypasses the
  validator and lets them exercise the wire-parsed-with-duplicates
  path the `LossWarning`s actually fire on. Net: 5 previously-failing
  tests now pass; 0.4.0's CI is green where 0.3.0's was red on those
  cases.

### Tests

- 130 (0.1.0) → 286 (0.2.0) → 415 (0.3.0) → **463 passing + 35
  skipped + 70 fuzz (deselected)** at 0.4.0. Verify your local
  numbers match before tagging a release.
- **7 new bijection axes proven** — Wave-13's two new enums plus the
  renamed `SandboxMode`, each on its claiming target.
- **`enum_exhaustion` static catalog auto-picks-up new claimers.**
  The formerly-skipped `permission_mode` (Claude side), `sandbox_mode`
  (Codex side), and `approval_policy` (Codex side) cases now PASSED.
- **`no-silent-drops` audit unchanged: still 0 silent-drops on both
  targets.** Codex `claimed 171 → 178`, `pass-through 528 → 521`
  reflects the prefix-claim covering `approval_policy` plus six
  fields under the `granular` subtree.

### Known limitations

- **Rich authorization translation is intentionally NOT implemented.**
  Claude's pattern-language permissions and Codex's named-profile
  permissions stay target-native. The LCD scheme handles common-case
  operators cleanly; rich-case operators preserve their target-native
  shape via `targets.<target>.items["permissions"]` pass-through and
  do not propagate cross-target.
- **Granular approval shape (`AskForApproval4` Codex variant)** is
  preserved byte-faithfully via pass-through but does NOT round-trip
  cross-target. Operators using Codex's granular discriminated-union
  approval need to author it Codex-side only.
- **Claude's auto-mode classifier customization
  (`autoModeClassifierPrompt` etc.), `dontAsk`, `bypassPermissions`,
  and `delegate` defaultMode values** pass through but do not
  propagate to Codex.
- **Windows still untested.** `fcntl`-based partial-ownership writes
  remain POSIX-only by design. Unchanged from 0.1.0.

## [0.3.0] — 2026-05-06

This release closes the V1 → V1+ acceptance gate. The verification
posture shifts from hand-curated unit tests to **exhaustive proof and
property-based fuzzing**: every wire field on both targets is now
statically accounted for, every finite-domain enum is proved bijective,
and six Hypothesis-driven fuzzers exercise the codecs, the cross-target
unification engine, and the merge state machine. Six real bugs surfaced
by those fuzzers are fixed in this release, with two of them retiring
strict xfails from 0.2.0.

The test suite went from **286 passing + 5 strict xfails (0.2.0)** to
**410 passing + 25 skipped + 70 fuzz tests (deselected by default)** at
0.3.0 cut. Zero strict-xfails remain on the default suite. The fuzz
suite runs under `uv run pytest -m fuzz` and in CI nightly.

### Added — verification infrastructure

- **Static no-silent-drops audit.** `tests/static/test_no_silent_upstream_drops.py`
  enumerates every field on the upstream-canonized
  `_generated.ClaudeCodeSettings` (Claude) and `_generated.ConfigToml`
  (Codex) and asserts each is either claimed by a codec or explicitly
  routed through pass-through — **2119/2119 wire fields accounted for**.
  A new upstream field that nobody claims now fails CI loudly instead of
  silently dropping at runtime.
- **Exhaustive enum / Literal bijection.** `tests/parity/test_enum_literal_bijection.py`
  proves round-trip on **27 finite-domain leaves** by enumerating every
  member of every `enum.Enum` and `typing.Literal` reachable from the
  neutral schema and asserting `from_target(to_target(x)) == x` for
  every value. A new enum value that breaks bijection now fails at
  schema-load time.
- **Hypothesis fuzzer scaffolding** (`tests/fuzz/`). Strategies, marker
  configuration, profile registration, and CI workflow ship with the
  release. The five fuzzer families below all build on this scaffold.

### Added — Hypothesis-guided fuzzers

- **FUZZ-1 + FUZZ-2 — per-codec round-trip and wire-disassemble.**
  Property: for every codec on every target, `from_target(to_target(x)) == x`
  on randomly-generated valid inputs.
- **FUZZ-3 — cross-target unification differential.** Property: for the
  9 schema paths that have a codec on both Claude and Codex, the
  unification engine produces the same neutral value regardless of
  which target's wire shape it started from. Four properties checked
  per shared path: idempotence, commutativity (within target),
  cross-target equivalence, and semantic preservation under merge.
- **FUZZ-4 — pass-through deep-nesting.** Property: arbitrary
  pass-through trees (target-native types, deeply nested, mixed
  scalar / list / dict / TOML datetime) survive round-trip through the
  `targets.<target>.*` namespace at adversarial depth.
- **FUZZ-5 — engine state machine.** A `RuleBasedStateMachine`
  exercises the merge engine across sequences of operations (init,
  edit-target-A, edit-target-B, edit-neutral, merge, adopt, discard,
  doctor) and asserts the merge invariants (idempotency, drift
  detection, conflict classification) hold at every reachable state.
- **FUZZ-6 — Unicode broadside.** Property: every Unicode codepoint
  the schema permits round-trips through every codec, every I/O layer,
  and the partial-ownership writer. Catches BOM / NFC vs NFD
  normalisation regressions.

### Added — §15.x codec slots

- **Claude side: Wave-10 §15.x codec coverage.** Three previously
  pass-through-only enum slots are promoted to first-class codec
  coverage with full round-trip; thirteen additional slots are
  documented as `LossWarning`-emitting where the Claude wire shape
  cannot represent the neutral richness.
- **Codex side: Wave-10 §15.x codec slots.** Twelve previously
  pass-through-only Codex enum slots get first-class codec coverage,
  each with documented `LossWarning`s on the lossy axes.

### Fixed — bugs surfaced and fixed by the fuzzers

The fuzzers found six real bugs; each is fixed in 0.3.0 by a dedicated
parity branch.

- **F-CWD — `McpServerStdio.cwd` was being dropped on both targets.**
  Both the Claude and Codex `capabilities.mcp_servers` codecs now
  carry the `cwd` working-directory through the wire shape. Without
  this fix, an MCP server entry with a non-default cwd would silently
  lose it on the first merge.
- **F-MP-G / F-MP-U / F-AU — Codex marketplace round-trip.** The
  Codex `marketplaces` codec now preserves `kind=github`, `url`, and
  `auto_update` through the round-trip via Chameleon-namespaced
  extras; previously these three fields collapsed onto an incomplete
  marketplace entry. **This retires the F2 xfail from 0.2.0** —
  `[marketplaces.<name>]` sub-tables are now lossless.
- **A-TRUST — Codex Trust list lossy-collapse surfaced as
  `LossWarning`.** The Codex governance codec now emits two
  documented `LossWarning` categories — `Trust.duplicate_paths` and
  `Trust.both_trusted_and_untrusted` — covering the two cases where
  the neutral `Trust` schema can express more than the
  `projects.<path>.trust_level` wire shape can hold.
- **D-IDEM — governance idempotency asymmetry.** Trust path lists are
  now canonicalised at `Trust` schema construction (dedup within each
  list; if a path appears in both lists, `untrusted_paths` wins). This
  matches the Codex codec's last-write-wins behaviour and makes the
  schema constructor itself idempotent — the second `merge` on a
  duplicate-bearing input is now a no-op at the schema level, not just
  the codec level. The fuzz state machine's D-IDEM xfail is retired.

### Changed

- **`AuthMethod` enum shrunk to wire reality (5 → 2 values).** The
  Wave-11 §15.x schema reconciliation removed three `AuthMethod` values
  that no upstream wire shape ever emitted; they had been carried as
  speculative codec slots since 0.1.0. The two remaining values
  (`api_key`, `oauth`) are the ones that round-trip through both
  targets.
- **Per-target codec coverage delta.** Claude gains 3 round-tripping
  + 13 documented-loss codec slots; Codex gains 12 round-tripping
  slots. Combined with F-MP and F-CWD, the §15.x surface is now the
  same shape on both targets — every previously-pass-through enum is
  either round-tripped or has a documented `LossWarning`.
- **Trust schema constructor canonicalises.** See D-IDEM above.
  Operator-visible: a `neutral.yaml` that lists the same path twice
  in `governance.trust.trusted_paths` (or that lists it in both
  trust lists) now silently canonicalises on load, with a
  `LossWarning` on the merge that surfaces the canonicalisation. This
  matches what the Codex codec was already doing at serialise time;
  0.3.0 just makes the schema's invariant explicit.

### Tests

- 130 (0.1.0) → 286 (0.2.0) → **415 passing + 25 skipped + 70 fuzz
  (deselected)** at 0.3.0. The default suite is the gate; the fuzz
  marker is opt-in (`uv run pytest -m fuzz`) and runs in CI nightly.
- **Zero strict-xfails on the default suite.** The three transaction-
  marker xfails and the F2 marketplace xfail from 0.2.0 are all retired
  by Wave-11 fixes. (F1 — Claude `statusLine.type` — was retired during
  the Wave-10 §15.x codec coverage work.)
- **Exhaustive bijection proof on 27 finite-domain leaves.** Every
  enum and `Literal` reachable from neutral round-trips on every member.
- **Cross-target differential fuzz on 9 shared paths × 4 properties.**
  Idempotence, commutativity, cross-target equivalence, and semantic
  preservation under merge.
- **2119/2119 wire fields statically accounted for** by the
  no-silent-drops audit.

### Known limitations

- **P3 — authorization unification.** The richer authorization surface
  (Claude's `Bash(...)` permission patterns ↔ Codex's named
  `[permissions.<name>]` profiles) still ships as `LossWarning`-only
  on cross-target merges. This is the **last open architectural node**
  from the original parity-gap DAG; everything else from 0.1.0 forward
  is either shipped as round-trip codec coverage or has a documented
  `LossWarning`. P3 needs a design pass — the asymmetry is genuine
  (Claude is pattern-based and unbounded; Codex is profile-based and
  named) so unification is not a single codec edit. Tracked for a
  separate spec; will not ship in a 0.3.x patch.
- **Windows still untested.** `fcntl`-based partial-ownership writes
  remain POSIX-only by design. Unchanged from 0.1.0.

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
