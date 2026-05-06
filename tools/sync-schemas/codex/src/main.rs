//! Dump the schemars-derived JSON Schema for codex's ConfigToml.
//!
//! This binary is built and run only by `tools/sync-schemas/sync.py codex`.
//! Its output (stdout) is captured into `tools/sync-schemas/upstream/codex.schema.json`,
//! which is then fed to `datamodel-code-generator` to produce
//! `src/chameleon/codecs/codex/_generated.py`.
//!
//! The codex-rs commit pinned in `pins.toml` is the source of truth.

use codex_config::ConfigToml;
use schemars::schema_for;

fn main() {
    let schema = schema_for!(ConfigToml);
    let stdout = std::io::stdout();
    let mut handle = stdout.lock();
    serde_json::to_writer_pretty(&mut handle, &schema).expect("failed to write schema as JSON");
    println!();
}
