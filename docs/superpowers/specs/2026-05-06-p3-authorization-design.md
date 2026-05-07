# P3 — Authorization Unification: Design Exploration

**Date:** 2026-05-06
**Status:** Design exploration. Not yet a spec; informs the brainstorm
session that should precede any implementation agent dispatch.
**Audience:** Project owner. This document lays out the actual surface,
the genuine tension, and a recommendation — but the design call is the
operator's, not the agent's.

The original parity-gap doc (`2026-05-06-parity-gap.md`) explicitly
deferred this node, saying that the authorization codecs need a design
pass before any implementation. After eleven waves closing every other
DAG node, this is the last open architectural question.

---

## 1. The actual surface

### 1.1 Claude's permission model

Claude expresses authorization as a **declarative pattern language** in
`settings.json`'s `permissions` object:

```json
{
  "permissions": {
    "allow":   ["Bash(npm run *)", "Read", "WebFetch(domain:github.com)"],
    "ask":     ["Bash(rm *)", "Edit(/secrets/**)"],
    "deny":    ["Read(./.env)", "WebFetch"],
    "defaultMode": "default" | "acceptEdits" | "plan" | "auto" | ...
  }
}
```

The `PermissionRule` is a single string with a regex:

```
^((Agent|Bash|Edit|ExitPlanMode|Glob|Grep|KillShell|LSP|Monitor|...)
  (\((?=.*[^)*?])[^)]+\))?|mcp__.*)$
```

In words: **either** (a) one of ~25 known tool names with an optional
parenthesised filter, **or** (b) any string starting with `mcp__`.

The filter is a glob-like sub-language. Examples:
- `Bash(npm run *)` — match any `npm run …` invocation.
- `Read(/Users/alice/secrets/**)` — match any read under that subtree.
- `WebFetch(domain:github.com)` — semantically-typed filter (domain).

The `defaultMode` is an orthogonal axis controlling pre-prompt
disposition: `default` (prompt on first use), `acceptEdits` (auto-accept
file edits), `plan` (read-only), `auto` (LLM-classifier with allow/deny),
`dontAsk` (deny unless pre-approved), `bypassPermissions` (skip prompts
entirely).

Keys we already model in neutral:
- `allow_patterns`, `ask_patterns`, `deny_patterns` (pattern strings)
- `default_mode` (enum: `READ_ONLY`, `WORKSPACE_WRITE`, `FULL_ACCESS`)

Mismatches with Claude's actual surface:
- Claude's `defaultMode` enum has 7 values, not 3. Our neutral
  `DefaultMode` was named after Codex's vocabulary.
- Claude has no concept of "named permission profiles." Permissions are
  one flat ruleset.

### 1.2 Codex's permission model

Codex expresses authorization as **named permission profiles** in
`config.toml`:

```toml
approval_policy = "on-request"      # global default
sandbox_mode    = "workspace-write"
default_permissions = "developer"   # default profile to apply

[permissions.developer.filesystem]
"/srv/repo"  = "write"
"/srv/build" = "read"

[permissions.developer.network.domains]
"github.com" = "allow"
"*.npmjs.org" = "allow"

[permissions.locked-down.filesystem]
"/srv/repo" = "read"

[permissions.locked-down.network.domains]
"*" = "deny"
```

Three structural pieces:

1. **`approval_policy`** — global enum: `untrusted`, `on-request`,
   `on-failure` (deprecated), `never`, or a structured `granular`
   object. This is the closest Codex equivalent to Claude's
   `defaultMode`.
2. **`sandbox_mode`** — `read-only`, `workspace-write`, `danger-full-access`,
   etc. Closest to neutral's existing `DefaultMode` enum (which we named
   from Codex's vocabulary).
3. **`[permissions.<name>]`** — named profiles. Each profile is a
   `PermissionProfileToml` with `filesystem` (path → access mode) and
   `network` (domain → allow/deny). `default_permissions` selects which
   profile is active by default; specific commands can request
   `with_additional_permissions = "<profile>"` to escalate.

Keys we already model in neutral:
- `default_mode` (sandbox_mode)
- `filesystem.{allow_read, allow_write, deny_read, deny_write}` —
  pattern lists, not the dict-keyed structured form Codex uses.
