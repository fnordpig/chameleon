# Chameleon — End-to-End Design

**Date:** 2026-05-05
**Status:** Approved (pending user spec review)
**Audience:** Future Chameleon contributors, package consumers, plugin authors.

---

## 1. Goal

Chameleon is an MIT-licensed Python tool, used on the CLI or at login time,
that maintains a **single neutral configuration** (YAML) describing how an
operator wants their AI coding agents — initially Claude Code and OpenAI Codex
CLI — to behave, and **bidirectionally synchronizes** that neutral form with
each agent's native, target-specific configuration files. When an agent edits
its own configuration at runtime, Chameleon detects the drift, prompts the
operator to resolve any conflict, absorbs the change back into the neutral
form, and re-derives every other agent's configuration so the operator's
intent stays consistent across tools.

The architecture is explicitly forward-compatible with future "context
teleportation" between agents — moving session state, memory, and other
non-configuration artifacts using the same neutral-form + per-target-codec
machinery — without requiring layout changes.

## 2. Non-Goals

- **No continuous-sync daemon.** Chameleon runs as a one-shot CLI command;
  the operator (or a login-time hook) invokes it. No file-watching service,
  no background processes, no IPC.
- **No three-way text merging or partial-line conflict editing.** Conflicts
  are resolved at the granularity of "neutral schema key" with simple
  A-or-B selection.
- **No telemetry / phone-home / analytics.** Chameleon does not contact any
  network service of its own. It manipulates files only.
- **No agent-installer responsibilities.** Chameleon assumes Claude Code and
  Codex CLI are already installed; it manages their configuration files,
  not their binaries.
- **No magic dotfile takeover.** Chameleon never edits a target file outside
  its declared paths, and never edits any file before the operator has run
  `chameleon init` to opt in per machine.
- **V0 will not implement every domain.** See §13 — the schema is designed
  end-to-end now; V0 ships codecs for a deliberate thin slice and stubs the
  rest with `NotImplementedError`-raising codecs guarded by `xfail` tests.

## 3. Glossary

- **Neutral form** — the operator's canonical YAML file. The source of
  truth in steady state.
- **Target** — a configurable AI agent. V0 targets: `claude`, `codex`.
  Future targets register via Python entry points.
- **Codec** — a pure function that translates one schema *domain* between
  neutral form and a *target*'s shape. Codecs come in pairs: `to_<target>`
  (forward) and `from_<target>` (reverse).
- **Assembler / disassembler** — per-target component that knows the
  on-disk layout (which files contain which sections) but no semantics.
- **Domain** — one of the eight orthogonal slices of the schema ontology
  (§7). Domains are rational and covering: each is internally coherent,
  and together they partition the entire configurable surface.
- **Pass-through** — a target-unique feature with no neutral equivalent.
  Stored under `targets.<target>.<key>` in neutral; the assembler splices
  it into the target's output verbatim and the disassembler harvests
  unmapped keys back into the same namespace on reverse.
- **Drift** — divergence between a target's live config files and the
  last commit in that target's state-repo HEAD.
- **Merge** — the single round-trip operation. Reads all targets, detects
  drift, resolves conflicts, derives a new neutral, re-derives all
  targets, commits one structured commit per target's state-repo.

## 4. Execution Model

### 4.1 Per-target git state repos

Every target Chameleon manages has its own git repo at
`$XDG_STATE_HOME/chameleon/targets/<target>/` (default
`~/.local/state/chameleon/targets/<target>/`). The repo's working tree
mirrors the file content the agent actually reads, namespaced by *artifact
class*:

```
~/.local/state/chameleon/targets/claude/
├── .git/
├── .meta.toml                 # provenance: source neutral file path + sha + last-applied UTC
├── settings/
│   ├── settings.json          # mirrors ~/.claude/settings.json (or project equivalent)
│   ├── .mcp.json              # if managed
│   └── ~.claude.json          # if managed (escaped path; see §10.4)
└── sessions/                  # reserved for future "teleport" work; empty in V0
```

The `settings/` subdirectory is V0's only artifact class. Future classes
(`sessions/`, `memory/`, etc.) reuse the same per-target git layout, so the
teleportation roadmap requires no structural changes.

### 4.2 No symlinks to live files

Agents commonly write configuration atomically (write-temp + rename),
which silently replaces a symlink with a regular file and breaks any
chain of indirection. Chameleon therefore never symlinks the live file to
its state repo. Instead, on every operation it copies live → state-repo
to sample current state, and copies state-repo → live to apply.

### 4.3 The single `merge` operation

Every state-changing operation is a `merge`. A merge consolidates up
to **four sources of change per neutral key**:

- **N₀** — the neutral key value as of the *last* successful merge.
  Stored as a hash + key-by-key snapshot under
  `~/.local/state/chameleon/neutral.lkg.yaml` ("last known good")
  alongside the per-target state-repos.
- **N₁** — the neutral key value in the current neutral file (which
  the operator may have edited since the last merge).
- **Tᵢ** — for each target *i*, the value derived by reverse-codec
  from that target's *live* config files. Possibly drifted from the
  state-repo's HEAD (which represents what Chameleon last wrote).

A key has *changed* if `N₁ ≠ N₀`, or if any `Tᵢ ≠ N₀` for some target
*i*. A key is *consensual* if every changed source agrees on the new
value. A key is *conflicted* if two or more changed sources disagree
(see §5.3).

Pipeline:

1. **Sample.** For each target, read live files. Each target's
   assembler parses bytes into a typed `FullTargetModel`.
2. **Disassemble + reverse-codec.** For each target, route fields to
   per-domain codecs (§8.1) and gather pass-through. Produces a
   per-target *typed neutral candidate*.
3. **Load anchors.** Parse the current neutral file (`N₁`) and the
   last-known-good snapshot (`N₀`).
4. **Detect change.** For each neutral key, compute the change-source
   set ⊆ {`N₁`, `T₁`, `T₂`, …} consisting of sources where the value
   ≠ `N₀`. Empty set → key unchanged; nothing to do for that key.
5. **Classify.** Singleton change-source set with new value V →
   *consensual*; resolve to V automatically. Multi-source set with
   all values equal → *consensual*; resolve to that shared value.
   Multi-source set with disagreement → *conflict*.
6. **Resolve conflicts** via §5 (interactive or `--on-conflict`).
7. **Compose new neutral.** Apply consensual + resolved-conflict
   values onto `N₀` to produce `N₂` (the merge result). Pass-through
   namespaces are merged per-target with the same change-detection
   rules (a passed-through key in `targets.claude.*` follows the same
   N₀ / N₁ / T-claude algorithm).
8. **Re-derive.** For each target, run forward codecs + assembler to
   compute new live-file bytes from `N₂`.
9. **Write live.** For each target where the new bytes differ from
   live: atomic write (write-temp + rename). Partial-ownership
   files use the concurrency discipline in §10.5.
10. **Commit state-repos.** For each target, mirror new live bytes
    into the target's state-repo working tree, then `git commit`
    with the structured trailer (§4.4) including a stable `merge-id`
    UUID shared across all per-target commits in this merge.
11. **Update last-known-good.** Atomically replace
    `neutral.lkg.yaml` with `N₂`'s serialization, hash recorded in
    the merge transaction marker (§4.6).
12. **Atomic write** of `N₂` to the operator's neutral file (this
    is what they'll see in `git diff` if they version-control it).

If step 4 yields no changes for any key, the run is a no-op and
exits 0 with `merge: nothing to do`. Steps 9–12 are *transactional*
in the sense that step 11's marker (§4.6) lets the next merge detect
and recover from a partial completion.

