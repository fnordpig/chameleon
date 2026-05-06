# Chameleon

> One neutral configuration; many AI coding agents. Bidirectional.

Chameleon transpiles a single neutral YAML configuration into Claude Code's
`settings.json` and OpenAI Codex CLI's `config.toml` (and back again),
detecting drift when an agent edits its own configuration at runtime,
prompting the operator to resolve conflicts, and re-deriving every other
agent's view so the operator's intent stays consistent across tools.

## Status

Early V0. Implements `identity` (model + reasoning effort + provider + auth
method), `directives.commit_attribution` + `directives.system_prompt_file`,
`capabilities.mcp_servers`, and `environment.variables` end-to-end across
both targets. The remaining four schema domains (`authorization`,
`lifecycle`, `interface`, `governance`) have full typed Pydantic models
already — codecs land in follow-on specs.

## Why YAML?

The neutral form is YAML so that operator-authored comments and anchors
survive a round-trip; both target formats (Claude's JSON and Codex's TOML)
are also written via formatters that preserve key order and (for TOML)
comments, so `git diff` on live target files stays informative rather than
noisy.

## Running

Every command goes through `uv` — this is a uv-wrapped project.

```sh
uv sync
uv run chameleon --help
uv run chameleon init
uv run chameleon merge
uv run chameleon status
```

Development:

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
uv run ty check
```

Refreshing the upstream-canonized schemas (rare; see `docs/sync-schemas.md`):

```sh
uv run --group schema-sync python tools/sync-schemas/sync.py claude
uv run --group schema-sync python tools/sync-schemas/sync.py codex   # needs cargo
```

## License

MIT — see `LICENSE`.
