"""FUZZ-4 — pass-through preservation at adversarial nesting depths.

 B1 (sub-table extras via ``__pydantic_extra__``) and  F2
(typed-default rountrip) fixed pass-through preservation for the cases
the B1/F2 regression tests cover.  This module pushes the same invariant
into the corners those targeted tests don't reach: arbitrary unknown
keys spliced at random depths inside the wire-form of both Claude
``settings.json`` and Codex ``config.toml``, including

  * top-level (the ``passthrough`` dict path),
  * inside claimed-section bodies (the ``__pydantic_extra__`` path),
  * inside dict-of-tables values such as ``mcp_servers.<name>`` and
    ``hooks.<event>[*]`` (the recursive ``_walk_field_extras`` path),
  * inside doubly-nested sub-tables such as
    ``[tui.model_availability_nux]`` and ``[history.<future_subtable>]``.

The contract this fuzz test pins is identical to the one /7 stated
in prose: every unknown key the operator wrote into the live target
file must come back out of ``assemble(disassemble(file))``, byte-shape
permitting (we test by parsing the output and walking it).  If a single
preservation failure is found, the test fails with a Hypothesis
counter-example showing the splice path and the dropped key — that is a
 regression of B1/F2 class.

Constraints (from the FUZZ-4 task spec):

  * ONLY this file is added (no codec, schema, assembler edits).
  * Unknown keys are namespaced with the ``__chameleon_fuzz_extra_``
    prefix so they cannot collide with any modelled Pydantic field at
    any depth — collision would turn a valid fuzz example into a
    spurious validation failure.
  * The test is gated by the ``fuzz`` marker so the default ``pytest``
    invocation skips it; ``-m fuzz`` (or ``HYPOTHESIS_PROFILE=fuzz``
    nightly) exercises it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

import pytest
import tomlkit
from hypothesis import HealthCheck, given, settings, target
from hypothesis import strategies as st

from chameleon.targets.claude.assembler import ClaudeAssembler
from chameleon.targets.codex.assembler import CodexAssembler

# Importing the strategies module wires every ``register_type_strategy``
# call before ``@given`` collects.  This mirrors the import idiom in
# ``tests/fuzz/test_smoke.py`` — both files declare the dependency
# locally so they read coherently in isolation.
from tests.fuzz import strategies as _strategies  # noqa: F401

pytestmark = pytest.mark.fuzz


# ----------------------------------------------------------------------
# Splice-point catalogue.
#
# Each entry names a path inside one of the wire-form documents the
# assemblers consume.  The path tuple is interpreted relative to the
# ROOT dict (``settings.json`` for Claude, ``config.toml`` for Codex).
# ``"<key>"`` segments descend into a fixed dict key; the synthetic
# ``"<dict_value>"`` sentinel descends into an arbitrary entry of a
# dict-of-tables (we draw a random key name when generating).
#
# The catalogue intentionally covers each of the four preservation
# axes /7 fixed:
#   - top-level (the empty path ``()``),
#   - inside a claimed section (e.g. ``("tui",)``),
#   - inside a dict-of-tables entry (``("mcpServers", "<dict_value>")``),
#   - doubly-nested (e.g. ``("tui", "model_availability_nux")``,
#     which is itself an unclaimed sub-table inside a claimed section).
#
# Adding entries here widens fuzz coverage; removing them narrows it.
# ----------------------------------------------------------------------

_DICT_VALUE = "<dict_value>"


_CLAUDE_SPLICE_POINTS: tuple[tuple[str, ...], ...] = (
    # Top-level — exercises the assembler's passthrough path.
    (),
    # Inside the claimed [statusLine] sub-object — exercises section
    # ``__pydantic_extra__`` at the section root.
    ("statusLine",),
    # Doubly-nested unclaimed sub-table inside a claimed section.
    ("statusLine", "__chameleon_fuzz_extra_inner"),
    # Inside an arbitrary mcpServers entry — exercises the
    # dict-of-tables walk (``_walk_field_extras`` over a dict value).
    ("mcpServers", _DICT_VALUE),
    # Inside the [permissions] sub-object — claimed by authorization,
    # extras land in ``__pydantic_extra__``.
    ("permissions",),
    # Inside [voice] — undocumented structured object the codec models;
    # extras here sit on the ``_ClaudeVoice.model_config`` extra=allow.
    ("voice",),
)


_CODEX_SPLICE_POINTS: tuple[tuple[str, ...], ...] = (
    # Top-level — Codex passthrough.
    (),
    # Inside [tui] — claimed by interface, extras → __pydantic_extra__.
    ("tui",),
    # Inside [tui.<unclaimed_subtable>] — doubly-nested.
    ("tui", "__chameleon_fuzz_extra_inner"),
    # Inside [history] — claimed by lifecycle.
    ("history",),
    # Inside [history.<unclaimed_subtable>] — doubly-nested.
    ("history", "__chameleon_fuzz_extra_inner"),
    # Inside an mcp_servers.<name> sub-table — dict-of-tables.
    ("mcp_servers", _DICT_VALUE),
    # Inside [shell_environment_policy] — extras on the section.
    ("shell_environment_policy",),
)


# ----------------------------------------------------------------------
# Extras generators.
#
# The unknown-key prefix makes these guaranteed-non-colliding with any
# Pydantic field name at any depth: every modelled field in the schema
# uses identifier-shaped names that DON'T begin with the marker. The
# values are arbitrary JSON / TOML-friendly leaves and small recursive
# composites — pass-through is parametric in value type, so the strategy
# can be permissive.
# ----------------------------------------------------------------------

_EXTRA_KEY_PREFIX = "__chameleon_fuzz_extra_"


_extra_key: st.SearchStrategy[str] = st.from_regex(
    r"\A__chameleon_fuzz_extra_[a-z][a-z0-9_]{0,12}\Z",
    fullmatch=True,
)

# TOML cannot serialise ``None``; both formats accept the rest. The
# value strategy is intentionally NOT recursive deeply — we want lots
# of cheap examples that splice many extras, not a few examples that
# happen to splice one giant tree.
_extra_scalar: st.SearchStrategy[Any] = st.one_of(
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31 - 1),
    st.text(
        alphabet=st.characters(min_codepoint=0x0020, max_codepoint=0x007E),
        max_size=32,
    ),
)

_extra_value: st.SearchStrategy[Any] = st.recursive(
    _extra_scalar,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(
            keys=st.from_regex(r"\A[a-z][a-z0-9_]{0,8}\Z", fullmatch=True),
            values=children,
            max_size=4,
        ),
    ),
    max_leaves=8,
)


@st.composite
def _extras_dict(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a dict of unknown keys → arbitrary JSON/TOML-friendly values.

    At least one entry — an empty extras dict is a vacuous splice that
    teaches the search nothing.  Hypothesis would otherwise shrink to
    that and report it as the minimum failing example, masking real
    failures further out.
    """
    n = draw(st.integers(min_value=1, max_value=4))
    out: dict[str, Any] = {}
    for _ in range(n):
        # Loop until a fresh non-colliding key — ``_extra_key`` regex
        # already prevents collisions with modelled fields, but draws
        # can still repeat within one dict.
        for _attempt in range(10):
            key = draw(_extra_key)
            if key not in out:
                out[key] = draw(_extra_value)
                break
    return out


