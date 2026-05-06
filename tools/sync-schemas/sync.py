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


def sync_codex(pins: dict[str, object]) -> int:
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

    rust_dir = REPO_ROOT / "tools" / "sync-schemas" / "codex"
    sys.stderr.write(f"[codex] cargo build --release in {rust_dir}\n")
    rc = subprocess.run(
        ["cargo", "build", "--release", "--manifest-path", str(rust_dir / "Cargo.toml")],
        check=False,
    ).returncode
    if rc != 0:
        return rc

    binary = rust_dir / "target" / "release" / "codex-schema-dump"
    sys.stderr.write(f"[codex] running {binary}\n")
    schema_bytes = subprocess.check_output([str(binary)])

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
