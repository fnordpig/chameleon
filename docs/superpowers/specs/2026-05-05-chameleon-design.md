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

Every state-changing operation is a `merge`. The pipeline:

1. **Sample.** For each target, read its live files and run its
   *disassembler* to produce per-domain section dicts.
2. **Reverse.** Run each domain's `from_<target>` codec on its section
   dict, producing a per-target *neutral candidate* plus that target's
   pass-through harvest (`targets.<target>.*`).
3. **Drift.** Compare live-file SHAs to the per-target state-repo HEAD.
   Anything that differs is drift; the diff is the per-target *delta*
   against the last commit.
4. **Cross-correlate.** For each neutral key, gather the set of drifting
   targets and their proposed values. If exactly one target drifted on
   that key, its value wins automatically. If two or more targets
   drifted with disagreeing values, that key is a *conflict*.
5. **Resolve.** Run conflicts through the resolution protocol (§5).
6. **Compose.** Build the new neutral form by overlaying resolved drifts
   onto the previous neutral. Pass-through namespaces are merged
   per-target.
7. **Re-derive.** For each target, run each domain's `to_<target>` codec
   against the new neutral, run the assembler to produce file content,
   and write live files (atomic rename).
8. **Commit.** In each target's state-repo, write the new live-file
   content and `git commit -m "merge: <one-line summary>"` with a
   structured trailer (§4.4).