### 4.4 Commit message structure

```
merge: <human-readable summary>

Sources:
  neutral: <sha256 of post-merge neutral.yaml>
  drift:   <list of (target, domain, key) tuples that drifted>
  conflicts: <list of resolved conflicts and their winners>

Chameleon-Schema-Version: 1
```

The trailer is machine-parseable. `chameleon log <target>` formats it
as a human-readable timeline; `chameleon log --json` emits the parsed
trailers for tooling.

### 4.5 Auxiliary operations

- **First-time `init`.** §9.2 specifies the four-cell decision matrix
  (depending on which of `neutral.yaml` and the state-repos already
  exist).
- **Discard.** `chameleon discard <target>` overwrites the live file
  with `HEAD` of the state-repo, throwing away any drift. Useful when
  the operator knows the agent's edit was a mistake. Does *not*
  modify neutral.
- **Adopt.** `chameleon adopt <target>` is shorthand for "merge but
  resolve every conflict in favor of `<target>`." Equivalent to
  `chameleon merge --on-conflict=prefer=<target>`.

### 4.6 Transaction marker and recovery

Each merge generates a fresh `merge-id` UUID. Before step 9
(write live), Chameleon writes a transaction marker:

```
~/.local/state/chameleon/.tx/<merge-id>.toml
```

containing `{ started_at, target_ids, neutral_lkg_hash_after }`.
After step 11 (last-known-good updated), the marker is removed.

If a merge is interrupted between steps 9 and 11, the marker
remains. On next `chameleon merge` (or `chameleon doctor`),
Chameleon:

1. Reads the marker; identifies which targets have a state-repo
   commit carrying that `merge-id` and which don't.
2. For targets *with* the merge commit: live file is consistent
   with state-repo HEAD; nothing to recover.
3. For targets *without*: live file may have been written but
   state-repo wasn't committed. Chameleon snapshots the current
   live bytes into the state-repo with a recovery commit
   `recover: from interrupted merge <merge-id>`. The next merge
   then proceeds normally.
4. The neutral last-known-good is restored from
   `neutral.lkg.yaml`'s pre-marker hash if step 11 didn't
   complete; otherwise it's already up to date.

`chameleon doctor` flags any leftover markers older than 24h as
suspicious. Markers are explicitly typed (a Pydantic model in
`chameleon/state/transaction.py`); no JSON-shaped strings.

## 5. Conflict Resolution Protocol

A conflict is a single (domain, key, [target → value] map) record. The
resolver receives a list of such records and must return a (key →
chosen-value) map.

### 5.1 Interactive mode (TTY)

A conflict shows up to four sources (`N₀` is shown only as
context; it is not a choice):

```
authorization.network.allowed_domains
  was (last merge):   ["github.com", "*.npmjs.org"]
  neutral (now):      ["github.com", "*.npmjs.org", "files.pythonhosted.org"]
  claude (live):      ["github.com", "*.npmjs.org", "registry.pypi.org"]
  codex (live):       ["github.com", "*.npmjs.org"]            # unchanged from "was"

  [n] take neutral=[github.com, *.npmjs.org, files.pythonhosted.org]
  [a] take claude=[github.com, *.npmjs.org, registry.pypi.org]
  [b] take codex=[github.com, *.npmjs.org]                     # = revert
  [s] skip (leave the change unresolved; re-prompt next merge)
```

Sources where the value equals `N₀` (here: `codex`) are shown for
context but their letter is grayed out — taking an unchanged value
is equivalent to reverting the conflict. Sources whose letter is
active (`n`, `a`) represent intentional changes the operator must
choose between.

`s` (skip) leaves all live files alone for that key — Chameleon
will re-prompt next merge. Useful when the operator wants to think
about it. Skip does *not* update last-known-good for that key, so
the conflict persists until resolved.

### 5.2 Non-interactive mode (login-time, CI, no TTY)

When stdin is not a TTY, Chameleon obeys `--on-conflict=<strategy>`:

| Strategy | Behavior on conflict |
|---|---|
| `fail` (default) | Exit 2 with conflict report on stderr; nothing written |
| `keep` | Leave live files alone for conflicting keys (skip) |
| `prefer=<target>` | Take the named target's value (per-key) |
| `prefer=neutral` | Take the *current* neutral file's value `N₁` (operator's edit wins over target drift) |
| `prefer=lkg` | Revert to last-known-good `N₀` (drop all changes; useful as a panic button) |

`fail` is default precisely because login-time silent resolution is a
trust hazard. Operators who want unattended runs opt into a strategy
explicitly.

### 5.3 What is and isn't a conflict

A key is *changed* if any source ≠ `N₀`. A key is *conflicting* if
the changed-source set has two or more sources with disagreeing
values. The cases:

| `N₁` vs `N₀` | `Tᵢ` vs `N₀` (any) | Outcome |
|---|---|---|
| same | none | unchanged — no-op |
| changed | none | apply `N₁`, push to all targets |
| same | one target changed | absorb `Tᵢ` into neutral, push to other targets |
| same | multiple targets changed, all equal | absorb shared value, no conflict |
| same | multiple targets changed, disagreeing | **conflict (cross-target)** |
| changed | one or more targets changed, all equal to `N₁` | absorb (shared agreement), no conflict |
| changed | one or more targets changed, disagreeing with `N₁` | **conflict (neutral vs target)** |

Not conflicts (handled silently):

- A target's drift on a key the other target doesn't speak: not a
  conflict — pass-through namespace handles it.
- Drift in a target that has no codec for that domain in V0: the
  pass-through namespace catches it; emits a `LossWarning` if the
  drifted value would be lost on round-trip, otherwise transparent.

## 5.4 Strict typing rule (applies to every section that follows)

**Everything is typed. No strings.** Every API surface in Chameleon —
codec inputs and outputs, conflict records, drift records, target and
domain identifiers, on-conflict strategies, file ownership flags — is
expressed via Pydantic models, `enum.Enum` subclasses, or `Literal[...]`
types. No `dict[str, Any]` floats free in the codebase except at the
single boundary where it must: the **pass-through bag** (§7.2) which
is itself parametric over the target — `PassThroughBag[ClaudeSettings]`
versus `PassThroughBag[CodexConfig]` — and stores values shaped by
the target's generated model rather than as raw JsonValue, so
target-native types (TOML datetimes, structured enums) survive
round-trip. Configuration that arrives from the wire
(YAML, JSON, TOML) is parsed straight into Pydantic models; we never
move untyped dicts through application code.

Concretely:

- `TargetId` is a Pydantic-validated identifier type (a frozen
  `RootModel[str]` whose validator consults the registered-targets
  registry — built-ins plus entry-point plugins). The static type is
  `TargetId`, never `str`. Built-in convenience constants
  `BUILTIN_CLAUDE` and `BUILTIN_CODEX` are pre-instantiated `TargetId`
  values for the always-present targets. (A closed `Enum` would
  preclude plugin-registered targets, so we use a registry-bound
  newtype.)
- `Domains` is an `enum.Enum` over the eight names in §7 — closed,
  intentionally; adding a domain is a core change.
- `OnConflict` is an `enum.Enum` (`FAIL`, `KEEP`, `PREFER_TARGET`,
  `PREFER_NEUTRAL`); the `prefer=...` argument is parsed into
  `(OnConflict.PREFER_TARGET, target=BUILTIN_CLAUDE)`.
- `ReasoningEffort`, `SandboxMode`, `ApprovalPolicy`, etc. are typed
  enums (mirroring upstream where present).