# ----------------------------------------------------------------------
# Wire-form skeleton generators.
#
# Build a minimal-but-realistic claimed-keys structure for each target.
# The skeleton is intentionally NOT exhaustive — its purpose is to
# provide attachment points for the splice-extras step, not to fuzz
# the schema itself (the smoke test and the cross-codec parity tests
# already cover that axis).
# ----------------------------------------------------------------------


@st.composite
def _claude_skeleton(draw: st.DrawFn) -> dict[str, Any]:
    """Plausible Claude ``settings.json`` skeleton.

    Each sub-table is included with probability one — we want every
    splice point reachable on every example.  The contents are minimal
    valid values per the section schemas (``ClaudeInterfaceSection``
    et al.); extras get spliced AFTER this skeleton is built.
    """
    n_mcp = draw(st.integers(min_value=1, max_value=3))
    mcp_servers: dict[str, dict[str, Any]] = {}
    for i in range(n_mcp):
        name = f"server_{i}"
        mcp_servers[name] = {
            "type": "stdio",
            "command": "echo",
            "args": ["hello"],
        }
    return {
        "model": "claude-sonnet-4-7",
        "statusLine": {"type": "command", "command": "echo"},
        "permissions": {"allow": [], "deny": []},
        "voice": {"enabled": True, "mode": "casual"},
        # mcpServers lives in ~/.claude.json, but the assembler reads
        # the dotclaude file separately.  We generate it as part of
        # the dotclaude wire form below.
    }


