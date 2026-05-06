"""Wave-6 golden round-trip on the sanitized exemplar.

The smoke (`tests/integration/test_exemplar_smoke.py`) verifies
*behavioural* properties — exit codes, key preservation, conflict
detection. Wave-6 is stronger: after `chameleon init` +
`chameleon merge --on-conflict=keep` against the exemplar, the live
target files must round-trip *modulo a small, documented set of
intentional transforms*.

Documented transforms (the "modulo X" allow-list):

* **P1-D — legacy attribution alias consolidation.** The three legacy
  bools in Claude `settings.json` (``includeCoAuthoredBy``,
  ``coauthoredBy``, ``gitAttribution``) collapse to a single
  ``attribution.commit`` entry.
* **B2 — sorted-by-key dict ordering.** Plugin and marketplace dicts
  are emitted alphabetically (the fix that gave us byte-stable
  idempotency in Wave-5).
* **B4 — non-ASCII preservation.** Non-ASCII codepoints written via
  partial-owned paths now route through `dump_json(ensure_ascii=False)`,
  so a literal ``—`` may replace ``\\u2014`` (or vice-versa); the
  *parsed* value must be identical.
* **P1-A — capabilities reconciliation.** Plugin enable-state and
  marketplaces are unioned across targets, so Codex `[plugins.*]`
  may grow keys that were originally only in Claude
  ``enabledPlugins`` (and vice-versa). Same for marketplaces.
* **Cosmetic empties.** ``env: {}`` (Claude) and
  ``[shell_environment_policy.set]`` (Codex) appear when neutral
  has no environment configured.

Anything outside that allow-list is a Wave-7 finding. Two such
findings already surface here (F1 and F2 below); both are pinned as
``xfail(strict=True)`` so the failure mode is visible in CI but
doesn't block the green build, and the moment they are fixed pytest
will flip them to passing automatically.

Wave-7 findings (xfail-pinned):

* **F1 — `statusLine.type` dropped on Claude `settings.json`.** The
  exemplar ships ``"statusLine": {"type": "command", "command": ...}``.
  After merge, only ``command`` survives; ``type`` is normalised away
  because ``_ClaudeStatusLine.type`` carries a default of
  ``"command"`` and is excluded as a default during section
  serialisation. Either the codec must explicitly emit ``type`` (it
  is the documented schemastore wire shape) or the assembler must
  preserve unclaimed sub-keys at the same hierarchical depth that
  B1 fixed for top-level extras.
* **F2 — Codex `[marketplaces.<name>]` sub-keys dropped.** The
  exemplar's marketplace tables carry ``last_updated`` and
  ``last_revision`` (Codex's marketplace cache state). The
  marketplace codec models only ``source`` and ``source_type``; the
  per-marketplace dict-of-tables shape doesn't go through B1's
  section-level ``extra="allow"`` harvester. After merge those
  fields are gone. This is the same shape as B1 but one level deeper
  — B1 fixed top-level table extras, F2 is the same fix applied to
  dict-of-tables values.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import tomlkit

from chameleon.io.yaml import load_yaml

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURE_HOME = REPO / "tests" / "fixtures" / "exemplar" / "home"

# Top-level keys in Claude settings.json that the legacy-attribution
# consolidation (P1-D) is allowed to remove from the original.
_LEGACY_ATTRIBUTION_ALIASES: frozenset[str] = frozenset(
    {"includeCoAuthoredBy", "coauthoredBy", "gitAttribution"}
)


@pytest.fixture
def exemplar_env() -> Iterator[dict[str, Path]]:
    """A tmpdir HOME mirroring the sanitized exemplar (smoke pattern)."""
    with tempfile.TemporaryDirectory() as td:
        sb = Path(td)
        home = sb / "home"
        home.mkdir()
        state = sb / "state"
        state.mkdir()
        config = sb / "config"
        config.mkdir()
        shutil.copytree(FIXTURE_HOME / "_claude", home / ".claude")
        shutil.copytree(FIXTURE_HOME / "_codex", home / ".codex")
        shutil.copy(FIXTURE_HOME / "_claude.json", home / ".claude.json")
        yield {"home": home, "state": state, "config": config}


def _run(args: list[str], env_paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HOME": str(env_paths["home"]),
        "XDG_STATE_HOME": str(env_paths["state"]),
        "XDG_CONFIG_HOME": str(env_paths["config"]),
    }
    return subprocess.run(
        ["uv", "run", "chameleon", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _init_and_merge(env_paths: dict[str, Path]) -> None:
    init = _run(["init"], env_paths)
    assert init.returncode == 0, f"init failed: {init.stderr[-500:]}"
    merge = _run(["merge", "--on-conflict=keep"], env_paths)
    assert merge.returncode == 0, f"merge failed: {merge.stderr[-500:]}"


def _toml_to_plain(value: Any) -> Any:
    """Recursively unwrap tomlkit container types into plain Python.

    tomlkit's ``Table`` / ``Array`` carry trivia (whitespace, comments)
    that aren't relevant to a structural compare. Round-tripping
    through plain dict/list preserves keys/values without that noise.
    """
    if isinstance(value, dict):
        return {k: _toml_to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_toml_to_plain(v) for v in value]
    return value


def _normalize_claude_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply the documented Wave-5 transforms to a Claude settings dict.

    Run on BOTH the pre-image and post-image; if the normalised forms
    differ, the discrepancy is real round-trip drift (a Wave-7 finding
    not yet pinned). The currently-pinned Wave-7 findings (F1, F2)
    are ALSO normalised away here so the main full-roundtrip test
    asserts only on novel drift; F1 and F2 have their own dedicated
    xfail-pinned tests below.
    """
    out = dict(raw)

    # P1-D: collapse legacy attribution aliases. We don't try to
    # reconstruct ``attribution.commit`` from the aliases here; we just
    # accept that the post-image has the canonical form and the
    # pre-image had aliases. Drop both sides' attribution-related keys
    # so the comparison ignores that axis.
    for alias in _LEGACY_ATTRIBUTION_ALIASES:
        out.pop(alias, None)
    out.pop("attribution", None)

    # Cosmetic: empty ``env: {}`` may be added when neutral
    # environment is empty. Treat absent and empty-dict as equivalent.
    if out.get("env") == {}:
        out.pop("env")

    # F1 (Wave-7 pinned): ``statusLine.type`` is dropped during
    # round-trip. Strip the ``type`` key on both sides so the main
    # roundtrip assertion ignores this axis; the dedicated F1 test
    # below pins the actual finding.
    if isinstance(out.get("statusLine"), dict):
        sl = dict(out["statusLine"])
        sl.pop("type", None)
        out["statusLine"] = sl

    # P1-A: ``enabledPlugins`` is reconciled (unioned) with the Codex
    # ``[plugins.*]`` table, so Claude may gain entries that originally
    # lived only in Codex (and vice-versa). Compare key *sets* and
    # individual values, but ignore key ordering. The full-roundtrip
    # test handles the union semantics by separate per-key checks; we
    # drop these from the structural compare here.
    out.pop("enabledPlugins", None)
    out.pop("extraKnownMarketplaces", None)

    return out


