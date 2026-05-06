"""Sync vendored upstream schemas and regenerate _generated.py.

Usage:
    uv run --group schema-sync python tools/sync-schemas/sync.py claude
    uv run --group schema-sync python tools/sync-schemas/sync.py codex
    uv run --group schema-sync python tools/sync-schemas/sync.py all

The orchestrator reads pins.toml, fetches the upstream JSON Schema
(downloading for claude, building+running the Rust binary for codex),
writes it to upstream/, then runs datamodel-code-generator to produce
src/chameleon/codecs/<target>/_generated.py.

Bumping pins is a deliberate operator action: edit pins.toml, run sync,
review the diff in upstream/ and _generated.py, address any codec breakage,
commit.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PINS_PATH = REPO_ROOT / "tools" / "sync-schemas" / "pins.toml"
UPSTREAM_DIR = REPO_ROOT / "tools" / "sync-schemas" / "upstream"
CODECS_DIR = REPO_ROOT / "src" / "chameleon" / "codecs"


def _load_pins() -> dict[str, object]:
    with PINS_PATH.open("rb") as fh:
        return tomllib.load(fh)


def sync_claude(pins: dict[str, object]) -> int:
    section = pins["claude"]
    assert isinstance(section, dict)
    git_sha = section["git_sha"]
    if git_sha == "REPLACE_WITH_ACTUAL_SHA_AT_FIRST_RUN":
        sys.stderr.write(
            "claude.git_sha in pins.toml is the placeholder; resolve a real SHA "
            "from https://github.com/SchemaStore/schemastore/commits/master and update.\n"
        )
        return 2

    source_template = section["source"]
    assert isinstance(source_template, str)
    url = source_template.format(git_sha=git_sha)
    vendored_at = REPO_ROOT / section["vendored_at"]

    sys.stderr.write(f"[claude] fetching {url}\n")
    with urllib.request.urlopen(url) as resp:  # noqa: S310 — pinned schemastore URL
        body = resp.read()
    vendored_at.parent.mkdir(parents=True, exist_ok=True)
    vendored_at.write_bytes(body)
    sys.stderr.write(f"[claude] wrote {vendored_at} ({len(body)} bytes)\n")

    output = CODECS_DIR / "claude" / "_generated.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr.write(f"[claude] generating {output}\n")
    rc = subprocess.run(
        [
            "datamodel-codegen",
            "--input",
            str(vendored_at),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(output),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--use-standard-collections",
            "--use-union-operator",
            "--use-schema-description",
            "--field-constraints",
            "--snake-case-field",
            "--allow-extra-fields",
            "--use-default",
        ],
        check=False,
    ).returncode
    if rc != 0:
        return rc

    sys.stderr.write(f"[claude] OK; commit upstream/{vendored_at.name} and {output.name}\n")
    return 0


VENDOR_DIR: Path = REPO_ROOT / "vendor" / "codex-rs"


def _ensure_codex_rs_clone(git_url: str, git_sha: str) -> Path:
    """Ensure vendor/codex-rs/ is a checkout at the pinned SHA.

    Self-contained: clones if missing, fetches+checkouts on SHA mismatch.
    No operator-managed CODEX_RS_PATH required. The vendor dir is gitignored.
    """
    git = shutil.which("git")
    if git is None:
        msg = "codex sync requires git on PATH"
        raise RuntimeError(msg)

    if not (VENDOR_DIR / ".git").exists():
        VENDOR_DIR.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr.write(f"[codex] cloning {git_url} into {VENDOR_DIR}\n")
        # blob:none filter keeps the checkout small; we only need source code
        subprocess.run(
            [git, "clone", "--filter=blob:none", git_url, str(VENDOR_DIR)],
            check=True,
        )

    current = subprocess.check_output(
        [git, "-C", str(VENDOR_DIR), "rev-parse", "HEAD"], text=True
    ).strip()
    if current != git_sha:
        sys.stderr.write(f"[codex] fetching + checking out {git_sha}\n")
        subprocess.run([git, "-C", str(VENDOR_DIR), "fetch", "origin", git_sha], check=True)
        subprocess.run([git, "-C", str(VENDOR_DIR), "checkout", "--detach", git_sha], check=True)

    return VENDOR_DIR


def sync_codex(pins: dict[str, object]) -> int:
    """Generate codex/_generated.py from an auto-managed codex-rs checkout.

    Self-contained pipeline: clones codex-rs into vendor/codex-rs/ at the
    pinned SHA (gitignored), drops a one-shot
    `dump-schema-chameleon.rs` into its config/examples/ directory, runs
    `cargo run --release --example dump-schema-chameleon` inside that
    workspace (so codex-rs's Cargo.lock is in scope), captures stdout,
    then deletes the example. The standalone Rust binary at
    tools/sync-schemas/codex/ remains for documentation but is bypassed
    in favour of this lock-file-aware path.
    """
    section = pins["codex"]
    assert isinstance(section, dict)
    git_sha = section["git_sha"]
    if git_sha == "REPLACE_WITH_ACTUAL_SHA_AT_FIRST_RUN":
        sys.stderr.write(
            "codex.git_sha in pins.toml is the placeholder; resolve a real SHA "
            "from https://github.com/openai/codex/commits/main and update.\n"
        )
        return 2

    if shutil.which("cargo") is None:
        sys.stderr.write("codex sync requires cargo + Rust toolchain; install rustup and re-run.\n")
        return 3

    git_url = section.get("git_url", "https://github.com/openai/codex.git")
    assert isinstance(git_url, str)
    assert isinstance(git_sha, str)

    codex_rs = _ensure_codex_rs_clone(git_url, git_sha)
    config_dir = codex_rs / "codex-rs" / "config"
    if not (config_dir / "Cargo.toml").exists():
        sys.stderr.write(
            f"vendored codex-rs at {codex_rs} does not contain codex-rs/config/Cargo.toml; "
            "the checkout may be incomplete or the layout changed.\n"
        )
        return 5
    examples_dir = config_dir / "examples"
    example_file = examples_dir / "dump-schema-chameleon.rs"

    sys.stderr.write(f"[codex] writing temp example to {example_file}\n")
    examples_dir.mkdir(parents=True, exist_ok=True)
    example_file.write_text(
        "//! Auto-generated by chameleon's tools/sync-schemas/sync.py — do not commit.\n"
        "use codex_config::config_toml::ConfigToml;\n"
        "use schemars::schema_for;\n"
        "fn main() {\n"
        "    let schema = schema_for!(ConfigToml);\n"
        '    serde_json::to_writer_pretty(std::io::stdout(), &schema).expect("write");\n'
        "    println!();\n"
        "}\n",
        encoding="utf-8",
    )

    try:
        sys.stderr.write(f"[codex] cargo run --example dump-schema-chameleon in {config_dir}\n")
        result = subprocess.run(
            ["cargo", "run", "--release", "--example", "dump-schema-chameleon"],
            cwd=config_dir,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
            return result.returncode
        schema_bytes = result.stdout
    finally:
        example_file.unlink(missing_ok=True)
        sys.stderr.write(f"[codex] removed temp example {example_file}\n")

    vendored_at = REPO_ROOT / section["vendored_at"]
    vendored_at.parent.mkdir(parents=True, exist_ok=True)
    vendored_at.write_bytes(schema_bytes)
    sys.stderr.write(f"[codex] wrote {vendored_at} ({len(schema_bytes)} bytes)\n")

    output = CODECS_DIR / "codex" / "_generated.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    rc = subprocess.run(
        [
            "datamodel-codegen",
            "--input",
            str(vendored_at),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(output),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--use-standard-collections",
            "--use-union-operator",
            "--use-schema-description",
            "--field-constraints",
            "--snake-case-field",
            "--allow-extra-fields",
            "--use-default",
        ],
        check=False,
    ).returncode
    if rc != 0:
        return rc

    sys.stderr.write(f"[codex] OK; commit upstream/{vendored_at.name} and {output.name}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync upstream schemas + regenerate _generated.py")
    parser.add_argument("target", choices=["claude", "codex", "all"])
    args = parser.parse_args(argv)

    pins = _load_pins()
    if args.target in ("claude", "all"):
        rc = sync_claude(pins)
        if rc != 0:
            return rc
    if args.target in ("codex", "all"):
        rc = sync_codex(pins)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