- `network.{allowed_domains, denied_domains, allow_local_binding,
  allow_unix_sockets}` — pattern lists, not Codex's
  `domains: dict[str, allow|deny]` form.
- `reviewer` (P1-G — `approvals_reviewer`).

Mismatches with Codex's actual surface:
- We model permissions as flat lists; Codex requires named profiles
  with the active one selected via `default_permissions`. Cannot
  round-trip a multi-profile config.
- Filesystem and network filters are **dict-keyed** in Codex (path/domain
  string → mode), not list-typed.
- `approval_policy` (the global enum) has no neutral representation —
  we conflated it with `default_mode` (sandbox_mode), but they're
  distinct axes in Codex.
- `granular` approval shape (`AskForApproval4`) is unmodelled.

### 1.3 The fundamental shape mismatch

Claude says: **here is one ruleset of patterns, classified by allow / ask
/ deny, plus a global mode**.

Codex says: **here are named profiles, each containing structured
filesystem and network maps; here's which profile is active; here's the
global approval_policy and sandbox_mode**.

These are not the same shape. There is no faithful bidirectional mapping
between a flat pattern list and a dict-keyed named profile registry. Any
unification has to either:
- **Pick one shape** and route the other through a transformation that
  loses information, OR
- **Model both in neutral** as separate paths (target-namespace
  semantics, like `targets.claude.permissions` and
  `targets.codex.permissions`), accepting that the operator authors
  authorization separately for each, OR
- **Define a richer neutral shape** that's a superset of both, with
  documented projection rules to each.

---

## 2. The four design options

### Option A: Pattern-language is canonical; project to/from profiles

Neutral keeps the flat `allow_patterns` / `ask_patterns` / `deny_patterns`
shape (Claude-aligned). On encode-to-Codex:
- Translate patterns into a single synthetic `[permissions.chameleon]`
  profile.
- Best-effort map `Bash(...)` patterns to the closest filesystem/network
  permission entries.
- Patterns that don't match Codex's structured shape (e.g. tool-class
  patterns like bare `Bash`) emit `LossWarning`.

**Strengths:** keeps the simplest shape in neutral. Operators authoring
in the pattern style (the Claude convention) round-trip cleanly through
Claude. Cross-target is "best-effort but documented."

**Weaknesses:** Operators authoring multi-profile Codex configs
(e.g. `developer` + `locked-down`) cannot represent that in neutral at
all. The Codex round-trip is **structurally lossy** — multiple profiles
become a single one with no way to recover. This is exactly the class
of bug Wave-9's cross-target fuzzer would surface immediately.

### Option B: Named-profile is canonical; project to/from patterns

Neutral grows a `permissions: dict[str, PermissionProfile]` with each
profile carrying filesystem and network maps. Plus a `default_profile:
str | None` to select the active one. Plus a global `default_mode` (the
sandbox_mode axis).

On encode-to-Claude:
- Pick the `default_profile`.
- Translate its filesystem/network entries into `Bash(...)` /
  `Read(...)` / `WebFetch(domain:...)` patterns under `allow` / `deny`.
- Other profiles are dropped with `LossWarning` (Claude has no profile
  switcher).

**Strengths:** can represent Codex's full multi-profile surface
losslessly. The structured filesystem/network shape is also more
amenable to validation than free-form patterns.

**Weaknesses:** Operators today author authorization in pattern style
(the Claude convention; documented examples are mostly `Bash(npm run *)`,
not structured maps). Forcing the named-profile shape on every operator
is ergonomically painful for the common case. The Claude→Codex
translation also requires ad-hoc rules (e.g. `Bash` with no filter →
what filesystem entries?).

### Option C: Dual representation; neutral models both

Neutral has BOTH:
- `permissions.patterns: PatternRules | None` (allow/ask/deny strings,
  for operators authoring Claude-style)
- `permissions.profiles: dict[str, PermissionProfile] | None` (named
  profiles, for operators authoring Codex-style)
- `permissions.default_profile: str | None`
- `permissions.default_mode: DefaultMode | None` (sandbox_mode)
- `permissions.approval_policy: ApprovalPolicy | None` (global)

Operators choose which to author. Cross-target encoding rules:
- **Claude codec**: prefer `patterns` if present; fall back to
  projecting `profiles[default_profile]` into patterns with
  `LossWarning` for unmappable structured entries.