def _normalize_codex_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply the documented Wave-5 transforms to a Codex config dict.

    F2 (Wave-7 pinned) is handled by dropping per-marketplace
    sub-keys before comparison; the dedicated F2 test below pins
    the actual finding.
    """
    out = _toml_to_plain(raw)

    # Cosmetic: empty ``[shell_environment_policy.set]`` table may be
    # added when neutral environment is empty.
    sep = out.get("shell_environment_policy")
    if isinstance(sep, dict) and sep.get("set") == {}:
        sep.pop("set")
        if not sep:
            out.pop("shell_environment_policy")

    # B2: alphabetical sort on dict-of-tables keys.
    for k in ("plugins", "marketplaces", "mcp_servers", "projects"):
        if k in out and isinstance(out[k], dict):
            out[k] = {kk: out[k][kk] for kk in sorted(out[k])}

    # F2 (Wave-7 pinned): per-marketplace ``last_updated`` and
    # ``last_revision`` cache state is dropped during round-trip.
    # Strip those keys on both sides so the main roundtrip assertion
    # ignores this axis.
    if isinstance(out.get("marketplaces"), dict):
        for name, body in list(out["marketplaces"].items()):
            if isinstance(body, dict):
                cache_keys = {"last_updated", "last_revision"}
                stripped = {k: v for k, v in body.items() if k not in cache_keys}
                out["marketplaces"][name] = stripped

    # P1-D (cosmetic): Codex now carries an empty
    # ``commit_attribution = ""`` mirror of Claude's consolidated
    # ``attribution.commit``. Treat as equivalent to absent.
    if out.get("commit_attribution") == "":
        out.pop("commit_attribution")

    return out


# --------------------------------------------------------------------------
# 1. Full-surface semantic round-trip
# --------------------------------------------------------------------------


def test_exemplar_full_roundtrip_preserves_semantic_content(
    exemplar_env: dict[str, Path],
) -> None:
    """After init+merge, the live config files must equal the exemplar
    modulo the documented Wave-5 transforms (P1-D, P1-A, B2, B4, and
    the two cosmetic empties).
    """
    home = exemplar_env["home"]
    live_settings = home / ".claude" / "settings.json"
    live_codex = home / ".codex" / "config.toml"
    live_dotc = home / ".claude.json"

    pre_settings = json.loads(live_settings.read_text(encoding="utf-8"))
    pre_codex = tomlkit.loads(live_codex.read_text(encoding="utf-8"))
    pre_dotc = json.loads(live_dotc.read_text(encoding="utf-8"))

    _init_and_merge(exemplar_env)

    post_settings = json.loads(live_settings.read_text(encoding="utf-8"))
    post_codex = tomlkit.loads(live_codex.read_text(encoding="utf-8"))
    post_dotc = json.loads(live_dotc.read_text(encoding="utf-8"))

    # ~/.claude.json: chameleon owns mcpServers; assert deep-equal on
    # everything else.
    pre_no_mcp = {k: v for k, v in pre_dotc.items() if k != "mcpServers"}
    post_no_mcp = {k: v for k, v in post_dotc.items() if k != "mcpServers"}
    assert post_no_mcp == pre_no_mcp, (
        "~/.claude.json non-mcpServers content drifted: "
        f"missing={set(pre_no_mcp) - set(post_no_mcp)}, "
        f"added={set(post_no_mcp) - set(pre_no_mcp)}"
    )

    # Claude settings.json: deep-equal modulo documented normalisation.
    assert _normalize_claude_settings(post_settings) == _normalize_claude_settings(pre_settings), (
        "Claude settings.json drifted beyond documented exceptions; this is a Wave-7 finding."
    )

    # P1-A union of marketplaces / plugins is allowed to ADD entries
    # to either side, but every entry that existed in the pre-image
    # must still exist in the post-image with the same value.
    for k in pre_settings.get("enabledPlugins", {}):
        assert k in post_settings.get("enabledPlugins", {}), f"enabledPlugins lost the {k!r} entry"
        assert post_settings["enabledPlugins"][k] == pre_settings["enabledPlugins"][k], (
            f"enabledPlugins[{k!r}] flipped value"
        )
    for k, v in pre_settings.get("extraKnownMarketplaces", {}).items():
        assert k in post_settings.get("extraKnownMarketplaces", {}), (
            f"extraKnownMarketplaces lost the {k!r} entry"
        )
        assert post_settings["extraKnownMarketplaces"][k] == v, (
            f"extraKnownMarketplaces[{k!r}] structure changed"
        )

    # Codex config.toml: deep-equal modulo documented normalisation.
    norm_pre = _normalize_codex_config(pre_codex)
    norm_post = _normalize_codex_config(post_codex)

    # Plugins/marketplaces/mcp_servers may have grown (P1-A union);
    # assert pre is a subset of post on those, then strip from the
    # structural compare. ``mcp_servers`` may exist only in post
    # (the exemplar's MCP server lives in ~/.claude.json and is
    # reconciled into Codex during merge).
    for k in ("plugins", "marketplaces", "mcp_servers"):
        for entry_k, entry_v in norm_pre.get(k, {}).items():
            assert entry_k in norm_post.get(k, {}), (
                f"Codex [{k}.{entry_k}] disappeared from config.toml"
            )
            assert norm_post[k][entry_k] == entry_v, (
                f"Codex [{k}.{entry_k}] sub-table content drifted: "
                f"pre={entry_v!r} post={norm_post[k][entry_k]!r} "
                "(this is the Wave-7 sub-table-extras finding F2 if "
                "the missing keys are last_updated/last_revision)"
            )
        norm_pre.pop(k, None)
        norm_post.pop(k, None)
    # `projects` is not unioned; should be exact.
    assert norm_post == norm_pre, (
        "Codex config.toml drifted beyond documented exceptions; this is a Wave-7 finding."
    )


# --------------------------------------------------------------------------
# 2. Idempotency at the byte level
# --------------------------------------------------------------------------


def test_exemplar_idempotency_byte_stable(exemplar_env: dict[str, Path]) -> None:
    """Wave-5 B2 closed dict-ordering instability. Two consecutive
    keep-merges against the exemplar must produce byte-identical
    target files.
    """
    home = exemplar_env["home"]
    live_settings = home / ".claude" / "settings.json"
    live_codex = home / ".codex" / "config.toml"
    live_dotc = home / ".claude.json"

    _init_and_merge(exemplar_env)
    a_settings = live_settings.read_bytes()
    a_codex = live_codex.read_bytes()
    a_dotc = live_dotc.read_bytes()

    second = _run(["merge", "--on-conflict=keep"], exemplar_env)
    assert second.returncode == 0, f"second merge failed: {second.stderr[-200:]}"
    b_settings = live_settings.read_bytes()
    b_codex = live_codex.read_bytes()
    b_dotc = live_dotc.read_bytes()

    assert a_settings == b_settings, "second keep-merge changed Claude settings.json bytes"
    assert a_codex == b_codex, "second keep-merge changed Codex config.toml bytes"
    assert a_dotc == b_dotc, "second keep-merge changed ~/.claude.json bytes"


# --------------------------------------------------------------------------
# 3. Non-ASCII codepoint preservation (B4)
# --------------------------------------------------------------------------


def test_exemplar_full_unicode_preserved(exemplar_env: dict[str, Path]) -> None:
    """Every non-ASCII codepoint in the original ``~/.claude.json``
    must survive the merge.

    The fixture stores the em-dash as ``\\u2014`` in the source bytes
    but as the literal U+2014 codepoint when parsed; we compare on
    the parsed string so escape vs literal is not material (only the
    semantic codepoint is).
    """
    home = exemplar_env["home"]
    live_dotc = home / ".claude.json"

    pre = json.loads(live_dotc.read_text(encoding="utf-8"))
    pre_text_serialised = json.dumps(pre, ensure_ascii=False)
    pre_non_ascii = sorted({c for c in pre_text_serialised if ord(c) > 127})
    assert pre_non_ascii, (
        "fixture invariant violated: exemplar ~/.claude.json should "
        "contain at least one non-ASCII codepoint (em-dash)"
    )

    _init_and_merge(exemplar_env)

    post = json.loads(live_dotc.read_text(encoding="utf-8"))
    post_text_serialised = json.dumps(post, ensure_ascii=False)

    missing = [c for c in pre_non_ascii if c not in post_text_serialised]
    assert not missing, (
        f"non-ASCII codepoints were dropped from ~/.claude.json: {[hex(ord(c)) for c in missing]}"
    )


# --------------------------------------------------------------------------
# 4. Pass-through bag should be empty after Wave-4 closed every codec
# --------------------------------------------------------------------------


def test_exemplar_zero_unexpected_passthrough(exemplar_env: dict[str, Path]) -> None:
    """After Wave-4, every claimed key has a codec; therefore the
    exemplar should produce an empty ``targets.<target>.items``
    pass-through bag in neutral.yaml.

    If any keys leak in, list them — that's the codec that missed
    something during Wave-4 closure.
    """
    _init_and_merge(exemplar_env)
    neutral = exemplar_env["config"] / "chameleon" / "neutral.yaml"
    assert neutral.exists(), f"neutral.yaml missing at {neutral}"
    parsed_raw = load_yaml(neutral)
    assert isinstance(parsed_raw, dict), (
        f"neutral.yaml didn't parse as a mapping: {type(parsed_raw)!r}"
    )
    parsed = cast(dict[str, Any], parsed_raw)

    targets_raw = parsed.get("targets") or {}
    assert isinstance(targets_raw, dict), "neutral.targets must be a mapping"
    leaks: dict[str, list[str]] = {}
    for tgt, body in targets_raw.items():
        body_dict: dict[str, Any] = body if isinstance(body, dict) else {}
        items = body_dict.get("items") or {}
        if items:
            leaks[tgt] = sorted(items.keys())
    assert not leaks, (
        "pass-through bag is non-empty after init+merge; some claimed "
        "key didn't make it into a codec: "
        f"{leaks}"
    )


# --------------------------------------------------------------------------
# 5. Pinned Wave-7 findings — strict xfail until fixed.
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Wave-7 finding F1: Claude statusLine.type='command' is dropped "
        "during round-trip because _ClaudeStatusLine.type carries a "
        "default and is excluded as a default at serialise time. The "
        "exemplar ships {type:'command', command:...}; after merge only "
        "command survives. Fix path: explicitly include `type` in the "
        "codec emission, OR have the assembler harvest sub-section "
        "extras the same way B1 harvests top-level extras."
    ),
)
def test_wave7_f1_status_line_type_preserved(exemplar_env: dict[str, Path]) -> None:
    """Pinned Wave-7 finding — flips green when F1 is fixed."""
    home = exemplar_env["home"]
    live_settings = home / ".claude" / "settings.json"
    pre = json.loads(live_settings.read_text(encoding="utf-8"))
    pre_status = pre.get("statusLine")
    assert isinstance(pre_status, dict)
    assert pre_status.get("type") == "command", "fixture invariant"

    _init_and_merge(exemplar_env)

    post = json.loads(live_settings.read_text(encoding="utf-8"))
    post_status = post.get("statusLine") or {}
    assert post_status.get("type") == "command", (
        f"statusLine.type was dropped: post={post_status!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Wave-7 finding F2: Codex [marketplaces.<name>] tables lose "
        "last_updated/last_revision sub-keys after round-trip. The "
        "marketplace codec models only source/source_type; the per-"
        "marketplace dict-of-tables shape isn't covered by B1's "
        "section-level extras harvester. Same shape as B1 but one "
        "level deeper (dict-of-tables values, not section top-level)."
    ),
)
def test_wave7_f2_codex_marketplace_extras_preserved(
    exemplar_env: dict[str, Path],
) -> None:
    """Pinned Wave-7 finding — flips green when F2 is fixed."""
    home = exemplar_env["home"]
    live_codex = home / ".codex" / "config.toml"

    pre = tomlkit.loads(live_codex.read_text(encoding="utf-8"))
    pre_marketplaces = _toml_to_plain(pre.get("marketplaces") or {})
    assert pre_marketplaces, "fixture invariant: exemplar has marketplaces"
    sample_name = next(iter(pre_marketplaces))
    pre_sample = pre_marketplaces[sample_name]
    assert "last_updated" in pre_sample, (
        f"fixture invariant: marketplace[{sample_name!r}] should carry last_updated"
    )
    assert "last_revision" in pre_sample, (
        f"fixture invariant: marketplace[{sample_name!r}] should carry last_revision"
    )

    _init_and_merge(exemplar_env)

    post = tomlkit.loads(live_codex.read_text(encoding="utf-8"))
    post_marketplaces = _toml_to_plain(post.get("marketplaces") or {})
    post_sample = post_marketplaces.get(sample_name) or {}
    missing = sorted(set(pre_sample) - set(post_sample))
    assert not missing, (
        f"Codex [marketplaces.{sample_name}] lost sub-keys: {missing}; "
        f"pre={pre_sample}, post={post_sample}"
    )
