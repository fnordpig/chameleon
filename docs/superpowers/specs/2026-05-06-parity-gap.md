# Parity-Gap Analysis: Real Exemplar vs. Current Codecs

**Date:** 2026-05-06
**Status:** Diagnostic. Replaces the "V0 thin slice / follow-on
specs" framing in the original design spec with a concrete
node-by-node DAG of remaining work, grounded in the sanitized
real-world exemplar at `tests/fixtures/exemplar/`.

---

## How this document came to exist

The original design spec carved out a "V0 thin slice" and named
follow-on specs for everything else. That framing turned out to be
disguised waterfall — every codec lane the user actually depends on
in their real setup got pushed to "deferred." Running chameleon
against a sanitized snapshot of a live operator setup produced
**concrete, debuggable failure modes** — not abstract domain
disclaimers.

This doc enumerates every key in the exemplar, names the codec or
pass-through path it should travel, and lists the bugs we hit
running a real `init`. The result is the actual remaining work.

## Setup

The exemplar lives at `tests/fixtures/exemplar/home/{_claude,_codex,_claude.json}`.
It mirrors a real ~/.claude/, ~/.codex/, and ~/.claude.json after
PII and secrets are redacted (see `tests/fixtures/exemplar/README.md`
for the sanitization rules; one GITHUB_TOKEN was caught and revoked
during fixture creation, which is itself evidence the toy
acceptance test was hiding real risks).

## P0 — bugs that block real users on first `init`

### P0-1. Claude MCP capabilities codec rejects modern `type` discriminator

```
ValidationError: 6 validation errors for ClaudeCapabilitiesSection
mcpServers.Textual-MCP._ClaudeMcpServerStdio.type
  Extra inputs are not permitted [type=extra_forbidden, input_value='stdio', ...]
```

Real Claude `~/.claude/settings.json` MCP entries include a
`"type": "stdio"` (or `"http"`) discriminator. Our
`_ClaudeMcpServerStdio` and `_ClaudeMcpServerHttp` models use
`extra="forbid"` and don't model the `type` field, so any operator
with a modern Claude MCP setup crashes on first `chameleon init`.

**Fix:** add `type: Literal["stdio"]` / `Literal["http"]` to the two
member models, use `Annotated[..., Field(discriminator="type")]` on
the union. Validate against the fixture.

**File:** `src/chameleon/codecs/claude/capabilities.py`
**Test:** disassemble round-trip of fixture's `mcpServers.Textual-MCP`.

### P0-2. Disassemble validation failures crash the merge engine

`ClaudeAssembler.disassemble` calls `model_validate(section_obj)`
unguarded. A single malformed key in any one domain raises and the
entire merge — including domains that would have worked — aborts.