- Every neutral key whose value is a fixed-vocabulary string in the
  wire format becomes a `Literal[...]` or `Enum` field in Pydantic.
- Codec routing (§8.1) does not look up keys by string — it consumes
  typed sub-models that the disassembler constructs by Pydantic
  field introspection.

Static enforcement: `uv run ty check` is a verification gate; the CI
configuration treats `Any`, `dict[str, ...]`, and untyped lambdas in
production source as errors. The pass-through bag and serialization
boundary code are explicitly marked in narrow modules.

## 6. Neutral Configuration Format

### 6.1 Why YAML

- Comments survive a round-trip (operator-authored documentation is
  preserved when Chameleon rewrites the file post-merge).
- Anchors and aliases let operators DRY up repeated structures (e.g.
  shared MCP server definitions reused across profiles).
- Strict ordering and indentation discipline align with `ruamel.yaml`,
  which Chameleon uses to preserve comments + key order + flow style
  across writes.
- Pydantic models export JSON-Schema; we publish a `$schema` URL so
  editors get autocomplete and validation.

### 6.2 File location

- **User scope (V0):** `$XDG_CONFIG_HOME/chameleon/neutral.yaml`
  (default `~/.config/chameleon/neutral.yaml`).
- **Project scope (deferred):** `.chameleon/neutral.yaml` at the repo
  root, merged over user scope. Schema designed for it; CLI flag
  `--scope=user|project|both` reserved.
- Override: `--neutral <path>` flag and `CHAMELEON_NEUTRAL` env var.

### 6.3 Top-level shape

```yaml
schema_version: 1

# the implicit base profile — these eight domains plus the targets
# escape hatch are what the operator edits day-to-day
identity:
  reasoning_effort: high          # target-shared scalar
  thinking: true                  # target-shared scalar
  model:                          # target-specific (see §7.1)
    claude: claude-sonnet-4-7
    codex: gpt-5.4
directives:
  system_prompt_file: ~/.config/chameleon/AGENTS.md
  commit_attribution: "Generated with Chameleon"
capabilities:
  mcp_servers:
    memory:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-memory"]
authorization: { ... }            # deferred: codecs land in a follow-on spec
environment:
  variables:
    CI: "true"
lifecycle: { ... }                # deferred
interface: { ... }                # deferred
governance: { ... }               # deferred

# named overlay profiles — re-specify any subset of any domain
profiles:
  deep-review:
    identity:
      reasoning_effort: high
      model:
        claude: claude-opus-4-7
        codex: gpt-5-pro
    authorization:
      default_mode: read-only

# pass-through escape hatch for target-unique features (typed per
# target — see §7.2)
targets:
  claude:
    voice: { enabled: true, mode: tap }
    spinner_verbs: { mode: append, verbs: [Pondering] }
  codex:
    personality: pragmatic
    apps:
      google_drive: { enabled: false }
```

`schema_version: 1` is required. Major versions trigger explicit
migration prompts; minor schema additions are forward-compatible.

## 7. Schema Ontology — Eight Domains

Each domain is its own Pydantic model in `chameleon/schema/<domain>.py`.
Together they form `Neutral` in `chameleon/schema/neutral.py`. The
following table also serves as the V0 implementation map.

| Domain | Concern | Representative neutral keys | Maps to (Claude) | Maps to (Codex) | V0? |
|---|---|---|---|---|---|
| **identity** | model + auth + endpoint | `model`, `provider`, `reasoning_effort`, `thinking`, `service_tier`, `context_window`, `auth.method`, `auth.api_key_helper`, `endpoint.base_url` | `model`, `effortLevel`, `alwaysThinkingEnabled`, `apiKeyHelper`, `awsCredentialExport`, `forceLoginMethod`, `modelOverrides` | `model`, `model_provider`, `model_reasoning_effort`, `[model_providers.*]`, `cli_auth_credentials_store`, `forced_login_method`, `service_tier`, `model_context_window` | yes (subset) |
| **directives** | how the agent thinks/writes | `system_prompt_file`, `output_style`, `language`, `personality`, `commit_attribution`, `verbosity`, `show_thinking_summary` | `outputStyle`, `language`, `attribution.commit`, `attribution.pr`, `showThinkingSummaries`, `includeGitInstructions` | `model_instructions_file`, `developer_instructions`, `personality`, `commit_attribution`, `model_verbosity`, `model_reasoning_summary`, `hide_agent_reasoning` | yes (`commit_attribution` + `system_prompt_file`) |
| **capabilities** | what the agent can use | `mcp_servers.<name>`, `skills`, `subagents.<name>`, `plugins`, `web_search`, `apps.<name>` | `enabledMcpjsonServers`, `disabledMcpjsonServers`, `enableAllProjectMcpServers`, `enabledPlugins`, `extraKnownMarketplaces`, `agent`, `disableSkillShellExecution` | `[mcp_servers.<id>]`, `[agents.<id>]`, `[apps.<name>]`, `[[skills.config]]`, `web_search`, `[features]` | yes (`mcp_servers` only) |
| **authorization** | what the agent may do | `default_mode`, `filesystem.{allow,deny}_{read,write}`, `network.{allowed,denied}_domains`, `network.allow_local_binding`, `approval` (granular block) | `permissions.{allow,ask,deny}`, `permissions.defaultMode`, `permissions.additionalDirectories`, `sandbox.*` | `approval_policy`, `sandbox_mode`, `[sandbox_workspace_write]`, `[permissions.<name>]`, `default_permissions`, `--yolo` | no (deferred — own spec) |
| **environment** | execution context | `variables.<KEY>`, `inherit` (none/core/all), `include_only`, `exclude`, `additional_directories`, `worktree.*`, `respect_gitignore` | `env`, `permissions.additionalDirectories`, `worktree.symlinkDirectories`, `worktree.sparsePaths`, `respectGitignore` | `[shell_environment_policy]`, `sandbox_workspace_write.writable_roots`, `sandbox_workspace_write.exclude_*`, `project_root_markers` | yes (`variables` only) |
| **lifecycle** | events around actions | `hooks.<event>`, `history.persistence`, `history.max_bytes`, `telemetry.exporter`, `telemetry.endpoint`, `cleanup_period_days`, `plans_directory` | `hooks.*`, `disableAllHooks`, `cleanupPeriodDays`, `plansDirectory`, `feedbackSurveyRate`, `otelHeadersHelper` | `[history]`, `[hooks]` (planned), `[otel]`, `[features].codex_hooks` | no (deferred) |
| **interface** | human-facing UX | `tui.fullscreen`, `tui.theme`, `editor_mode`, `status_line.command`, `file_opener`, `voice.enabled`, `notification_channel`, `motion.reduced` | `tui`, `editorMode`, `viewMode`, `statusLine`, `voice`, `prefersReducedMotion`, `preferredNotifChannel`, `terminalProgressBarEnabled`, `awaySummaryEnabled` | `[tui]`, `file_opener`, `disable_paste_burst`, `notify`, `hide_agent_reasoning` | no (deferred) |
| **governance** | rules about rules | `managed.*`, `trust.<path>`, `updates.channel`, `updates.minimum_version`, `features.<flag>`, `plugins.marketplaces` | `allowManagedHooksOnly`, `allowManagedMcpServersOnly`, `strictKnownMarketplaces`, `blockedMarketplaces`, `autoUpdatesChannel`, `minimumVersion`, managed-only `*` keys, per-project trust in `~/.claude.json` | `[projects.<path>]`, `[features]`, `requirements.toml` (separate file), `check_for_update_on_startup` | no (deferred) |

