# Refreshing vendored upstream schemas

Chameleon grounds its target codecs in JSON Schema produced by each
target's canonical authority (§8.4 of the design spec):

- **Claude**: schemastore.org's `claude-code-settings.json`
  (Draft-07 JSON Schema, vendored at
  `tools/sync-schemas/upstream/claude.schema.json`).
- **Codex**: derived from `codex-rs/config/src/config_toml.rs`
  (`ConfigToml` struct deriving `schemars::JsonSchema`) by a tiny
  Rust binary under `tools/sync-schemas/codex/`.

Both grounds are vendored — committed to git. Refreshing them is an
explicit operator action, never automatic.

## Bumping the Claude pin

1. Find the desired schemastore commit SHA (e.g. the latest on master).
2. Edit `tools/sync-schemas/pins.toml`: replace `claude.git_sha`.
3. `uv run --group schema-sync python tools/sync-schemas/sync.py claude`.
4. Review the diff in `tools/sync-schemas/upstream/claude.schema.json`
   and `src/chameleon/codecs/claude/_generated.py`.
5. Run `uv run pytest -m schema_drift -v`. If a codec's claimed paths
   point at fields that no longer exist, fix the codec.
6. Commit pins.toml + schema + generated + any codec fixes.

## Bumping the Codex pin

1. Find the desired Codex commit SHA on
   <https://github.com/openai/codex/commits/main>.
2. Edit `tools/sync-schemas/pins.toml`: replace `codex.git_sha`.
3. `uv run --group schema-sync python tools/sync-schemas/sync.py codex`.
   Requires `cargo` + Rust toolchain.
4. Same review/test/commit flow as above.

## Why pin

If we re-fetched on every sync, codec stability would silently depend
on whatever was on master at build time. The pin gives us a fixed
target the test suite is calibrated against, and bumping the pin
becomes a reviewable diff that surfaces breakage at PR time rather
than mysteriously at runtime.
