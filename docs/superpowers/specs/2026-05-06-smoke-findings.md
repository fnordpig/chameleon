# Exemplar Smoke Findings (post-Wave-4)

**Date:** 2026-05-06
**Status:** Closed by Wave-5. Originally diagnostic, surfacing four
real bugs in the V0+post-Wave-4 build by running `chameleon init` +
`chameleon merge` end-to-end against the sanitized real-world
exemplar fixture at `tests/fixtures/exemplar/`. All four are now
fixed and the smoke (`tests/integration/test_exemplar_smoke.py`)
runs all assertions as live tests; this doc is kept as a
post-mortem. See "Resolution" section at the end.

After four waves of parity work that closed every node enumerated in
`2026-05-06-parity-gap.md`, the smoke run against the exemplar reveals
three real bugs that the per-codec property tests didn't catch. They
all live at the engine ↔ assembler boundary, not in any individual
codec.

## What works (verified by the smoke)

- `chameleon init` against the exemplar exits clean.
- `chameleon merge --on-conflict=keep` after init exits clean and
  preserves all 71 non-`mcpServers` keys in `~/.claude.json` (the
  partial-ownership discipline holds end-to-end).
- The **legacy attribution alias consolidation** is observable: P1-D
  rewrites `coauthoredBy` / `gitAttribution` / `includeCoAuthoredBy`
  into a single `attribution.commit` entry. This is the documented
  design — operators upgrading from the old config shape will see
  this as a normalization, not a regression.
- `chameleon diff <target>` correctly detects manual drift and
  produces a coloured unified diff.
- `chameleon discard <target> --yes` restores live to state-repo
  HEAD and `chameleon diff` afterward exits 0.
- `chameleon merge --dry-run` writes nothing (verified bytes pre/post)
  even when the operator has authored a change in neutral.

## Real bugs surfaced

### B1: Codex partially-claimed sub-tables lose their unclaimed sub-keys

The exemplar's `[tui]` table has:

```toml
[tui]
status_line = ["model-with-reasoning", "current-dir", ...]

[tui.model_availability_nux]
"gpt-5.5" = 4
```

The Codex interface codec models `tui.theme`, `tui.alternate_screen`,
and `file_opener` only. After `chameleon init` + first merge, the
entire `[tui]` table is **gone** from the live `~/.codex/config.toml`
— including `status_line` and `[tui.model_availability_nux]` which
chameleon shouldn't have touched.

**Root cause:** the pass-through bag operates at top-level granularity.
When a codec partially claims a top-level table, the assembler emits
the codec's section (which doesn't include the unclaimed sub-keys) and
nothing else. The assembler's `existing` overlay merges at top-level
keys only, not into nested tables.

**Fix scope:** non-trivial. Either
- Codecs declare `unclaimed_subkeys: frozenset[FieldPath]` and the
  assembler merges those from `existing` per-table; or
- The pass-through bag becomes hierarchical (`PassThroughBag` carries
  full nested subtree state for partially-claimed tables); or
- Each codec's `target_section` model gets `extra="allow"` and the
  disassembler harvests the extras into the section so `to_target`
  re-emits them.

The third option (extra="allow") is the smallest blast radius: every
codec that wraps a table-shaped section just needs to flip its
`ConfigDict(extra="forbid")` to `extra="allow"` and the disassembler
to capture+re-emit the extras. Recommend that path for the next wave.

### B2: Marketplace dict key ordering not stable across keep-merges

A second `chameleon merge --on-conflict=keep` run against an
already-merged tree reorders the keys inside `extraKnownMarketplaces`
(Claude) and `[marketplaces.*]` (Codex). All keys are preserved — this
is *not* data loss — but byte-equality across passes breaks.

**Root cause:** P1-A's capabilities reconciler (`reconcile_plugins`)
takes the union of per-target dicts via Python's iteration order. When
the iteration order differs across runs (e.g., the disassembler reads
TOML before JSON on one run, JSON before TOML on the next), the union
order shifts.

**Fix scope:** trivial. Sort by key in the reconciler. ~5 lines.

### B4: Non-ASCII escaped in partial-owned writes

The exemplar's `~/.claude.json` has an em-dash (`—`, U+2014) in the
`companion.personality` string. After `chameleon merge`, the partial-
owned write path re-serialized the merged dict with `\uXXXX` escapes,
breaking `chameleon discard`'s round-trip.

**Root cause (corrected):** `io/json.py:dump_json` had already been
fixed to pass `ensure_ascii=False` on 2026-05-06 — the original
findings doc was wrong about the location. The actual broken path
was a duplicate inline `json.dumps(...)` call in
`src/chameleon/state/locks.py:67` (inside `partial_owned_write`)
that bypassed the canonical `dump_json` entirely. Wave-5 Agent R
caught this and routed the partial-owned write through `dump_json`,
which is the principled fix. Lesson: a single canonical serialiser
is worth more than one-line workarounds in N call sites.

**Fix shipped:** `src/chameleon/state/locks.py` now calls
`dump_json(merged, indent=2)` instead of inline `json.dumps`.

### B3: Per-FieldPath leaf-write doesn't coerce through schema annotations

The resolver (`Strategy(PREFER_NEUTRAL)` for example) returns the
chosen value as a raw object — for `identity.reasoning_effort` that's
a `str` like `"xhigh"`. The engine's `_write_leaf` then `setattr`s the
raw `str` onto `composed.identity.reasoning_effort`, bypassing
Pydantic's enum coercion. When `to_target` later reads that field and
calls `.value` (expecting a `ReasoningEffort` enum), it crashes:

```
AttributeError: 'str' object has no attribute 'value'
  in src/chameleon/codecs/claude/identity.py:61
  section.effortLevel = model.reasoning_effort.value
```

**Root cause:** the engine's `_write_leaf` uses raw `setattr`. Pydantic
models with `model_config` permitting field assignment apply per-field
validation only when assignment goes through the validator path. Raw
`setattr` bypasses it.

**Fix scope:** small. `_write_leaf` should resolve the field's
annotation and run the value through `TypeAdapter(annotation).validate_python(value)`
before setattr. ~10 lines plus a regression test.

## Documented behaviors that aren't bugs but worth noting

- **`--on-conflict=fail` correctly raises on real cross-target divergences.**
  The exemplar has Claude `effortLevel="high"` vs Codex
  `model_reasoning_effort="xhigh"`. These are genuinely different
  values; `fail` does what it says. P2-1 fixed false-conflicts on
  `dict[TargetId, V]` fields (which it did) but cannot eliminate real
  scalar disagreements — those need operator resolution.

- **`--on-conflict=keep` does not apply operator-authored neutral edits.**
  This is KEEP's documented semantic. To apply an operator's change,
  use `=prefer-neutral` (assuming B3 is fixed first) or remove the
  conflicting target value.

- **`env: {}` written when neutral has no environment.** Cosmetic
  noise. The Claude environment codec emits an empty dict when
  `Environment.variables` is empty. Could be filtered out at the
  assembler boundary.

## Smoke retrospective: what V0+ ships

For an operator who:

1. Sets `neutral.yaml` once with their preferred identity, directives,
   and capabilities;
2. Runs `chameleon merge --on-conflict=prefer-neutral` (after B3 is
   fixed) or `--on-conflict=keep` to preserve the existing live state
   while letting unclaimed pass-through round-trip;
3. Doesn't have `[tui].status_line` or `[tui.model_availability_nux]`
   in their Codex config (or accepts losing them);

V0+post-Wave-4 chameleon **works**. For the full operator surface
captured in the exemplar, three issues (B1/B2/B3) need to land before
the tool is genuinely shippable to operators with rich live configs.

## Recommended next-wave dispatch

Wave-5 — four truly disjoint Opus agents:

- **Agent W5-1**: B1 — sub-table preservation. `extra="allow"` on each
  codec's section model + a small disassembler change. Touches every
  codec's `target_section`. Largest scope.
- **Agent W5-2**: B2 — sorted-by-key reconciliation. Trivial fix in
  `src/chameleon/schema/capabilities.py`'s `reconcile_plugins`.
- **Agent W5-3**: B3 — schema-aware leaf-write. Engine-only:
  `_write_leaf` runs values through `TypeAdapter(annotation).validate_python`
  before `setattr`.
- **Agent W5-4**: B4 — `ensure_ascii=False` in `io/json.py:dump_json`.
  Plus a regression test using non-ASCII fixture content.

After W5 lands, every xfail in `tests/integration/test_exemplar_smoke.py`
flips to a passing assertion and the exemplar smoke becomes the V1
acceptance gate.

## Resolution

Wave-5 dispatched four parallel Opus agents (one per bug) and all
four fixes landed via merge commits on main between SHAs `f33b8cb`
and `f9d07a4`. After merge:

- `tests/integration/test_exemplar_smoke.py`: all 9 tests pass, 0
  xfails. The smoke is now a live regression test, not a
  documentation of known bugs.
- 268 tests pass on main (was 246 with 4 xfails pre-Wave-5).
- All four gates green.

**Per-bug shipping status:**

| ID | Branch | SHA | Behavior fix |
|---|---|---|---|
| B1 | `parity/b1-subtable-preservation` | `0008179` | All codec section models flipped to `extra="allow"` (with documented exception for `ClaudeDirectivesSection` whose `forbid` is the routing-to-passthrough discipline for legacy attribution aliases). Two helpers added to `targets/_protocol.py` (`harvest_section_extras`, `merge_extras_into_dict`); both assemblers re-disassemble the existing target file at assemble time and merge sub-table extras into the produced doc with "modelled wins" overlay. |
| B2 | `parity/b2-stable-reconciliation` | `557c696` | `reconcile_plugins` and the capabilities codecs sort dict iterations by key. Both reconciler and codec sort: belt-and-suspenders, since the engine flow goes through `walk_changes` not `reconcile_plugins` directly. |
| B3 | `parity/b3-leaf-write-coercion` | `6bec01c` | `_coerce_through_annotation(annotation, value)` helper in `merge/engine.py` wraps `TypeAdapter(annotation).validate_python(value)`. `_write_leaf` branches between scalar leaves (full annotation) and `dict[K, V]` leaves (extracts V via `typing.get_args`). The `value=None` drop-the-key semantics are preserved. |
| B4 | `parity/b4-ensure-ascii-false` | `1eb7db6` | `state/locks.py:partial_owned_write` now routes through canonical `dump_json` instead of inline `json.dumps`. (See corrected root-cause note in B4 section above — `dump_json` itself was already correct.) |

The exemplar smoke (`test_exemplar_smoke.py`) plus three new regression
suites (`test_subtable_preservation.py`, `test_capabilities_reconciler_ordering.py`,
`test_engine_leaf_write_coercion.py`, `test_io_json_unicode.py`) now
pin all four bugs as closed-and-non-regressing.