> **Note on `profiles`:** Profiles are a structural overlay sibling of the
> eight domains (see §6.3), not a governance concern. They re-specify any
> subset of any domain under a name. The neutral schema treats `profiles`
> as a top-level key; governance does not.

> **Note on `Domains` enum:** The eight names above (`identity`,
> `directives`, `capabilities`, `authorization`, `environment`,
> `lifecycle`, `interface`, `governance`) are members of an
> `enum.Enum` named `Domains` in `chameleon/schema/_constants.py`.
> Codec registration, drift records, conflict records, and CLI
> output all use `Domains` members, never strings. Adding a new
> domain requires editing this enum (and the `Neutral` model), which
> is the entire surface for the rare event of adding a domain.

V0 ships codecs only for the keys flagged "yes" above. All other
domains have full Pydantic schemas (so the YAML is editable now), but
their codecs are stubbed with `raise NotImplementedError("planned in
spec X")` and matching `pytest.xfail` tests. This is the architecture
contract: the structure is settled, the volume is incremental.

**Behavior when neutral references an unimplemented domain.** The
merge engine catches `NotImplementedError` from stub codecs and:

1. Logs a typed `UnimplementedDomain` warning naming the
   `(target, domain)` pair.
2. Skips that domain in *both* directions for that target — the
   neutral value is neither written to the target nor harvested
   from the target.
3. Preserves the neutral value in `N₂` unchanged, so the operator
   keeps their authored configuration. When a future codec lands,
   the next merge automatically activates.

This makes "design end-to-end, ship V0 thin" non-destructive.
Operators who write `authorization.default_mode: read-only` in
their neutral today get a warning that nothing is being applied,
but the value stays put for the day the codec arrives.

### 7.1 Per-target values within shared domains

Some neutral keys are inherently target-specific even when they sit
inside a shared domain. The most prominent is `identity.model`:
Claude understands `claude-sonnet-4-7`; Codex understands
`gpt-5.4`. There is no universal model-name vocabulary, and we
should not invent one.

The schema represents these as a `Mapping[TargetId, V]` instead of a
scalar:

```yaml
identity:
  reasoning_effort: high              # target-shared scalar
  thinking: true                      # target-shared scalar
  model:                              # target-specific
    claude: claude-sonnet-4-7
    codex: gpt-5.4
  endpoint:
    base_url:                         # target-specific
      claude: https://api.anthropic.com
      codex: https://api.openai.com
```

Pydantic enforces this: the field's type is
`Mapping[TargetId, ModelName]` where `ModelName` is itself the
union of per-target generated `Literal` types from `_generated.py`.
A scalar at this position is rejected by validation with a clear
message ("identity.model is target-specific; provide a mapping").
Each target's `to_target` codec reads only the value at its own
TargetId.

This pattern is opt-in per field — most identity keys (e.g.
`reasoning_effort`) genuinely share a vocabulary across targets and
remain scalar. The schema declares which fields are per-target via
their type alone; no per-field annotation is needed beyond Pydantic
generics. The decision is made once per neutral key when its
domain's schema module is authored.

### 7.2 Pass-through namespace (`targets.<name>`)

Anything a target codec doesn't claim during reverse codec runs is
harvested into `targets.<target>.<original-key>` of neutral. On
forward, the assembler splices `targets.<self>` back into the target's
output before writing.

This is what makes "design end-to-end, ship V0 thin" safe. Operators
can configure target-unique features today (`targets.claude.voice`,
`targets.codex.personality`) and they round-trip correctly even though
no codec understands them. When a future spec promotes a feature to a
neutral domain, the codec replaces the pass-through path; existing
neutral files keep working under a deprecation warning.

## 8. Transpiler Architecture

### 8.1 Three small contracts

**Codec** — pure function pair, one per `(target, domain)`. The
neutral and target sides are both typed Pydantic models; routing is
done by typed field-path traversal, not string lookup:

```python
class FieldPath(NamedTuple):
    """A path through a Pydantic model's nested field hierarchy.
    Each element is the literal field name in the parent model.
    Statically validated against the FullTargetModel at codec import
    time via a startup check that walks the path through model_fields.
    """
    segments: tuple[str, ...]

class Codec[NeutralDomainModel: BaseModel, TargetSectionModel: BaseModel](Protocol):
    target: ClassVar[TargetId]                # registry-validated newtype, not str
    domain: ClassVar[Domains]                 # closed enum, not str

    # The set of field paths in the target's FullTargetModel this codec
    # claims responsibility for. Paths may be nested (e.g.
    # ("permissions", "allow") for the authorization codec on Claude)
    # and may overlap with other domains' claims — a path *prefix* may
    # be shared across domains, but each terminal path may be claimed
    # by at most one codec, enforced by a startup check.
    claimed_paths: ClassVar[frozenset[FieldPath]]

    # The Pydantic submodel the codec consumes/produces. Its field
    # structure mirrors the SHAPE of FullTargetModel restricted to
    # claimed_paths. The disassembler constructs an instance by
    # walking each claimed path through FullTargetModel and slotting
    # values into the equivalent path in TargetSectionModel.
    target_section: ClassVar[type[TargetSectionModel]]

    @staticmethod
    def to_target(
        model: NeutralDomainModel, ctx: TranspileCtx,
    ) -> TargetSectionModel: ...

    @staticmethod
    def from_target(
        section: TargetSectionModel, ctx: TranspileCtx,
    ) -> NeutralDomainModel: ...
```

`to_target` and `from_target` are stateless, deterministic, and never
do I/O. They consume typed Pydantic submodels and return typed
Pydantic submodels — never dicts. `ctx` carries shared inputs (current
scope, profile name, warning collector). Codecs may emit `LossWarning`
via `ctx` when a neutral input is encoded with documented information
loss (e.g. a neutral list is silently deduplicated, or a neutral key
has no equivalent on this target and is being dropped); operators see
these on stderr.

**Why field paths, not just field names.** Real targets have nested
sections that straddle neutral domains: Claude's
`permissions.allow/ask/deny/defaultMode` belongs to authorization,
while `permissions.additionalDirectories` belongs to environment.
Top-level field claims would force one codec to own the whole
`permissions` subtree. Path-level claims allow domain-correct
ownership of leaves. Two codecs may share the *prefix* `("permissions",)`
but no two codecs may share a terminal path; the startup check
detects collisions and refuses to load the registry.

Codecs do **not** decide pass-through — that's the disassembler's
job (below). A codec only sees a typed submodel whose fields the
disassembler populated; nothing else can reach codec code.

**Assembler** — one per target, knows file layout but no semantics
beyond key routing:

```python
class Assembler[FullTargetModel: BaseModel](Protocol):
    target: ClassVar[TargetId]
    full_model: ClassVar[type[FullTargetModel]]   # the generated upstream model (§8.4)
    files: ClassVar[tuple[FileSpec, ...]]          # paths, content-types, ownership rules

    @staticmethod
    def assemble(
        per_domain: Mapping[Domains, BaseModel],   # one TargetSectionModel per domain
        passthrough: PassThroughBag,               # typed bag of unclaimed fields
        ctx: AssembleCtx,
    ) -> Mapping[Path, FileBytes]: ...

    @staticmethod
    def disassemble(
        files: Mapping[Path, FileBytes], ctx: DisassembleCtx,
    ) -> tuple[Mapping[Domains, BaseModel], PassThroughBag]: ...
```

The disassembler:
1. Parses the live target files into the generated `FullTargetModel`
   (§8.4) using the appropriate format codec (yaml/json/toml from
   `io/`).
