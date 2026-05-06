# Exemplar fixture: a sanitized real-world Claude + Codex setup

This directory holds a sanitized snapshot of a real operator's
`~/.claude/`, `~/.codex/`, and `~/.claude.json` files. It exists so
that chameleon's parity analysis runs against a representative live
case — ~40 enabled plugins, 9 marketplaces, custom status lines, hooks,
project-trust state — instead of the toy two-key neutral.yaml in
`tests/integration/test_v0_acceptance.py`.

## Layout

The fixture tree uses **visible** directory names (`_claude` /
`_codex`) so they aren't hidden in finders, file browsers, or git
diffs. Tests that need actual `~/.claude/` semantics copy the fixture
into a tmpdir under proper dotfile names at runtime; chameleon never
reads from the fixture tree directly.

```
tests/fixtures/exemplar/
├── README.md *11── this file
├── sanitize.py *11── the sanitizer (operator runs against live HOME)
└── home/
    ├── _claude/ *13── mirrors ~/.claude/
    │   ├── settings.json
    │   ├── settings.local.json
    │   ├── mcp.json
    │   ├── CLAUDE.md
    │   ├── policy-limits.json
    │   └── mcp-needs-auth-cache.json
    ├── _codex/ *14── mirrors ~/.codex/
    │   └── config.toml
    └── _claude.json *11── mirrors ~/.claude.json (the big one)
```

## What's NOT here, and why

`sanitize.py` has an explicit blocklist for files that are either:

- **Credentials** (`.codex/auth.json`)
- **Conversation transcripts / history** (`history.jsonl`,
  `cozempic-sessions.json`, `stats-cache.json`)
- **Account-tied / installation-tied state** (`installation_id`,
  `models_cache.json`, `model-catalog-*.json`, the sqlite databases)
- **Personal notes** (`RTK.md`)
- **OS noise** (`.DS_Store`)

The sanitizer never opens these files, even to count bytes. If you
add a file to `_FILE_PLAN`, also reason explicitly about whether its
shape leaks identifying info that the substring-replacement table
won't catch.

## Sanitization rules

The sanitizer runs three layers:

1. **Secret patterns** (regex, applied first). Catches `ghp_`-style
   GitHub PATs, `sk-ant-`/`sk-` provider keys, AWS access-key IDs
   (`AKIA`/`ASIA`), Slack tokens, JWTs, `Bearer ...` headers, and a
   broad `"token"|"secret"|"password"|"key": "high-entropy-string"`
   catch-all. Matches are replaced with `REDACTED_<KIND>` markers.
2. **Substring replacements** (operator-specific identifiers).
   `rwaugh` → `exampleuser`, `fnordpig` → `example-user`, custom
   marketplace and project names → `example-org-*` / `example-project-*`.
3. **Structural wipes** (JSON-aware). `oauthAccount`,
   `customApiKeyResponses`, `cachedGrowthBookFeatures`, etc. are
   emptied to `{}`; `userID`, `anonymousId`, `installMethod` etc. are
   replaced with stable placeholders. The `projects` dict in
   `~/.claude.json` keeps its keys (sanitized) but discards values
   wholesale.

A **post-write paranoia scan** then re-runs every secret pattern
against the produced files and aborts if anything matches. This is
how the GITHUB_TOKEN leak from the first sanitizer run was caught.

## Refresh procedure

```sh
uv run python tests/fixtures/exemplar/sanitize.py --clean
```

Then `git diff tests/fixtures/exemplar/home/` and review every change
manually before committing. Things to look for:

- Strings the regex+substring rules didn't recognize.
- New JSON keys that should be in `CLAUDE_JSON_WIPE_TO_EMPTY_*`.
- Long high-entropy strings the broad-net catch-all missed (extend
  `SECRET_PATTERNS`).

## Why both `_claude` and `_claude.json`

The Claude Code project splits state across two locations:

- `~/.claude/` is a per-user dotfile **directory** holding
  `settings.json` (the schema-modeled config), plugin scratch space,
  hooks, etc.
- `~/.claude.json` is a single **file** at the home root that holds
  per-user account state, OAuth tokens, MCP servers, project trust
  state, growth-book flags, and ~70 other top-level keys.

Chameleon's Claude assembler only **partially owns** `~/.claude.json`
(just `mcpServers`); it owns `~/.claude/settings.json` fully. Both
files appear in the fixture tree because both are inputs to a real
disassemble.
