"""Produce sanitized exemplar fixtures from live ~/.claude and ~/.codex configs.

Run from a developer's actual machine to refresh the fixtures:

    uv run python tests/fixtures/exemplar/sanitize.py [--source $HOME]

The script reads from the (real) source HOME, replaces every PII-bearing
substring with a stable placeholder, and writes to
``tests/fixtures/exemplar/home/_claude``, ``home/_codex``, and
``home/_claude.json``. Files containing OAuth tokens, conversation
transcripts, history dbs, etc. are NEVER copied — only configuration-
shaped files are processed.

The output is a realistic exemplar of how Claude Code and Codex CLI
actually configure themselves in the field: ~40 enabled plugins, custom
status lines, project-trust state, marketplace pins. It is the right
input for analyzing chameleon's parity gap honestly, instead of the
toy ``test_v0_acceptance.py`` neutral.yaml.

Sanitization rules
------------------
- ``/Users/<real>/`` → ``/Users/exampleuser/``
- GitHub usernames / org names ``fnordpig``, ``Archivium-Properties``,
  ``rob-archivium`` → ``example-user``, ``example-org``, etc.
- Custom marketplace names that identify the operator (``archivium-*``,
  ``my-claude-plugins``) → ``example-org-marketplace`` /
  ``example-user-plugins`` (preserves shape; loses identity)
- ``cozempic-*`` (a project name) → ``example-project-*``
- Git commit SHAs → 40-char placeholders ``0000…``
- Timestamps → fixed ``2026-01-01T00:00:00Z``
- ``userID``, ``anonymousId`` → constant 64/50-char ``"x"`` strings
- ``oauthAccount``, ``customApiKeyResponses``, ``cachedGrowthBookFeatures``
  → emptied (we keep the keys to preserve assembler-disassembler shape
  but strip any user-identifying values)
- ``projects`` (in ~/.claude.json): paths sanitized; values kept as
  empty dicts (we care about the *shape*, not the per-project history)
- ``installation_id``, sqlite/jsonl/auth files: NOT copied at all.

The fixtures are committed to git. Do not commit raw output of this
script without reviewing the diff.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import tomlkit

FIXTURE_ROOT = Path(__file__).parent / "home"

# --- Replacement tables -----------------------------------------------------

# Order matters: process longer patterns first so ``rob-archivium`` is
# replaced before ``rob`` if we ever add the latter.
SUBSTRING_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("rob-archivium", "example-org-user"),
    ("Archivium-Properties", "example-org"),
    ("archivium-marketplace", "example-org-marketplace"),
    ("archivium-admin", "example-org-admin"),
    ("archivium-aws", "example-org-aws"),
    ("archivium-ci", "example-org-ci"),
    ("archivium-doc-tools", "example-org-doc-tools"),
    ("archivium-federation", "example-org-federation"),
    ("archivium-plugin-creator", "example-org-plugin-creator"),
    ("archivium-workflow", "example-org-workflow"),
    ("archiuvium-plugin-creator", "example-org-plugin-creator-typo"),
    ("archivium", "example-org"),
    ("archiuvium", "example-org-typo"),
    ("my-claude-plugins", "example-user-plugins"),
    ("github-archivium", "github-example-org"),
    ("fnordpig", "example-user"),
    ("rwaugh", "exampleuser"),
    ("cozempic", "example-project"),
)

SHA_PATTERN = re.compile(r"\b[0-9a-f]{40}\b")
ISO_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
PLACEHOLDER_SHA = "0" * 40
PLACEHOLDER_TIMESTAMP = "2026-01-01T00:00:00Z"

# Secret-token patterns. Order: each entry is (compiled regex,
# replacement). These run BEFORE substring replacements so that any
# secret accidentally ending in a sanitized substring still gets
# redacted as a secret. Patterns intentionally err on the side of
# over-redaction — false positives turn into ``REDACTED_*`` markers in
# the fixture, which is fine; false negatives leak credentials.
SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # GitHub personal-access tokens (classic and fine-grained), OAuth
    # tokens, server-to-server tokens, and refresh tokens.
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "REDACTED_GITHUB_PAT"),
    (re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"), "REDACTED_GITHUB_OAUTH"),
    (re.compile(r"\bghu_[A-Za-z0-9]{20,}\b"), "REDACTED_GITHUB_USER"),
    (re.compile(r"\bghs_[A-Za-z0-9]{20,}\b"), "REDACTED_GITHUB_SERVER"),
    (re.compile(r"\bghr_[A-Za-z0-9]{20,}\b"), "REDACTED_GITHUB_REFRESH"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "REDACTED_GITHUB_FINEGRAINED"),
    # Anthropic / OpenAI / generic provider keys.
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "REDACTED_ANTHROPIC_KEY"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "REDACTED_OPENAI_KEY"),
    # AWS access key IDs and secret keys (AKIA / ASIA prefix).
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "REDACTED_AWS_KEY_ID"),
    # Slack tokens.
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "REDACTED_SLACK_TOKEN"),
    # JWTs (header.payload.signature, base64url segments).
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "REDACTED_JWT",
    ),
    # Bearer-prefixed values often appear in HTTP-header configs.
    (
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
        "Bearer REDACTED_BEARER",
    ),
    # Generic high-entropy looking secret values inside JSON-shaped
    # strings: ``"token": "..."``-shaped pairs where the value is 24+
    # chars of base64-ish content. This is the broad-net safety catch.
    (
        re.compile(
            r'(?P<key>"(?:[a-zA-Z_]+(?:_)?(?:token|secret|password|key|api_key))"\s*:\s*)'
            r'"(?P<val>[A-Za-z0-9._/+=-]{24,})"',
            flags=re.IGNORECASE,
        ),
        r'\g<key>"REDACTED_GENERIC_SECRET"',
    ),
)

# Keys in ~/.claude.json whose values should be wiped (set to empty
# of the appropriate shape) rather than just substring-replaced.
CLAUDE_JSON_WIPE_TO_EMPTY_DICT: frozenset[str] = frozenset(
    {
        "oauthAccount",
        "customApiKeyResponses",
        "cachedGrowthBookFeatures",
        "tipsHistory",
        "skillUsage",
        "githubRepoPaths",
        "clientDataCache",
        "cachedExperimentFeatures",
        "additionalModelOptionsCache",
        "additionalModelCostsCache",
        "metricsStatusCache",
        "overageCreditGrantCache",
        "passesEligibilityCache",
        "s1mAccessCache",
        "groveConfigCache",
        "feedbackSurveyState",
        "hasShownOpus46Notice",
        "seenNotifications",
    }
)
CLAUDE_JSON_WIPE_TO_EMPTY_LIST: frozenset[str] = frozenset(
    {
        "claudeAiMcpEverConnected",
    }
)
# Identifying string fields that should be replaced with stable placeholders.
CLAUDE_JSON_FAKE_STRING_FIELDS: dict[str, str] = {
    "userID": "x" * 64,
    "anonymousId": "x" * 50,
    "installMethod": "manual",
    "deepLinkTerminal": "iterm2",
    "voiceLangHintLastLanguage": "en",
}


def sanitize_string(s: str) -> str:
    out = s
    # Redact secrets FIRST so a partial substring replacement can't
    # corrupt a token's prefix (which would defeat detection).
    for pattern, replacement in SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    for needle, replacement in SUBSTRING_REPLACEMENTS:
        out = out.replace(needle, replacement)
    out = SHA_PATTERN.sub(PLACEHOLDER_SHA, out)
    out = ISO_TIMESTAMP_PATTERN.sub(PLACEHOLDER_TIMESTAMP, out)
    return out


def sanitize_json_value(value: object, *, key_path: tuple[str, ...] = ()) -> object:
    """Recursively sanitize a JSON-shaped value tree.

    `key_path` accumulates parent keys to support context-aware rules
    (e.g. ``projects`` in ~/.claude.json has path-shaped keys whose
    values we want to wipe wholesale).
    """
    if isinstance(value, str):
        return sanitize_string(value)
    if isinstance(value, list):
        return [sanitize_json_value(v, key_path=key_path) for v in value]
    if isinstance(value, dict):
        # `projects` in ~/.claude.json: keys are filesystem paths; values
        # contain per-project history. Sanitize keys, replace values with {}.
        if key_path == ("projects",):
            sanitized_keys: dict[str, object] = {}
            for raw_k in value:
                sk = sanitize_string(raw_k) if isinstance(raw_k, str) else str(raw_k)
                sanitized_keys[sk] = {}
            return sanitized_keys
        out: dict[str, object] = {}
        for raw_k, v in value.items():
            sk: str = sanitize_string(raw_k) if isinstance(raw_k, str) else str(raw_k)
            if not key_path and sk in CLAUDE_JSON_WIPE_TO_EMPTY_DICT:
                out[sk] = {}
                continue
            if not key_path and sk in CLAUDE_JSON_WIPE_TO_EMPTY_LIST:
                out[sk] = []
                continue
            if not key_path and sk in CLAUDE_JSON_FAKE_STRING_FIELDS:
                out[sk] = CLAUDE_JSON_FAKE_STRING_FIELDS[sk]
                continue
            out[sk] = sanitize_json_value(v, key_path=(*key_path, sk))
        return out
    return value


def sanitize_toml_text(raw: str) -> str:
    """Substring-replace then verify the result still parses.

    tomlkit's data model treats table-header components as opaque keys
    that don't pass through a value-walker, so subkeys like
    ``archivium-marketplace`` in ``[marketplaces.archivium-marketplace]``
    survive a tree walk. Substring replacement on the raw text catches
    both keys and values uniformly. We re-parse afterward to ensure the
    sanitization didn't break the TOML grammar (e.g. by inserting
    whitespace into a bare-key context — none of our replacements do
    that, but the assertion is cheap insurance).
    """
    sanitized = sanitize_string(raw)
    sanitized = SHA_PATTERN.sub(PLACEHOLDER_SHA, sanitized)
    sanitized = ISO_TIMESTAMP_PATTERN.sub(PLACEHOLDER_TIMESTAMP, sanitized)
    tomlkit.parse(sanitized)  # raises on grammar break
    return sanitized


def copy_text(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(sanitize_string(src.read_text(encoding="utf-8")), encoding="utf-8")


def copy_json(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(src.read_text(encoding="utf-8"))
    sanitized = sanitize_json_value(raw)
    dest.write_text(json.dumps(sanitized, indent=2) + "\n", encoding="utf-8")


def copy_toml(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = src.read_text(encoding="utf-8")
    dest.write_text(sanitize_toml_text(raw), encoding="utf-8")


# --- File set --------------------------------------------------------------

# (source path under HOME, dest path under FIXTURE_ROOT, copy fn)
_FILE_PLAN: tuple[tuple[str, str, str], ...] = (
    # Codex
    (".codex/config.toml", "_codex/config.toml", "toml"),
    # Claude per-user dir
    (".claude/settings.json", "_claude/settings.json", "json"),
    (".claude/settings.local.json", "_claude/settings.local.json", "json"),
    (".claude/mcp.json", "_claude/mcp.json", "json"),
    (".claude/CLAUDE.md", "_claude/CLAUDE.md", "text"),
    (".claude/policy-limits.json", "_claude/policy-limits.json", "json"),
    (".claude/mcp-needs-auth-cache.json", "_claude/mcp-needs-auth-cache.json", "json"),
    # ~/.claude.json (the big one)
    (".claude.json", "_claude.json", "json"),
)

# Files we DO NOT copy under any circumstances. Listed for transparency
# (so reviewers can confirm we never even open these in the sanitizer).
_FILE_BLOCKLIST: tuple[str, ...] = (
    ".codex/auth.json",
    ".codex/installation_id",
    ".codex/history.jsonl",
    ".codex/session_index.jsonl",
    ".codex/logs_2.sqlite",
    ".codex/logs_2.sqlite-shm",
    ".codex/logs_2.sqlite-wal",
    ".codex/state_5.sqlite",
    ".codex/state_5.sqlite-shm",
    ".codex/state_5.sqlite-wal",
    ".codex/version.json",  # version-specific telemetry
    ".codex/models_cache.json",  # may contain model availability tied to account
    ".codex/model-catalog-600k.json",  # custom; account-tied
    ".claude/history.jsonl",
    ".claude/cozempic-sessions.json",
    ".claude/stats-cache.json",
    ".claude/.DS_Store",
    ".claude/settings.json.bak",
    ".claude/RTK.md",  # personal note; not config
)


def main() -> None:  # noqa: PLR0912 — operator-only utility; readability > complexity bound
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=str(Path.home()),
        help="Source HOME from which to read live configs (default: $HOME).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Wipe the existing fixture tree before writing.",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if args.clean and FIXTURE_ROOT.exists():
        shutil.rmtree(FIXTURE_ROOT)

    plan = []
    for src_rel, dest_rel, kind in _FILE_PLAN:
        src = source / src_rel
        if not src.exists():
            print(f"SKIP (missing): {src_rel}")
            continue
        dest = FIXTURE_ROOT / dest_rel
        plan.append((src, dest, kind, src_rel))

    for src, dest, kind, src_rel in plan:
        if kind == "toml":
            copy_toml(src, dest)
        elif kind == "json":
            copy_json(src, dest)
        elif kind == "text":
            copy_text(src, dest)
        else:
            msg = f"unknown copy kind: {kind}"
            raise ValueError(msg)
        print(f"WROTE: {src_rel}  →  {dest.relative_to(FIXTURE_ROOT.parent)}")

    print()
    print("Blocklist (never copied):")
    for blocked in _FILE_BLOCKLIST:
        print(f"  {blocked}")

    # --- Post-write paranoia scan ---------------------------------------
    print()
    print("Post-write secret scan...")
    leaked: list[tuple[Path, int, str]] = []
    for fixture in FIXTURE_ROOT.rglob("*"):
        if not fixture.is_file():
            continue
        try:
            text = fixture.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, _ in SECRET_PATTERNS:
            for m in pattern.finditer(text):
                # Find line number for clearer reporting
                line_no = text.count("\n", 0, m.start()) + 1
                leaked.append((fixture, line_no, m.group(0)))
    if leaked:
        print("  ✘ SECRET PATTERNS DETECTED IN OUTPUT — refusing to claim clean:")
        for path, ln, match in leaked:
            print(f"    {path.relative_to(FIXTURE_ROOT.parent)}:{ln}: {match[:30]}...")
        msg = "secret patterns survived sanitization; investigate and tighten rules"
        raise SystemExit(msg)
    print("  ✓ No known secret patterns in output")


if __name__ == "__main__":
    main()