2. Walks each registered codec's `claimed_paths`. For each path,
   reads the value at that path inside `FullTargetModel` and slots
   it into the equivalent path inside an instance of
   `codec.target_section`. Two codecs may share a path prefix; no
   two may share a terminal path (enforced at registry load time).
3. Any path in `FullTargetModel` that no codec's `claimed_paths`
   covers — plus any value held in `FullTargetModel`'s
   `additional_properties` overflow (relevant for Claude — see
   §8.4) — is recorded in the target's typed `PassThroughBag`. The
   bag is parametric over target: `PassThroughBag[ClaudeSettings]`
   stores values shaped per Claude's generated model;
   `PassThroughBag[CodexConfig]` stores Codex-shaped values
   (preserving e.g. TOML datetime types). It is *not* a generic
   `dict[str, JsonValue]`.

The assembler does the inverse: takes the per-domain typed submodels
plus the pass-through bag and rebuilds a `FullTargetModel`, then
serializes it via the format codec to file bytes. If a path appears
in both a codec's section and the pass-through bag (which can happen
if the operator manually populated `targets.<target>.<path>` while
the schema also covers it), the typed claim wins; the disassembler
emits a `ShadowedPassThrough` warning naming the offending path.

**Target plugin** — declared via `chameleon.targets` Python entry point:

```toml
# in a third-party package's pyproject.toml
[project.entry-points."chameleon.targets"]
roo = "roo_chameleon:RooTarget"
```

`RooTarget` exposes its eight codecs and its assembler. Chameleon
discovers it on import without further configuration. Built-in
targets (`claude`, `codex`) register themselves the same way; nothing
about V0 makes the built-ins privileged at runtime.

### 8.2 Why domain-centric

A new *domain* is the rare event (one in the project lifetime per
domain). A new *target* is the common event (every new agent). Two
arrangements were considered:

- **Target-centric** — `targets/<target>/` holds one big transpiler
  module. Easy to onboard one target end-to-end. But adding a domain
  later requires editing every target's transpiler in lockstep, and
  the neutral schema is implicit (each transpiler's expectations are
  the schema).
- **Domain-centric (chosen)** — `schema/<domain>.py` defines the
  neutral schema for that domain in one place. `codecs/<target>/<domain>.py`
  files implement codec pairs in parallel. New target = ship a package
  with eight small codec files + an assembler. New domain = one schema
  file + N codec files (parallelizable across PRs).

Codecs and schema are colocated only by domain, never by target —
this keeps the neutral schema centrally edit-controlled.

### 8.3 Layout

```
src/chameleon/
├── __init__.py                 # __version__
├── cli.py                      # argparse + subcommand dispatch
├── schema/
│   ├── __init__.py
│   ├── neutral.py              # composes domains + profiles + targets
│   ├── identity.py
│   ├── directives.py
│   ├── capabilities.py
│   ├── authorization.py
│   ├── environment.py
│   ├── lifecycle.py
│   ├── interface.py
│   └── governance.py
├── codecs/
│   ├── __init__.py
│   ├── _registry.py            # plugin discovery + lookup keyed on (TargetId, Domains)
│   ├── claude/
│   │   ├── __init__.py
│   │   ├── _generated.py       # GENERATED: full Pydantic ClaudeSettings (§8.4); checked-in artefact
│   │   ├── identity.py         # imports ClaudeIdentitySection (a typed slice of _generated)
│   │   ├── directives.py
│   │   ├── capabilities.py
│   │   ├── authorization.py    # raises NotImplementedError in V0
│   │   ├── environment.py
│   │   ├── lifecycle.py        # raises NotImplementedError in V0
│   │   ├── interface.py        # raises NotImplementedError in V0
│   │   └── governance.py       # raises NotImplementedError in V0
│   └── codex/
│       ├── __init__.py
│       ├── _generated.py       # GENERATED: full Pydantic CodexConfig (§8.4)
│       └── ...                 # parallel structure to claude
├── targets/
│   ├── __init__.py
│   ├── _registry.py            # entry-point discovery; binds plugin TargetIds at startup
│   ├── claude/
│   │   ├── __init__.py         # ClaudeTarget class wiring codecs + assembler
│   │   └── assembler.py        # settings.json + .mcp.json + ~/.claude.json layout
│   └── codex/
│       ├── __init__.py
│       └── assembler.py        # config.toml + requirements.toml layout
├── merge/
│   ├── __init__.py
│   ├── drift.py
│   ├── conflict.py
│   ├── resolve.py
│   └── engine.py
├── state/
│   ├── __init__.py
│   ├── git.py                  # subprocess `git` wrapper around per-target repos (no GitPython dep)
│   ├── transaction.py          # typed merge-transaction markers (§4.6)
│   ├── locks.py                # fcntl-based file locking for partial-ownership writes (§10.5)
│   └── paths.py                # XDG resolution + assembler FileSpec → live/repo paths
└── io/
    ├── __init__.py
    ├── yaml.py                 # ruamel.yaml wrapper preserving comments + ordering
    ├── json.py                 # stdlib json with insertion-order preservation + indent=2
    └── toml.py                 # tomlkit wrapper preserving comments + ordering
```