@st.composite
def _claude_dotclaude_skeleton(draw: st.DrawFn) -> dict[str, Any]:
    n_mcp = draw(st.integers(min_value=1, max_value=3))
    mcp_servers: dict[str, dict[str, Any]] = {}
    for i in range(n_mcp):
        name = f"server_{i}"
        mcp_servers[name] = {
            "type": "stdio",
            "command": "echo",
            "args": ["hello"],
        }
    return {"mcpServers": mcp_servers}


@st.composite
def _codex_skeleton(draw: st.DrawFn) -> dict[str, Any]:
    """Plausible Codex ``config.toml`` skeleton.

    Tables and sub-tables are populated with valid values per the
    Codex section schemas.  As with Claude, the skeleton is purpose-
    built to give each splice point a place to attach.
    """
    n_mcp = draw(st.integers(min_value=1, max_value=3))
    mcp_servers: dict[str, dict[str, Any]] = {}
    for i in range(n_mcp):
        name = f"server_{i}"
        mcp_servers[name] = {
            "command": "echo",
            "args": ["hello"],
        }
    return {
        "model": "gpt-5",
        "tui": {"theme": "dark"},
        "history": {"persistence": "save-all", "max_bytes": 1048576},
        "mcp_servers": mcp_servers,
        "shell_environment_policy": {"set": {}},
    }


# ----------------------------------------------------------------------
# Splice helpers.
#
# Walk a wire-form dict to a path, creating any missing intermediate
# tables, then merge an extras dict.  The ``<dict_value>`` sentinel
# means "descend into an arbitrary existing entry"; if the dict is
# empty we create one.  After splice, every key from ``extras`` is
# guaranteed to be reachable at the same path in the output dict.
# ----------------------------------------------------------------------


def _walk_to_splice_point(
    root: dict[str, Any],
    path: tuple[str, ...],
    sample_dict_key: str,
) -> dict[str, Any]:
    """Walk ``root`` along ``path``, creating missing intermediates.

    Returns the dict at the final segment, where extras get merged.
    The ``<dict_value>`` sentinel is replaced by ``sample_dict_key``;
    if that key isn't already present in the dict-of-tables, an empty
    sub-dict is created.
    """
    cur: dict[str, Any] = root
    for seg in path:
        if seg == _DICT_VALUE:
            # ``cur`` is dict-of-tables; descend into ``sample_dict_key``.
            existing = cur.get(sample_dict_key)
            if not isinstance(existing, dict):
                cur[sample_dict_key] = {}
            cur = cast("dict[str, Any]", cur[sample_dict_key])
        else:
            existing = cur.get(seg)
            if not isinstance(existing, dict):
                cur[seg] = {}
            cur = cast("dict[str, Any]", cur[seg])
    return cur


def _splice_extras(
    root: dict[str, Any],
    splices: list[tuple[tuple[str, ...], dict[str, Any], str]],
) -> list[tuple[tuple[str, ...], str]]:
    """Apply a batch of (path, extras, sample_key) splices to ``root``.

    Returns the list of (resolved_path, extra_key) tuples — every entry
    is a (path-from-root, key) the round-trip MUST preserve.  The
    resolved_path replaces ``<dict_value>`` with the sample_key so the
    verifier can walk the output dict deterministically.
    """
    expectations: list[tuple[tuple[str, ...], str]] = []
    for path, extras, sample_key in splices:
        bucket = _walk_to_splice_point(root, path, sample_key)
        for k, v in extras.items():
            # If the splice point already had this exact unknown key from
            # an earlier splice (rare — the prefix space is large), the
            # later draw wins; the expectation list still references it.
            bucket[k] = v
            resolved = tuple(sample_key if seg == _DICT_VALUE else seg for seg in path)
            expectations.append((resolved, k))
    return expectations


def _walk_dict(root: Mapping[str, Any], path: tuple[str, ...]) -> Mapping[str, Any] | None:
    """Walk ``root`` along ``path``; return None if any segment is missing
    or non-dict-shaped at the final level.
    """
    cur: Any = root
    for seg in path:
        if not isinstance(cur, Mapping):
            return None
        if seg not in cur:
            return None
        cur = cur[seg]
    return cur if isinstance(cur, Mapping) else None