- **Codex codec**: prefer `profiles` if present; fall back to
  projecting `patterns` into a single synthetic profile with
  `LossWarning` for unmappable Claude-tool patterns.

**Strengths:** operator authors in their preferred style; both targets
get their preferred shape. The translation only happens when an
operator authors in the "other" style.

**Weaknesses:** the schema grows two parallel surfaces for the same
concept. Risk of inconsistency: what if operator authors both? (Answer:
authoring conflict — `LossWarning` and pick patterns first per a
documented precedence, OR fail validation if both are non-empty.) The
fuzzer's cross-target differential test gets thornier — comparing
"Claude lane projection" against "Codex lane projection" of the same
structured input is ill-defined unless we pick a canonical form.

### Option D: Target-namespace; no unification

Authorization stays target-specific. Operators author
`targets.claude.permissions` and `targets.codex.permissions` as
parallel pass-through bags with documented schemas. Neutral keeps
**only the small set of fields that genuinely cross-translate**
(probably just `default_mode` / `sandbox_mode` if we keep them aligned,
and `reviewer` from P1-G).

**Strengths:** zero translation lossiness. Each target gets its full
expressive power with no mapping ambiguity. Implementable now — no
schema design needed. Honest about the fact that the shapes don't unify
cleanly.

**Weaknesses:** chameleon's whole pitch is "author once, propagate." If
authorization is target-namespaced, operators authoring across both
agents must duplicate effort. The "set Bash permissions once across both
tools" use case is the common one; Option D regresses on it.

---

## 3. Comparison against the project's stated goals

The original parity-gap doc said:

> Every neutral key whose value is a fixed-vocabulary string in the
> wire format becomes a `Literal[...]` or `Enum` field in Pydantic.

Authorization isn't a fixed-vocabulary surface. Pattern language is
extensible (any operator-defined `Bash(...)` filter is valid), and
Codex profiles are a registry indexed by operator-defined names. So
"finite-domain enum proof" doesn't apply here.

The doc also said the V1 acceptance gate is "exemplar round-trips
end-to-end without data loss." That's testable via the fuzzer:
- Option A: would surface multi-profile loss as cross-target xfail.
- Option B: would surface pattern-style loss as cross-target xfail.
- Option C: would pass if precedence rule is documented and the
  operator authors in only one style.
- Option D: would pass trivially because no cross-translation occurs.

**The closest analogue we already shipped is Wave-7's `interface.voice`
under P1-C**: structured object on Claude, missing on Codex, modeled as
a structured neutral concept with `LossWarning` on Codex encode. The
shape mismatch was less severe (single field vs object), but the
principle was: model it richly in neutral, let the Codex side warn-and-drop.

That's Option C, applied selectively.

## 4. The honest edge case: real operator configs

Looking at the exemplar fixture (`tests/fixtures/exemplar/home/_claude/settings.json`):

```json
"permissions": {
  "allow": [
    "Read", "Edit", "Write", "Glob", "Grep", "Bash(*)",
    "WebSearch", "WebFetch", "mcp__*"
  ]
}
```

Nine permissive patterns, all using Claude's pattern language. No `ask`
or `deny` lists, no profiles. The Codex side of the same exemplar has
no `[permissions.*]` profiles either — just `approval_policy` and
`sandbox_mode`. So **the real operator configs don't exercise the
multi-profile surface.** They lean on permissive patterns for both
targets.

This is a strong signal that **Option A** (pattern-canonical) covers
the common case. The multi-profile use is documented in Codex's docs
but not in the operator's actual lived configuration here.

But: **chameleon's job is to round-trip what operators author**, not
just the common case. If a Codex power user authors three named
profiles, the tool must not silently flatten them on the way through
neutral. So Option A must include either:
- (a) An explicit `LossWarning` when the wire side has more than one
  named profile, AND
- (b) A pass-through escape hatch (`targets.codex.items["permissions"]`)
  for operators who need to preserve the multi-profile surface
  unchanged.

That's mostly already in place — Wave-5's `extra="allow"` plus Wave-7's
recursive harvester would route an unclaimed `[permissions.*]` table to
`targets.codex.items`. **The minimum viable Option A is just teach the
codec to handle the simple-pattern case and lean on existing
infrastructure for the complex case.**

---

## 5. Recommendation

**Option A with a documented LossWarning surface and pass-through fallback.**

Concretely:

1. **Keep neutral's existing pattern-list shape** (`allow_patterns`,
   `ask_patterns`, `deny_patterns`, `filesystem` lists,
   `network.{allowed,denied}_domains`).

2. **Add `default_mode` reconciliation**: rename or alias the Codex-
   sided `DefaultMode` enum. Claude's `defaultMode` has 7 values
   (`default`/`acceptEdits`/`plan`/`auto`/`dontAsk`/`bypassPermissions`/
   `delegate`); Codex's `sandbox_mode` has 3 (`read-only`/
   `workspace-write`/`danger-full-access`). They are NOT the same axis.
   The current neutral conflates them. **Split into two fields**:
   - `permissions.sandbox_mode: SandboxMode | None` (Codex-aligned)
   - `permissions.permission_mode: PermissionMode | None`
     (Claude-aligned)
   With cross-target LossWarnings when the unsupported field is set.

3. **Add `approval_policy`** as a new neutral enum (Codex-aligned, with
   the granular variant accepting an `extra="allow"` overflow). Claude
   has no equivalent — emit LossWarning on Claude encode.

4. **Implement Claude codec** (`to_target` / `from_target`) for the
   pattern-list shape. Translation from neutral list → Claude wire is
   essentially identity per pattern.

5. **Implement Codex codec** with this layering:
   - If neutral has only flat patterns and they're simple
     (`Bash(...)`, `Read(...)`, etc.), translate to a synthetic
     `[permissions.chameleon]` profile with the structured form.
   - If neutral has `targets.codex.items["permissions"]` pass-through
     content, splice it in alongside the synthetic profile (operator
     escape hatch for multi-profile setups).
   - Otherwise, emit `LossWarning` for un-translatable patterns.

6. **Strict-xfail any obvious round-trip gaps in cross-target fuzzer** —
   the existing FUZZ-3 infrastructure will catch divergences; pin them
   as known asymmetries until each is addressed individually.

This is the smallest design that:
- Round-trips the common case (the exemplar) cleanly on both sides.
- Has an explicit escape hatch for the multi-profile case via
  pass-through (no schema bloat).
- Keeps the neutral shape recognizable to existing operators.
- Preserves the cross-target fuzzer's discriminatory power.

Estimated implementation scope: 1 schema PR + 1 Claude codec PR + 1
Codex codec PR (+ tests for each). Probably 3-4 parallel agents.
None require deep design beyond what this doc contains.

---

## 6. The brainstorm questions that should precede dispatch

Before any agent touches code:

1. **Is the user satisfied with Option A?** It's the recommendation,
   but Options C (dual representation) and D (target-namespaced) are
   defensible for different reasons. The choice is principled, not
   technical.

2. **Should `permission_mode` model all 7 Claude values, or just the
   core 3 (`default`/`acceptEdits`/`plan`)?** The other 4 are
   `auto` (LLM-classifier; richer), `dontAsk`, `bypassPermissions`
   (dangerous), and `delegate` (experimental, agent-team feature).
   Conservative: model the 3, route the rest through `targets.claude`
   pass-through.

3. **What's the precedence when neutral has BOTH a synthetic Codex
   profile AND `targets.codex.items["permissions"]` pass-through?** I'd
   argue: pass-through wins (operator's explicit override), synthetic
   profile is the fallback, document precedence as a `LossWarning`
   when both are present.

4. **Should `granular` approval (`AskForApproval4` with
   `request_permissions` etc.) be modelled in neutral?** It's a Codex-
   only structured form. Lean toward routing through pass-through
   rather than schema growth.

5. **Are pattern translations (Claude's `Bash(npm run *)` →
   Codex's filesystem map) worth the complexity?** Probably not for
   v0.4. Document the asymmetry and let operators author both styles
   when they need both targets to be aware.

---

## 7. What this doesn't address

- **Hooks** also reference permission rules (Claude `hooks[*].if` is a
  permission filter). That's covered by P1-B's hooks codec already and
  doesn't need re-design here.
- **Plugin-scoped permissions** (`PluginMcpServerConfig.disabled_tools`,
  `enabled_tools`) are per-plugin, not global. Already routed via
  capabilities pass-through.
- **`disable_bypass_permissions_mode`** (Claude-only managed setting):
  pass-through. Not a unification target.

---

*End of design exploration.*