`ruamel.yaml`, `tomlkit`, and stable-ordered JSON are chosen
specifically to preserve operator-authored formatting through a
round-trip — this is what makes `git diff` on the live files
informative rather than noise. (Claude's `settings.json` format is
plain JSON without comment support, so `tomlkit`-style comment
preservation isn't applicable; key-order preservation is enough.)

### 8.4 Upstream schema canonization

Every target's typing grounds out in a JSON Schema produced by the
upstream authority for that target. We never hand-author the
generated `FullTargetModel`; we generate it from the upstream schema
and check the generated artefact into git so the runtime path needs
no codegen tooling.

| Target | Canonical authority | Format we ingest | Generation pipeline |
|---|---|---|---|
| **claude** | `https://json.schemastore.org/claude-code-settings.json` (Draft-07 JSON Schema; loose: `additionalProperties: true`) | JSON Schema Draft-07 | vendored at `tools/sync-schemas/upstream/claude.schema.json`; regenerated via `datamodel-code-generator --input-file-type jsonschema` to `src/chameleon/codecs/claude/_generated.py` |
| **codex** | `codex-rs/protocol/src/config_types.rs` + `codex-rs/core/src/config/` (Rust types deriving `schemars::JsonSchema`) | JSON Schema (emitted by a small Rust binary we vendor under `tools/sync-schemas/codex/`) | runs the Rust binary to dump JSON Schema, then `datamodel-code-generator` to `src/chameleon/codecs/codex/_generated.py` |

Each target's `_generated.py` is a Pydantic model with full type
fidelity to its upstream schema — nested models, enums, discriminated
unions where the schema declares them. The generated file is
**vendored** (committed to git) for two reasons: (1) `uv sync` must
not require Rust toolchain or network access; (2) regenerations
become reviewable diffs against codec expectations.

Pinning and refresh:

- `tools/sync-schemas/pins.toml` records the exact upstream commit
  for both Claude (schemastore git SHA) and Codex (Codex CLI git SHA
  + Rust toolchain version). The build does not refresh implicitly.
- `tools/sync-schemas/sync.py` is the operator-run command. It pulls
  upstream at the pinned ref, regenerates `_generated.py` for each
  target, and runs `uv run pytest -m schema_drift` to surface codec
  expectations that no longer match upstream.
- Refreshes are explicit PRs that bump `pins.toml`, regenerate the
  vendored schemas + Pydantic models, and update any codecs whose
  contracts broke. Reviewers see exactly what changed upstream.

Loose-schema handling (Claude side):

The Claude JSON Schema declares `additionalProperties: true` at the
root and inside several `$defs`. The generated Pydantic model
mirrors this with a `model_config = ConfigDict(extra="allow")` plus
an explicit `additional_properties: dict[str, JsonValue]` field for
the disassembler to harvest. Properties absent from the schema but
present in real configs land here automatically and round-trip
through pass-through. This pattern is **also** used on the Codex
side for `[unknown sections]` to defend against upstream additions
landing between sync refreshes.

### 8.5 Round-trip equivalence as test contract

For every `(target, domain)` codec pair, the test suite asserts:

```python
neutral_in = arbitrary_valid(domain)              # property-based via hypothesis
target_section = codec.to_target(neutral_in, ctx)
neutral_out = codec.from_target(target_section, ctx)
assert canonicalize(neutral_in) == canonicalize(neutral_out)
```

`canonicalize` accounts for legitimately lossy transformations
(deduplicated lists, sorted keys). Where a codec is genuinely
lossy in a target direction, the test marks the lossy axes
explicitly and the codec emits a `LossWarning` at runtime so
operators see what was dropped.

## 9. CLI Surface

All commands are subcommands of `chameleon`. All mutating commands
accept `--dry-run`, `--verbose`/`--quiet`, and `--neutral <path>`.
The `--scope` flag is parsed but accepts only `user` in V0 (project
scope deferred — see §15).

V0 commands:

| Command | Purpose |
|---|---|
| `chameleon init` | First-time bootstrap; behavior depends on which artefacts exist (§9.2) |
| `chameleon merge` | The core round-trip. `--on-conflict=fail\|keep\|prefer=<target>\|prefer=neutral\|prefer=lkg` controls non-interactive behavior |
| `chameleon status` | Per-target drift summary. Exit 0 if clean, exit 1 if drift, exit 2 if conflict pending |
| `chameleon diff <target>` | Detailed drift listing (domain → field-path → live vs. HEAD) |
| `chameleon log <target> [--json]` | Pretty timeline from the target's state-repo, parsing the structured commit trailers |
| `chameleon adopt <target>` | Equivalent to `merge --on-conflict=prefer=<target>` |
| `chameleon discard <target>` | Overwrite live files with state-repo HEAD; throw away drift; does *not* modify neutral |
| `chameleon validate` | Run schema validation against neutral file; exit 0/1 |
| `chameleon doctor` | Environment health: tool versions, paths exist, state-repos consistent, neutral file parseable, target files writable, no stale merge-transaction markers (§4.6), no unresolved login-time conflicts (§9.3) |
| `chameleon targets list` | List registered targets (built-in + entry-point plugins) |

Deferred to follow-on specs:
`chameleon profile use <name>` (overlay application without
modifying neutral — see §15.7).

### 9.2 `chameleon init` decision matrix

`init` is the only command that operates safely against missing or
partial state. It branches on the cross-product of "neutral file
exists" × "state-repos exist for any target":

| Neutral exists | Any state-repo exists | Behavior |
|---|---|---|
| no | no | **Bootstrap.** Sample live target files (if any). Reverse-codec to build a starter neutral. Conflicts (cross-target) get the standard resolution UI. Write neutral, commit each state-repo with `init: bootstrap from live`. If no live target files exist either, write a minimal scaffold neutral with sensible defaults and create empty state-repos (so subsequent `merge` is a no-op). |
| yes | no | **Adopt-existing-neutral.** Forward-codec the neutral to compute target files. If live target files exist and differ, treat as drift and fall through to a normal merge. Commit each state-repo with `init: from neutral` or `init: drift-merged`. |
| no | yes | **Reverse-engineer-neutral.** Read each state-repo HEAD, reverse-codec to build a neutral, prompt on conflicts. Compare against live (which may have drifted from HEAD); incorporate via merge. Write neutral. State-repo gets a `init: backfill neutral` commit. |
| yes | yes | **No-op or merge.** If everything is consistent, exit 0. Otherwise behave exactly like `chameleon merge`. |

`init` is idempotent in every cell.

### 9.3 Login-time invocation

Chameleon does not ship installers. Documented recipes:

- **macOS / launchd:** plist sample for `~/Library/LaunchAgents/io.waugh.chameleon.plist` running `chameleon merge --on-conflict=fail`. Fails closed on conflict so the operator sees it on next interactive shell (via §9.4).
- **Linux / systemd user:** `~/.config/systemd/user/chameleon.service` + `chameleon.timer` (or just OnLogin via `pam_systemd`).
- **Shell rc:** simple `chameleon merge --on-conflict=keep --quiet || true` snippet for `.zlogin` users, with the explicit acknowledgment that `keep` is permissive.

These recipes live in `docs/login/` and are advisory; nothing about
the tool requires login-time use.

### 9.4 Surfacing login-time failures

A login-time `chameleon merge --on-conflict=fail` that hits a
conflict has nowhere visible to fail to — launchd swallows stderr to
a system log most operators never read. To bridge this gap:

- On any non-zero login-time exit, Chameleon writes a typed
  `LoginNotice` (Pydantic model) to
  `~/.local/state/chameleon/notices/<utc-timestamp>.toml` containing
  the merge-id, exit code, brief reason, and pointer to the full
  conflict report.
- `chameleon doctor` lists outstanding notices on every invocation
  and exits non-zero if any exist.
- Operators are encouraged (in `docs/login/`) to add a one-liner
  to their interactive shell rc:
  `command -v chameleon >/dev/null && chameleon doctor --notices-only --quiet || true`.
  This surfaces a one-line warning at next interactive shell.

The notices directory is operator-purgeable (`chameleon doctor
--clear-notices`) and self-purges entries older than 30 days.

## 10. File System Layout

### 10.1 What Chameleon owns

- `~/.local/state/chameleon/` (or `$XDG_STATE_HOME/chameleon/`) — entirely.
  State-repos and per-target metadata.
- The neutral file at `~/.config/chameleon/neutral.yaml` (or wherever
  `--neutral`/`CHAMELEON_NEUTRAL` points). Operator-owned content;
  Chameleon writes preserving comments.

### 10.2 What Chameleon writes (the live target files)

Declared per-target by the assembler's `files` table. V0:

| Target | Path | Owned? |
|---|---|---|
| claude | `~/.claude/settings.json` | yes |
| claude | `~/.claude.json` | partial (mcpServers section only; rest preserved) |
| claude | `<project>/.mcp.json` (project scope, deferred) | yes |
| codex | `~/.codex/config.toml` | yes |
| codex | `~/.codex/requirements.toml` | yes |
| codex | `<project>/.codex/config.toml` (project scope, deferred) | yes |

Critically: `~/.claude.json` is mostly OAuth / session state. The
assembler treats it as a *partial-ownership* file — only the
`mcpServers` key is rewritten; everything else is read-then-written-back
to avoid clobbering the agent's state. This pattern (partial
ownership) is a first-class concept in the assembler interface, not a
Claude-specific kludge.

### 10.3 What Chameleon never touches

- The agent binaries, OAuth tokens, history files, log directories.
- `CLAUDE.md`, `AGENTS.md`, or any markdown memory file. These are
  prompt-engineering artifacts, not config; `directives.system_prompt_file`
  *points* at them but never edits them.
- Any path outside an assembler's declared `files` table.

### 10.4 Path escaping in state-repos

Live files at e.g. `~/.claude.json` contain a leading dot. We could
mirror them verbatim, but a leading-dot file at the *root* of a git
repo is fine while a leading-dot file inside `settings/` is also
fine — git handles both. The hazard is purely a UX one: an operator
running `ls` in the state-repo wouldn't see them.