# ----------------------------------------------------------------------
# Composite "splice plan" — Hypothesis draws a small list of splices
# (path, extras, dict-key) for a given splice-point catalogue.  The
# round-trip test feeds the plan into the assembler and verifies every
# (path, key) expectation survives.
# ----------------------------------------------------------------------


@st.composite
def _splice_plan(
    draw: st.DrawFn,
    splice_points: tuple[tuple[str, ...], ...],
) -> list[tuple[tuple[str, ...], dict[str, Any], str]]:
    """Draw a list of splices, biased toward many-and-deep.

    The min_size=1 floor guarantees every example has at least one
    extra to verify — empty plans teach Hypothesis nothing.  The cap
    of 6 keeps runtimes bounded under the 10s ``fuzz`` deadline; in
    practice the search saturates well before the cap.
    """
    n = draw(st.integers(min_value=1, max_value=6))
    plan: list[tuple[tuple[str, ...], dict[str, Any], str]] = []
    for _ in range(n):
        path = draw(st.sampled_from(splice_points))
        extras = draw(_extras_dict())
        # Only meaningful when path contains the sentinel; otherwise
        # ignored.  We always draw to keep the example deterministic.
        sample_key = draw(st.sampled_from(["server_0", "server_1", "server_2"]))
        plan.append((path, extras, sample_key))
    return plan


# ----------------------------------------------------------------------
# Round-trip verification.
#
# For each target we:
#   1. Build the wire-form skeleton.
#   2. Splice unknown keys per the plan.
#   3. Run disassemble → assemble.
#   4. Parse the output and walk every expectation.
#
# Failure mode: if an expectation isn't reachable in the output, the
# test fails with a Hypothesis counter-example pinpointing the
# (target, path, key) that was dropped.
# ----------------------------------------------------------------------


def _max_path_depth(plan: list[tuple[tuple[str, ...], dict[str, Any], str]]) -> int:
    """The deepest splice path length used in a plan, for ``target()``."""
    if not plan:
        return 0
    return max(len(path) for path, _extras, _key in plan)