9. **Update neutral.** Write the new neutral form atomically. (Neutral
   is the operator's file; we do not enforce git on it, though `git
   diff` works fine if they version-control it.)

If no drift exists in step 3 and the neutral file is unchanged since
last merge, the run is a no-op and exits 0 with `merge: nothing to do`.

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

### 4.5 Operations the operator never sees

- **First-time `init`.** §9 covers the bootstrap when no state-repo and
  no neutral file exist yet.
- **Discard.** `chameleon discard <target>` overwrites the live file
  with `HEAD` of the state-repo, throwing away any drift. Useful when
  the operator knows the agent's edit was a mistake.
- **Adopt.** `chameleon adopt <target>` is shorthand for "merge but
  resolve every conflict in favor of <target>."

## 5. Conflict Resolution Protocol

A conflict is a single (domain, key, [target → value] map) record. The
resolver receives a list of such records and must return a (key →
chosen-value) map.

### 5.1 Interactive mode (TTY)

Each conflict prints a compact diff:

```
authorization.network.allowed_domains
  claude:  ["github.com", "*.npmjs.org", "registry.pypi.org"]
  codex:   ["github.com", "*.npmjs.org"]
  neutral: ["github.com", "*.npmjs.org"]

  [a] claude   [b] codex   [k] keep neutral   [s] skip (leave drift unresolved)
```

`s` (skip) leaves the live file alone for that key — Chameleon will
re-prompt next merge. Useful when the operator wants to think about
it.

### 5.2 Non-interactive mode (login-time, CI, no TTY)

When stdin is not a TTY, Chameleon obeys `--on-conflict=<strategy>`:

| Strategy | Behavior on conflict |
|---|---|
| `fail` (default) | Exit 2 with conflict report on stderr; nothing written |
| `keep` | Leave live files alone for conflicting keys (skip) |
| `prefer=<target>` | Take the named target's value |
| `prefer=neutral` | Take the previous neutral value (effectively reverts conflicting drift) |

`fail` is default precisely because login-time silent resolution is a
trust hazard. Operators who want unattended runs opt into a strategy
explicitly.

### 5.3 What is *not* a conflict

- A target's drift on a key the other target doesn't speak. Not a
  conflict — pass-through namespace handles it.
- Both targets drifting to the *same* value. Trivially merged.
- Drift in a target that has no codec for that domain in V0. The
  pass-through namespace catches it; warned but not flagged.

## 5.4 Strict typing rule (applies to every section that follows)

**Everything is typed. No strings.** Every API surface in Chameleon —
codec inputs and outputs, conflict records, drift records, target and
domain identifiers, on-conflict strategies, file ownership flags — is
expressed via Pydantic models, `enum.Enum` subclasses, or `Literal[...]`
types. No `dict[str, Any]` floats free in the codebase except at the
single boundary where it must: the **pass-through bag** (§7.1) which
explicitly stores `dict[FieldName, JsonValue]` with `JsonValue` itself a
recursive `pydantic.Json` type. Configuration that arrives from the wire
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

# the implicit base profile — these eight domains and the targets
# escape hatch are what the operator edits day-to-day
identity: { ... }
directives: { ... }
capabilities: { ... }
authorization: { ... }
environment: { ... }
lifecycle: { ... }
interface: { ... }
governance: { ... }

# named overlay profiles — re-specify any subset of any domain
profiles:
  deep-review:
    identity: { model: gpt-5-pro, reasoning_effort: high }
    authorization: { default_mode: read-only }

# pass-through escape hatch for target-unique features
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

### 7.1 Pass-through namespace (`targets.<name>`)

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
done by field introspection, not string lookup:

```python
class Codec[NeutralDomainModel: BaseModel, TargetSectionModel: BaseModel](Protocol):
    target: ClassVar[TargetId]                # registry-validated newtype, not str
    domain: ClassVar[Domains]                 # closed enum, not str
    target_section: ClassVar[type[TargetSectionModel]]
    # TargetSectionModel is a Pydantic submodel whose fields are exactly
    # the keys this codec claims on the target side. The disassembler
    # routes target input by walking the target's full generated model
    # (§8.4) and matching field names — not strings — against each
    # codec's target_section.model_fields. Anything not matched flows
    # to pass-through (§7.1).

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

Codecs do **not** decide pass-through — that's the disassembler's job
(below). A codec only sees a typed submodel whose fields the
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
   (§8.5) using the appropriate format codec (yaml/json/toml from `io/`).
2. Iterates the codecs registered for this target. For each codec,
   constructs an instance of `codec.target_section` by copying field
   values from `FullTargetModel` whose field names appear in
   `codec.target_section.model_fields`.
3. Any field of `FullTargetModel` that no codec claims, plus any
   value held in `FullTargetModel`'s `additional_properties` overflow
   (relevant for Claude — see §8.4), goes into `PassThroughBag`,
   which is itself a typed structure: `dict[FieldName, JsonValue]`.

The assembler does the inverse: takes the per-domain typed submodels
plus the pass-through bag and rebuilds a `FullTargetModel`, then
serializes it via the format codec to file bytes.

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
│   │   ├── _generated.py       # GENERATED: full Pydantic ClaudeSettings (§8.5); checked-in artefact
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
│       ├── _generated.py       # GENERATED: full Pydantic CodexConfig (§8.5)
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
│   ├── git.py                  # GitPython or subprocess wrapper around per-target repos
│   └── paths.py                # XDG resolution
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
accept `--dry-run`, `--verbose`/`--quiet`, `--neutral <path>`, and
`--scope=user|project|both` (project deferred for V0).

| Command | Purpose |
|---|---|
| `chameleon init` | First-time bootstrap. If no neutral file and no state-repos exist, sample current target configs, build a starter neutral file, prompt on conflicts, write neutral + commit each state-repo at "initial" |
| `chameleon merge` | The core round-trip. `--on-conflict=fail|keep|prefer=<target>|prefer=neutral` controls non-interactive behavior |
| `chameleon status` | Per-target drift summary. Exit 0 if clean, exit 1 if drift, exit 2 if conflict pending |
| `chameleon diff <target>` | Detailed drift listing (domain → key → live vs. HEAD) |
| `chameleon log <target> [--json]` | Pretty timeline from the target's state-repo, parsing the structured commit trailers |
| `chameleon adopt <target>` | Merge resolving every conflict in favor of `<target>` |
| `chameleon discard <target>` | Overwrite live files with state-repo HEAD; throw away drift |
| `chameleon validate` | Run schema validation against neutral file; exit 0/1 |
| `chameleon doctor` | Environment health: tool versions, paths exist, state-repos consistent, neutral file parseable, target files writable |
| `chameleon targets list` | List registered targets (built-in + entry-point plugins) |
| `chameleon profile use <name>` | Apply a named overlay from `profiles.<name>` (writes targets as if base + overlay; never modifies neutral). Reverses on next `merge` |

### 9.1 Login-time invocation

Chameleon does not ship installers. Documented recipes:

- **macOS / launchd:** plist sample for `~/Library/LaunchAgents/io.waugh.chameleon.plist` running `chameleon merge --on-conflict=fail`. Fails closed on conflict so the operator sees it on next interactive shell.
- **Linux / systemd user:** `~/.config/systemd/user/chameleon.service` + `chameleon.timer` (or just OnLogin via `pam_systemd`).
- **Shell rc:** simple `chameleon merge --on-conflict=keep --quiet || true` snippet for `.zlogin` users, with the explicit acknowledgment that `keep` is permissive.

These recipes live in `docs/login/` and are advisory; nothing about
the tool requires login-time use.

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

Live files at e.g. `~/.claude.json` contain a leading dot, which
would shadow git internals if mirrored verbatim. The state-repo uses
the convention: leading-dot paths are stored with a `~` prefix
(`~.claude.json`). The assembler's path table maps these consistently
on every read/write.

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
  with all conflict and drift records as typed Pydantic models.
- Per-target git state via subprocess `git` (no `GitPython` dependency
  at this stage; subprocess is enough and avoids a heavy dep).
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
  → assert state-repo commit graph and live-file content.
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