Convention: dotfiles are stored under `settings/dotfiles/` with the
leading dot stripped. So `~/.claude.json` lives at
`settings/dotfiles/claude.json` in the state-repo, and `~/.claude/
settings.json` lives at `settings/claude/settings.json`. The
assembler's `files: tuple[FileSpec, ...]` table is the single
source of truth for the live-path ↔ state-repo-path mapping; both
the assembler and disassembler consult it. `FileSpec` is a typed
Pydantic model: `{ live_path: Path, repo_path: PurePosixPath,
ownership: FileOwnership, format: FileFormat }`. No string
manipulation in the routing path.

### 10.5 Concurrency for partial-ownership files

`~/.claude.json` is the canonical example: Chameleon owns only the
`mcpServers` key; everything else (OAuth tokens, project trust
state, caches) belongs to Claude Code, which writes the file
constantly during normal operation. A naive read-modify-write loses
any of Claude Code's concurrent updates that land between our read
and our write.

Discipline for any file flagged `ownership = FileOwnership.PARTIAL`:

1. **Read-time hash.** When sampling, capture the file's SHA-256
   along with its bytes. Record both on the merge transaction
   marker (§4.6) under `partial_owned_hashes`.
2. **OS file lock during write.** Acquire `fcntl.flock(LOCK_EX)`
   on the file (or the platform-equivalent on the file's directory)
   before reading-for-modification. Hold the lock through the
   write-temp + rename + lock-release sequence.
3. **Re-hash before writing.** Inside the lock, re-read the file
   and compute its SHA-256. If it differs from step 1's hash:
   - Re-parse the file.
   - Re-apply the merge result *only* to Chameleon-owned keys
     (`mcpServers`), preserving everything else as currently on
     disk.
   - Update the merge transaction marker to record that a
     concurrent update was absorbed.
4. **Atomic rename.** Write to a temp file in the same directory
   then `os.rename` over the original.

This is optimistic concurrency at the byte level: we never assume
exclusivity, but we do enforce key-level non-interference. If
Claude Code writes to its OAuth token between steps 1 and 2,
step 3 detects the change and absorbs it without losing the
token. Pure-ownership files (`~/.claude/settings.json`,
`~/.codex/config.toml`) skip steps 1 and 3 — they're always
fully written.

## 11. V0 Implementation Scope

V0 ships:

- The full eight-domain Pydantic schema, the profiles overlay, and the
  pass-through namespace — all typed (no `dict[str, Any]` in domain
  code).
- Generated `_generated.py` for `claude` (from schemastore.org Draft-07
  JSON Schema) and `codex` (from `codex-rs` Rust types via the
  vendored sync binary). Both checked into git at pinned upstream refs
  recorded in `tools/sync-schemas/pins.toml`.
- Codecs for: `identity` (model + reasoning_effort + provider + auth
  method), `directives.commit_attribution` + `directives.system_prompt_file`,
  `capabilities.mcp_servers`, `environment.variables`. Two targets ×
  four domain-slices ≈ 8 codec files; each codec consumes typed
  Pydantic submodels of the corresponding `_generated.py`.
- Assemblers for `claude` and `codex` covering the live files in §10.2,
  including pass-through harvesting through the typed `additional_properties`
  overflow.
- Merge engine with interactive + non-interactive conflict resolution,
  the four-source change model (§4.3), conflict classification
  (§5.3), and transaction recovery (§4.6). All conflict, drift, and
  transaction-marker records are typed Pydantic models.
- Per-target git state via subprocess `git` (no `GitPython`
  dependency).
- Partial-ownership concurrency discipline (§10.5) implemented for
  `~/.claude.json` and provided as a reusable building block for
  future targets.
- CLI commands: `init`, `merge`, `status`, `diff`, `log`, `adopt`,
  `discard`, `validate`, `doctor`, `targets list`. (`profile use`
  deferred.) CLI argument parsing produces typed `TargetId`, `Domains`,
  `OnConflict` values; no string `match` statements downstream.
- `tools/sync-schemas/sync.py` operational, with at least one
  successful refresh recorded in pins.toml.
- Documentation: README, AGENTS.md (with CLAUDE.md symlink), the
  schema reference auto-generated from Pydantic models, login-time
  recipe stubs, plugin authoring guide, schema-sync runbook.

V0 does **not** ship: codecs for `authorization`, `lifecycle`,
`interface`, `governance`; project-scope neutral file support;
`profile use`; multi-machine state sync; first-class Windows
support (Linux + macOS only — Windows is documented as "should
work, not tested in CI").

## 12. Testing Strategy

- **Unit, per-domain Pydantic schema tests** — round-trip a
  hand-curated corpus of valid YAML through model parse/dump.
- **Property-based codec tests (hypothesis)** — for each
  `(target, domain)` pair, generate arbitrary valid neutral fragments
  and assert `from_target(to_target(x)) == canonicalize(x)`.
- **Golden-file integration tests** — committed pairs of
  `tests/golden/<scenario>/neutral.yaml` and per-target expected
  output. Running `to_target` chain produces byte-identical output.
- **Conflict-resolution scenarios** — dedicated test corpus in
  `tests/conflicts/` driving the resolver with fake TTY + each
  `--on-conflict` strategy.
- **State-repo integration tests** — temp dir fixtures that
  initialize fake target homes, run `init` → manual edit → `merge`
  → assert state-repo commit graph and live-file content. The
  fixture corpus exercises the §9.2 init matrix (all four cells)
  and the §5.3 conflict classification table (every row).
- **Transaction recovery tests** (`tests/recovery/`) — simulate
  interruption between merge steps 9 and 11 by killing the merge
  process at controlled points; assert that the next `merge` or
  `doctor` recovers consistently per §4.6.
- **Partial-ownership concurrency tests** (`tests/concurrency/`) —
  spawn a background writer modifying `~/.claude.json`'s OAuth
  token field while a merge is mid-flight; assert no data loss
  per §10.5.
- **CLI smoke tests** — invoke each subcommand via subprocess against
  a fixture environment.
- **Schema drift tests** (`tests/schema_drift/`) — assert that every
  codec's `target_section` declares only field names that exist in
  the corresponding generated `_generated.py` model. Catches the
  case where an upstream schema regeneration removed a field a codec
  still expects.
- **Typing-rule audit** — `tests/typing_audit.py` greps the production
  source for forbidden patterns (`dict[str, Any]`, bare `: str` for
  enum-able fields named `target`/`domain`/`mode`/`policy`/etc.). Cheap,
  blunt, effective at catching regressions on the "no strings" rule.

Verification gates (must pass before any commit claims completion):

```sh
uv run ruff check
uv run ruff format --check
uv run ty check
uv run pytest
```

## 13. Plugin Model for New Targets

A target plugin is a Python package with:

1. A class implementing the `Target` protocol (eight codecs + one
   assembler + path table + a static `target_id: TargetId` it claims
   on registration).
2. A vendored `_generated.py` produced from its own canonical schema
   authority via the same `tools/sync-schemas/` pattern. The plugin's
   docs explain how to refresh it; Chameleon does not require a
   shared codegen toolchain across plugins.
3. An entry point in its `pyproject.toml`:
   ```toml
   [project.entry-points."chameleon.targets"]
   <target-name> = "<package>.<module>:<TargetClass>"
   ```
   On import, Chameleon's registry adds the plugin's `target_id` to
   the set `TargetId`'s validator consults, so the new identifier is
   instantly typed and accepted.
4. `pip install`-able into the same environment as `chameleon`; uv
   auto-discovers it on next run.

Plugin authoring guide (in `docs/plugins/`) covers: how to declare
target_section submodels against the plugin's `_generated.py`, how
to handle partial-ownership files, how the registry binds `TargetId`
at startup, and how to write golden tests against a published
`chameleon-test-kit` helper package (deferred — listed under §15
open questions).

## 14. Project Setup (Tooling)

### 14.1 Repository layout

```
chameleon/
├── .git/
├── .gitignore
├── LICENSE                      # MIT, (c) 2026 Robert Waugh
├── README.md
├── AGENTS.md
├── CLAUDE.md -> AGENTS.md
├── pyproject.toml
├── uv.lock                      # committed
├── docs/
│   ├── superpowers/specs/       # design history (this file lives here)
│   ├── login/                   # launchd/systemd/zlogin recipes
│   ├── plugins/                 # plugin authoring guide
│   └── schema/                  # auto-generated reference
├── src/chameleon/               # see §8.3
├── tools/
│   └── sync-schemas/            # operator-run; never invoked at install or runtime
│       ├── pins.toml            # pinned upstream refs for claude + codex
│       ├── sync.py              # orchestrator: pull → emit → datamodel-codegen
│       ├── upstream/
│       │   └── claude.schema.json   # vendored snapshot at the pinned ref
│       └── codex/
│           ├── Cargo.toml       # tiny Rust binary that imports codex's config
│           │                    # types and dumps schemars JsonSchema as JSON
│           └── src/main.rs
├── tests/
│   ├── unit/
│   ├── property/
│   ├── golden/<scenario>/
│   ├── conflicts/
│   ├── integration/
│   └── schema_drift/            # asserts vendored upstream schemas still match codec expectations
└── skills/                      # Claude Code skills shipped with the project
    └── README.md                # placeholder; skills added as workflows mature
```

### 14.2 `pyproject.toml` essentials

- Build backend: `hatchling`.
- `requires-python = ">=3.12"` (PEP 695 generics; ty's effective floor).
- `[project] dynamic = ["version"]` with version sourced from
  `src/chameleon/__init__.py`.
- `[project.scripts] chameleon = "chameleon.cli:main"`.
- Runtime deps: `pydantic>=2`, `ruamel.yaml`, `tomlkit`,
  `platformdirs` (XDG), `rich` (TUI rendering for status/diff/log).
- Dev deps via `[dependency-groups] dev = [...]`: `pytest>=8`,
  `pytest-cov`, `hypothesis`, `ruff>=0.15`, `ty>=0.0.34`.
- Schema-sync deps via `[dependency-groups] schema-sync = [...]`:
  `datamodel-code-generator>=0.27`. Used only by `tools/sync-schemas/`;
  not pulled in by `uv sync` for normal development.
- `[tool.ruff]`: line-length 100, target-version py312, lint
  rules `E,F,I,UP,B,SIM,RUF,PT,PL,N`.
- `[tool.ruff.format]`: enable; preview off.
- `[tool.pytest.ini_options]`: `testpaths = ["tests"]`,
  `addopts = "-ra --strict-markers --strict-config"`,
  `filterwarnings = ["error"]`.
- `[tool.ty]`: `src` includes `["src", "tests"]`; strictness defaults.
- `[tool.hatch.build.targets.wheel] packages = ["src/chameleon"]`.

### 14.3 Runtime assumption

Every command runs through `uv run`. README and AGENTS.md state
this explicitly. Bare `python`, `pip`, and `pytest` invocations are
documented as anti-patterns.

### 14.3.1 External binary dependencies

- **`git`** — required at runtime. The merge engine drives per-target
  state-repos via subprocess. `chameleon doctor` checks for `git` in
  `PATH` and reports a clear error if absent.
- **`uv`** — required for development workflow but not at runtime;
  the `chameleon` console script entry point installs into the venv
  produced by `uv sync` like any other Python tool.
- **`cargo` + Rust toolchain** — required only when running
  `tools/sync-schemas/sync.py codex`, never at install or runtime.
  Operators who never refresh schemas never need Rust.

### 14.4 AGENTS.md / CLAUDE.md content

Sections:
1. Project goal (one paragraph, same as §1).
2. Runtime: every command via `uv run`.
3. Verification gates: ruff check, ruff format --check, ty check,
   pytest. All must pass before claiming work is done.
4. Search tooling: prefer ripvec / semantic search over raw grep
   (organization-level instruction).
5. Round-trip orientation: the goal is bidirectional; codecs
   must round-trip; pass-through is the escape hatch for genuine
   asymmetry.
6. Conflict UX: keep it simple — A-or-B per key, no inline
   editing, no three-way text merging. If a future feature needs
   richer resolution, design it as a separate spec, not a runtime
   patch.
7. Schema discipline: the neutral schema is centrally defined in
   `src/chameleon/schema/`; codecs adapt, they don't redefine.

### 14.5 Initial `.gitignore`

Standard Python + uv + tooling caches:

- `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `build/`, `dist/`
- `.venv/` (uv project venv); `uv.lock` is **not** ignored
- `.pytest_cache/`, `.ruff_cache/`, `.ty_cache/`, `.hypothesis/`, `.coverage`, `htmlcov/`
- `.vscode/`, `.idea/`, `.DS_Store`

### 14.6 Verification of scaffolding

```sh
uv sync
uv run chameleon --help          # prints help including all V0 subcommands, exits 0
uv run pytest                    # all unit + property + golden tests pass; xfail markers stable
uv run ruff check
uv run ruff format --check
uv run ty check
```

## 15. Deferred Decisions (Future Specs)

These are intentionally not answered here. Each gets its own spec
when its time comes; the V0 design accommodates them all without
re-architecture.

1. **Authorization domain** — unifying Claude's `permissions.allow/ask/deny`
   pattern language with Codex's structured `[permissions.<name>]` profiles,
   `approval_policy` granular form, and sandbox network/filesystem rules.
2. **Lifecycle domain** — hooks ABI common across targets, telemetry
   exporter abstraction, history retention.
3. **Interface domain** — TUI/voice/notification abstraction.
4. **Governance domain** — managed-config delivery (MDM, drop-in dirs),
   project trust, plugin marketplace trust, schema migration policy.
5. **Project-scope neutral files** — `.chameleon/neutral.yaml` merged
   over user scope; conflict semantics across scopes.
6. **Sessions teleportation** — moving session/transcript/memory state
   between targets using the same per-target git layout under a new
   artifact class (`sessions/`).
7. **`profile use`** — applying a named overlay temporarily without
   modifying neutral; lifecycle of "leased" target state.
8. **Multi-machine sync** — neutral on machine A reflects edits made on
   machine B. Probably out of scope forever; let the operator put their
   neutral file in their dotfiles repo.
9. **`chameleon-test-kit`** — published helper package giving plugin
   authors a property-test harness for their codecs.
10. **Schema migrations** — when `schema_version` bumps, how Chameleon
    rewrites old neutral files.
11. **Upstream-schema regeneration on a schedule** — V0 makes
    `tools/sync-schemas/sync.py` a manual operator command. A CI job
    that opens regenerate-PRs against the pinned schemas (e.g. weekly,
    automatic) is a natural follow-on once the codec contract is
    stable enough that drift PRs are routine review.
12. **Direct reuse of OpenAI's published Python SDK** — the Codex repo
    ships `sdk/python/src/codex_app_server/.../v2_all.py` (the app-server
    protocol, not the config types). If OpenAI publishes a config-types
    Python package downstream, Chameleon could depend on it instead of
    regenerating from Rust. We do not depend on this happening; the
    schemars-based pipeline is self-sufficient.

---

*End of design.*