**Fix:** each per-domain validate in the assembler should
catch `ValidationError`, emit a typed `LossWarning` ("could not
disassemble <domain>: <error>; routing to pass-through"), route the
section's keys to pass-through instead, and continue.

**File:** `src/chameleon/targets/claude/assembler.py`,
`src/chameleon/targets/codex/assembler.py`
**Engine:** the warnings need to surface in `MergeResult` so the CLI
can show them.

### P0-3. Pass-through bag is harvested but engine drops it

`MergeEngine.merge` calls `target_cls.assembler.assemble(per_domain=
..., passthrough={}, ...)` — empty. The disassemble side correctly
fills a pass-through bag; the engine throws it away. Practical
effect: unclaimed keys are preserved on disk only because the
assembler reads `existing` files; if you delete `~/.claude/settings.json`
and try to re-derive from neutral alone, every unclaimed key is lost.

**Fix:** thread `passthrough` from disassemble → neutral.targets.<id>
on the way in; from neutral.targets.<id> → assemble on the way out.
Add an integration test that deletes the live target file and
verifies a re-derive produces a byte-identical (modulo formatting)
result.

**File:** `src/chameleon/merge/engine.py`

## P1 — claimed-keys parity for the exemplar's surface

The fixture has 11 top-level keys per target. Mapping:

### Claude `~/.claude/settings.json`

| Key | Status | Codec / disposition |
|---|---|---|
| `effortLevel` | ✅ claimed | `ClaudeIdentityCodec` |
| `permissions` | ✅ claimed | `ClaudeAuthorizationCodec` |
| `statusLine` | ✅ claimed | `ClaudeInterfaceCodec` |
| `voiceEnabled` | ✅ claimed | `ClaudeInterfaceCodec` |
| `enabledPlugins` | ❌ unclaimed | **P1-A** — capabilities.plugins (shared concept w/ Codex) |
| `extraKnownMarketplaces` | ❌ unclaimed | **P1-A** — capabilities.plugin_marketplaces |
| `hooks` | ❌ unclaimed | **P1-B** — lifecycle.hooks (real codec, not just LossWarning) |
| `voice` | ❌ unclaimed | **P1-C** — interface.voice (object, not just bool) |
| `includeCoAuthoredBy` | ❌ unclaimed | **P1-D** — directives.commit_attribution alias |
| `coauthoredBy` | ❌ unclaimed | **P1-D** — same; legacy alias |
| `gitAttribution` | ❌ unclaimed | **P1-D** — same; another alias |

### Codex `~/.codex/config.toml`

| Key | Status | Codec / disposition |
|---|---|---|
| `model` | ✅ claimed | `CodexIdentityCodec` |
| `model_reasoning_effort` | ✅ claimed | `CodexIdentityCodec` |
| `tui` | ✅ partially | `CodexInterfaceCodec` (only `theme`/`alternate_screen`; misses `status_line` array) |
| `projects` | ✅ claimed | `CodexGovernanceCodec` |
| `marketplaces` | ❌ unclaimed | **P1-A** — capabilities.plugin_marketplaces (mirrors Claude `extraKnownMarketplaces`) |
| `plugins` | ❌ unclaimed | **P1-A** — capabilities.plugins (mirrors Claude `enabledPlugins`) |
| `personality` | ❌ unclaimed | **P1-E** — directives.personality (Codex-only; pass-through eligible) |
| `model_context_window` | ❌ unclaimed | **P1-F** — identity.context_window (Codex-only knob) |
| `model_auto_compact_token_limit` | ❌ unclaimed | **P1-F** — identity.compact_threshold |
| `model_catalog_json` | ❌ unclaimed | **P1-F** — identity.model_catalog_path (Codex-only) |
| `approvals_reviewer` | ❌ unclaimed | **P1-G** — authorization.reviewer (Codex-only, related to §15.1) |

### The big finding: cross-target parity hidden behind shape differences

Two of the seven Claude unclaimeds and two of the seven Codex
unclaimeds are **the same domain**, just rendered differently:

```
Claude  enabledPlugins              ↔  Codex  [plugins.<id>].enabled
Claude  extraKnownMarketplaces      ↔  Codex  [marketplaces.<name>]
```

A single new neutral domain — `capabilities.plugins` — and codec
pair on each target gives the operator one place to enable/disable
across both. **This is the highest-value codec to land** because it
unifies real, in-the-wild operator behaviour (40 plugins, 9
marketplaces in the exemplar) that today is duplicated by hand.

## P2 — engine semantics

### P2-1. Per-FieldPath classification

Today the engine classifies at domain granularity. `identity.model`
is a `dict[TargetId, str]`; on a re-merge each target's reverse codec
produces only its own entry, which differs from the composed neutral's
multi-target dict, so the whole `identity` domain false-conflicts.

**Fix:** classify at field-path granularity, with a special-case for
`dict[TargetId, V]` fields where each target only ever owns its own
key. Spec already mentions this as a follow-on; it's now
P2-1 because every multi-target merge after the first one suffers.

**File:** `src/chameleon/merge/changeset.py`,
`src/chameleon/merge/engine.py`

### P2-2. `chameleon merge --dry-run` early-exits with placeholder

```python
if request.dry_run:
    return MergeResult(exit_code=0, summary="dry run — no files written", merge_id=merge_id)
```

A real dry-run should run through compose + re-derive, then produce
a unified diff against live. Today it's a stub.

### P2-3. `chameleon diff` and `chameleon discard` are stubs

`_cmd_diff` reads HEAD and live and prints a banner; `_cmd_discard`
is a no-op. Both need real implementations using `merge.drift`.

## P3 — Authorization parity (Claude patterns ↔ Codex profiles)

The original §15.1 deferral. Now P3 because P1 surfaces it: Claude's
`permissions.allow: ["Read", "Bash(*)", "mcp__*"]` doesn't map to
Codex's named `[permissions.<name>]` profiles in the V0 codec, but
the operator clearly uses both. Needs design before implementation.

**Wave-13 closure (LCD interpretation):** the design exploration in
`docs/superpowers/specs/2026-05-06-p3-authorization-design.md` settled
on a lowest-common-denominator unification. No translation between the
two pattern languages; lossless on the small structurally-common
subset (global mode/policy and approval policy); pass-through for
everything richer via `targets.{claude,codex}.items["permissions"]`;
`LossWarning` on the cross-target asymmetry. Wave-13 S1 ships the
schema half (`SandboxMode` rename + new `PermissionMode` and
`ApprovalPolicy` enums in `src/chameleon/schema/authorization.py`).
S2 (Claude codec) and S3 (Codex codec) follow in the next wave and
consume the new vocabulary; the cross-target fuzzer auto-picks up the
resulting xfails, which the codec waves document explicitly.

## The DAG

```
P0-1 fix Claude MCP discriminator ──┐
                                    ├──> P1-* codec lanes (parallel)
P0-2 robust disassemble ────────────┤
                                    │
P0-3 pass-through propagation ──────┘
                                    │
P2-1 per-FieldPath classification ──┴──> P3 authorization design
                                    │
P2-2/3 dry-run + diff + discard ────┘
```

P0 nodes are independent of each other and of P1; P1 nodes are
independent of each other (each codec lane is its own file); P2-1
is needed before any meaningful re-merge testing. P3 has a design
prerequisite that should run in parallel with P0/P1 implementation.

## Acceptance criteria for "parity"

`chameleon init` against `tests/fixtures/exemplar/` succeeds. Then:

1. `chameleon merge` is a no-op when neutral is freshly composed
   (round-trip equality).
2. Deleting `~/.claude/settings.json` and re-running `chameleon
   merge` reproduces the original byte-identical (modulo
   key-order/formatting noise that the IO codecs document as
   non-significant).
3. Editing `identity.model.claude` in neutral and running
   `chameleon merge` rewrites only that field in `~/.claude/settings.json`,
   leaves everything else (40 plugins, hooks, statusLine, ...) intact.
4. Adding a plugin to `~/.codex/config.toml` directly and running
   `chameleon adopt codex` pulls it into `capabilities.plugins` in
   neutral and propagates to `~/.claude/settings.json`'s
   `enabledPlugins`.

That's parity. Everything below is the work to get there.

## Wave-11 §15.x schema reconciliation (2026-05-06)

After Wave-10 Agents F (Claude) and G (Codex) implemented codecs for
the 5 §15.x enum slots Wave-8 β identified as typed-but-unclaimed,
both surfaced the same diagnosis: the §15.x neutral schemas were
written without grounding against the targets' actual wire vocabulary.
The result was a set of LossWarnings firing on every operator who
touched those slots, with no observable behaviour change — the
classic "speculation in the schema" smell.

This section pins the per-slot Wave-11 reconciliation decisions.

### Slot-by-slot decisions

#### `identity.auth.method` — schema shrunk

Original neutral: 6 values (OAUTH/API_KEY/BEDROCK/VERTEX/AZURE/NONE).
Upstream wire reality:

* Claude `ForceLoginMethod` (`_generated.py`): `claudeai`, `console`.
* Codex `ForcedLoginMethod` (`_generated.py`): `chatgpt`, `api`.

Neither target exposes `bedrock` / `vertex` / `azure` as an auth-method
value. Claude reaches the AWS Bedrock / GCP Vertex provider lanes
through per-provider env vars (`ANTHROPIC_BEDROCK_BASE_URL`,
`CLAUDE_CODE_SKIP_BEDROCK_AUTH`, `ANTHROPIC_VERTEX_BASE_URL`,
`ANTHROPIC_VERTEX_PROJECT_ID`, ...) which are owned by the
`environment` codec. Codex talks exclusively to OpenAI / OSS providers
and has no cloud-provider lane at all.

**Decision:** shrink `AuthMethod` to `{OAUTH, API_KEY}`. Both values
round-trip cleanly on both targets; the LossWarning paths on
`to_target` for the removed values are gone with them. Defensive
`wire is None` branches in the codecs remain so that a future schema
growth without a corresponding wire mapping still warns rather than
silently dropping.

#### `directives.verbosity` — schema preserved, asymmetry pinned

Original neutral: `Verbosity` = `{LOW, MEDIUM, HIGH}`. Codex's
upstream `Verbosity` enum (model_verbosity, GPT-5 Responses API) is
the same 3-element domain — round-trips lossless on Codex. Claude
has no persistent verbosity setting; the field emits a typed
`LossWarning` on every value when the Claude codec runs.

**Decision:** leave the schema as-is. The asymmetric LossWarning
behaviour is intentional and documented; the operator who sets
`verbosity` knows it lands on Codex but not Claude.

#### `capabilities.web_search` — schema preserved, asymmetry pinned

Original neutral: `Literal["cached", "live", "disabled"]`. Codex's
`WebSearchMode` enum is the same 3-element domain — round-trips
lossless on Codex. Claude gates web search via `permissions` tool
patterns rather than a tri-state axis, so this field emits a
`LossWarning` on the Claude codec.

**Decision:** leave the schema as-is. The asymmetry is structural
(different gating model on Claude); forcing Claude to fake one of
these three values would be lossy in a worse way.

#### `environment.inherit` — schema preserved, asymmetry pinned

Original neutral: `InheritPolicy` = `{ALL, CORE, NONE}`. Codex's
`shell_environment_policy.inherit` 3-arm RootModel union is the
same 3-element domain — round-trips lossless on Codex. Claude
inherits the parent shell environment unconditionally with no
analogue.

**Decision:** leave the schema as-is. The Claude codec emits a
typed `LossWarning` for every value; that's the honest signal.

#### `lifecycle.history.persistence` — schema preserved, asymmetry pinned

Original neutral: `HistoryPersistence` = `{SAVE_ALL, NONE}`. Codex's
`HistoryPersistence` 2-arm RootModel union is the same 2-element
domain — round-trips lossless on Codex. Claude's closest analogue is
the `CLAUDE_CODE_SKIP_PROMPT_HISTORY` env var, which is owned by the
`environment` codec; the Claude lifecycle codec emits a `LossWarning`
on every value.

**Decision:** leave the schema as-is. The asymmetry mirrors the
target reality.

### Reconciliation principle

Schema values that no codec can claim on either side are pure
speculation and should be removed. Schema values that one codec can
claim and the other cannot are an honest asymmetry and stay (with a
documented per-target `LossWarning`). The
`tests/property/test_enum_exhaustion.py` harness is the static
backstop: it makes "no codec claims this value" impossible to ignore
because every (field, value, codec) cell shows up explicitly in the
session-summary block.

Wave-11 result: enum-exhaustion catalog shrank from 12 leaves / 72
parametrised cases to 12 leaves / 66 cases (the 6 removed cases are
3 deleted AuthMethod values × 2 codecs). The pass count stays at 41
because the deleted cases were all LossWarning skips on both targets;
the gap is now exclusively the structural asymmetries documented
above, not phantom speculation.