@settings(suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(
    skel=_claude_skeleton(),
    dotclaude=_claude_dotclaude_skeleton(),
    plan=_splice_plan(_CLAUDE_SPLICE_POINTS),
)
def test_claude_passthrough_preservation_at_random_depth(
    skel: dict[str, Any],
    dotclaude: dict[str, Any],
    plan: list[tuple[tuple[str, ...], dict[str, Any], str]],
) -> None:
    """Claude assembler preserves unknown keys at adversarial depths.

    Splice ``__chameleon_fuzz_extra_*`` keys at random depths into a
    plausible ``settings.json`` skeleton; round-trip via the assembler;
    assert every spliced (path, key) survives.
    """
    expectations = _splice_extras(skel, plan)

    # Bias the search toward deeper splice paths — Hypothesis will
    # preferentially shrink toward whichever input has high target
    # value, so this nudges it toward the corner of the search space
    # we care about.
    target(float(_max_path_depth(plan)), label="splice depth")

    # Encode skeleton → bytes for both Claude files.  The dotclaude
    # half carries mcpServers; the test plan only splices into
    # settings.json's structure (its splice_points catalogue), which
    # keeps the dotclaude path concerns out of the per-target plan
    # but still exercises the assembler reading both files.
    files = {
        ClaudeAssembler.SETTINGS_JSON: json.dumps(skel).encode("utf-8"),
        ClaudeAssembler.DOTCLAUDE_JSON: json.dumps(dotclaude).encode("utf-8"),
    }

    # Disassemble + immediately re-assemble (no neutral-side codec
    # round-trip — that's covered by Wave-F2's smoke test).  This is
    # the layer where B1's harvest-and-reproject lives.
    domains, passthrough = ClaudeAssembler.disassemble(files)
    out_files = ClaudeAssembler.assemble(
        per_domain=domains,
        passthrough=passthrough,
        existing=files,
    )

    out_settings_raw = out_files[ClaudeAssembler.SETTINGS_JSON]
    out_settings = json.loads(out_settings_raw.decode("utf-8"))
    assert isinstance(out_settings, dict)

    for path, key in expectations:
        bucket = _walk_dict(out_settings, path)
        assert bucket is not None, (
            f"path {path!r} disappeared from settings.json round-trip; "
            f"expected key {key!r} unreachable.  This is a B1/F2-class "
            f"regression — report as  finding."
        )
        assert key in bucket, (
            f"unknown key {key!r} dropped at path {path!r} during "
            f"Claude disassemble/assemble round-trip.  Bucket keys: "
            f"{sorted(bucket.keys())!r}.  This is a B1/F2-class regression "
            f"— report as  finding."
        )


def _toml_dumps(value: Mapping[str, Any]) -> str:
    """Serialise a plain dict to TOML via tomlkit.

    We intentionally use ``tomlkit.dumps`` rather than building a
    Document by hand: the assembler also uses tomlkit on the way out,
    so any encoder-level quirks (key quoting, sub-table promotion)
    apply uniformly to input and output.
    """
    doc = tomlkit.document()
    _hydrate_toml(doc, value)
    return tomlkit.dumps(doc)


def _hydrate_toml(target_doc: MutableMapping[str, Any], value: Mapping[str, Any]) -> None:
    for k, v in value.items():
        if isinstance(v, Mapping):
            sub = tomlkit.table()
            _hydrate_toml(sub, cast("Mapping[str, Any]", v))
            target_doc[k] = sub
        else:
            target_doc[k] = v


@settings(suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(
    skel=_codex_skeleton(),
    plan=_splice_plan(_CODEX_SPLICE_POINTS),
)
def test_codex_passthrough_preservation_at_random_depth(
    skel: dict[str, Any],
    plan: list[tuple[tuple[str, ...], dict[str, Any], str]],
) -> None:
    """Codex assembler preserves unknown keys at adversarial depths."""
    expectations = _splice_extras(skel, plan)

    target(float(_max_path_depth(plan)), label="splice depth")

    raw_toml = _toml_dumps(skel).encode("utf-8")
    files = {CodexAssembler.CONFIG_TOML: raw_toml}

    domains, passthrough = CodexAssembler.disassemble(files)
    out_files = CodexAssembler.assemble(
        per_domain=domains,
        passthrough=passthrough,
        existing=files,
    )

    out_raw = out_files[CodexAssembler.CONFIG_TOML]
    out_doc = tomlkit.parse(out_raw.decode("utf-8"))
    # tomlkit.Document is a MutableMapping[str, Any] at the type level
    # but invariant generics force a cast when we hand it to the
    # plain-dict walker.  Runtime guarantees keys-are-str (TOML grammar).
    out_dict = cast("Mapping[str, Any]", out_doc)

    for path, key in expectations:
        bucket = _walk_dict(out_dict, path)
        assert bucket is not None, (
            f"path {path!r} disappeared from config.toml round-trip; "
            f"expected key {key!r} unreachable.  This is a B1/F2-class "
            f"regression — report as  finding."
        )
        assert key in bucket, (
            f"unknown key {key!r} dropped at path {path!r} during "
            f"Codex disassemble/assemble round-trip.  Bucket keys: "
            f"{sorted(bucket.keys())!r}.  This is a B1/F2-class regression "
            f"— report as  finding."
        )


# ----------------------------------------------------------------------
# Sanity guard — every splice path begins with a top-level segment
# that the assembler either claims (so extras land in section extras)
# or doesn't (so extras land in passthrough).  Guards against future
# schema renames silently turning splice points into no-ops.
# ----------------------------------------------------------------------


def _verify_splice_points_resolvable() -> None:
    """All splice paths must have at least one valid attachment in the
    skeletons OR resolve through the catalogue's defined first segment.

    This is a static cross-check: if a future refactor renames a
    top-level key, the catalogue here still references the old name
    and would silently splice into a never-read corner.  The test
    body's expectations would then succeed vacuously.
    """
    expected_first_segments = {
        "statusLine",
        "permissions",
        "voice",
        "mcpServers",
        "tui",
        "history",
        "mcp_servers",
        "shell_environment_policy",
    }
    for path in (*_CLAUDE_SPLICE_POINTS, *_CODEX_SPLICE_POINTS):
        if not path:
            continue
        first = path[0]
        if first not in expected_first_segments:
            msg = (
                f"splice point {path!r} starts with {first!r} which is not "
                f"in the curated first-segment set.  Update the assertion "
                f"set if this is intentional; otherwise remove the splice "
                f"point so the fuzz test doesn't degrade silently."
            )
            raise AssertionError(msg)


_verify_splice_points_resolvable()
