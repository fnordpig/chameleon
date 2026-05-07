"""Wave-F2 cross-target unification differential.

The user's primary concern (verbatim): "valid settings files that fail
to correctly transpile to the other dialect". This is the test that
catches it.

For every neutral path that BOTH targets claim — the curated
``cross_target_shared_paths()`` list in :mod:`tests.fuzz.strategies` —
we generate adversarial inputs and probe four properties through the
Claude and Codex codec lanes:

1. **Encode-symmetry** — ``decode(encode_claude(x)) ==
   decode(encode_codex(x))`` *projected at the shared path*. Either
   side may differ, but only when accompanied by a
   :class:`~chameleon.codecs._protocol.LossWarning` that names the loss.

2. **Decode-symmetry** — when the SAME wire shape arrives at both
   targets (a synthetic round-trip baseline), both decoders recover the
   same neutral slice. Implemented as: encode through one target,
   decode, re-encode through the OTHER target, decode again, and the
   recovered slice equals the original at the path.

3. **Idempotence on each side** — ``encode(decode(encode(x))) ==
   encode(x)`` for both Claude and Codex independently. Not a
   cross-target property strictly, but the only honest baseline against
   which property 1 has any meaning: if a single side is itself
   non-idempotent, the cross-target comparison is conflating two bugs.

4. **No silent divergence** — any difference between the two
   target lanes at a shared-path projection MUST be explained by at
   least one ``LossWarning`` on either context. Silent loss is the
   precise failure mode this whole suite exists to catch.

The four properties are exercised per-path via ``@pytest.mark.parametrize``
over the curated list; per-path, the value strategy is wired explicitly
because Hypothesis cannot derive a single strategy that exercises a
slice of an arbitrarily-deep model usefully — the slice's shape varies
per path and the projection function must match.

The "operator intent" oracle is **semantic round-trip equality at the
projected slice**, not byte equality of the produced section. Sections
legitimately differ in spelling between targets (``effortLevel`` vs.
``model_reasoning_effort``) — that's not a bug. What WOULD be a bug is
``Identity(reasoning_effort=MEDIUM)`` decoding back as
``Identity(reasoning_effort=HIGH)`` through one lane and
``Identity(reasoning_effort=MEDIUM)`` through the other.

Found divergences are HIGH-priority  findings — DO NOT fix
codec code in this branch; report them up to the parent agent so the
user (whose primary concern this is) sees them as the test result.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis import target as hyp_target

from chameleon._types import FieldPath
from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.codecs.claude.directives import ClaudeDirectivesCodec
from chameleon.codecs.claude.environment import ClaudeEnvironmentCodec
from chameleon.codecs.claude.identity import ClaudeIdentityCodec
from chameleon.codecs.codex.capabilities import CodexCapabilitiesCodec
from chameleon.codecs.codex.directives import CodexDirectivesCodec
from chameleon.codecs.codex.environment import CodexEnvironmentCodec
from chameleon.codecs.codex.identity import CodexIdentityCodec
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.capabilities import Capabilities
from chameleon.schema.directives import Directives
from chameleon.schema.environment import Environment
from chameleon.schema.identity import Identity

# Importing the strategies module wires `register_type_strategy` so the
# per-domain composites this test draws are available. The conftest also
# imports it, but the explicit re-import lets this file stand on its own.
from tests.fuzz import strategies as strats

pytestmark = pytest.mark.fuzz


# ----------------------------------------------------------------------
# WAVE-11 HIGH-PRIORITY FINDINGS
#
# Running this test against the current codecs surfaces four real
# cross-target divergences. They are LIVE — the test will fail without
# the xfail markers below — and they are exactly the failure mode the
# user named as their primary concern: "valid settings files that fail
# to correctly transpile to the other dialect".
#
# F-CWD  | capabilities.mcp_servers | RESOLVED in  by the paired
#        | branches  (Claude side) and
#        |  (Codex side). Both
#        | `_ClaudeMcpServerStdio` and `_CodexMcpServerStdio` now carry
#        | `cwd: str | None` and thread it through to_target/from_target.
#        | Original symptom: the neutral schema models cwd as a first-
#        | class field but neither codec did, so encoding through either
#        | lane and decoding back yielded cwd=None for any input where
#        | cwd was set, with no LossWarning.
#
# F-MP-G | capabilities.plugin_marketplaces | Codex codec silently
#        | rewrites kind='github' marketplaces to kind='git' when
#        | round-tripped (it synthesizes an HTTPS URL via
#        | `f"https://github.com/{repo}.git"` and stores it under
#        | source_type='git'; on decode it always reconstructs as
#        | kind='git'). The operator's documented "github" intent is
#        | erased without a LossWarning.
#        | Site: src/chameleon/codecs/codex/capabilities.py
#        |   `_codex_marketplace_from_neutral` collapses github -> git;
#        |   `_codex_marketplace_to_neutral` always returns
#        |   kind='git' for non-local sources.
#
# F-MP-U | capabilities.plugin_marketplaces | Codex codec is non-
#        | idempotent for kind='url' marketplaces. First encode emits
#        | source=<url> with source_type=None; decode collapses that
#        | to kind='git' (the `else` branch in
#        | `_codex_marketplace_to_neutral`); second encode then emits
#        | source_type='git'. The encode-on-encode oracle fails.
#        | Site: src/chameleon/codecs/codex/capabilities.py
#        |   `_codex_marketplace_to_neutral` lacks a 'url' branch.
#
# F-AU   | capabilities.plugin_marketplaces | Codex codec drops
#        | PluginMarketplace.auto_update entirely; it has no place in
#        | `_CodexMarketplaceEntry` and `_codex_marketplace_to_neutral`
#        | hard-codes `auto_update=None`. No LossWarning. (Surfaces as
#        | a per-lane loss when an input sets auto_update=True/False.)
#        | Site: src/chameleon/codecs/codex/capabilities.py
#        |   `_codex_marketplace_to_neutral` returns
#        |   `PluginMarketplace(..., auto_update=None)` unconditionally.
#
# These findings are EXACTLY the bug class FUZZ-3 was commissioned to
# surface. Per the task spec, this branch does NOT fix them — codec
# repair is 's lane. The xfail markers below are STRICT so that
# 's fixes land observable: the test goes red the moment a fix
# turns the xfail into an xpass.
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Per-path infrastructure.
#
# Each shared path in `cross_target_shared_paths()` needs three things:
#
# * A *value strategy* — a Hypothesis strategy that produces a domain
#   submodel (Identity / Directives / Capabilities / Environment) with
#   the path's slice populated by an adversarial value, and the rest of
#   the submodel left at defaults so we exercise the path-of-interest
#   in isolation.
#
# * A *codec pair* — the Claude and Codex codecs that own the domain.
#
# * A *projection function* — given a recovered neutral submodel,
#   extract just the slice claimed by the path. This is what we compare
#   across targets; comparing the whole submodel would conflate
#   per-target lossy axes (e.g. Identity.thinking on the Codex side)
#   with the path-under-test.
#
# The PER_PATH dict below is the dispatch table. Wave-F2 grows it as
# new shared paths are added to `strategies.cross_target_shared_paths()`.
# ----------------------------------------------------------------------


def _project(model: Any, path: FieldPath) -> Any:
    """Walk a Pydantic model along `path.segments`, returning the slice.

    Used by every property assertion as the "operator intent" oracle.
    Works for any path that resolves through attribute access on a
    chain of Pydantic models — every shared path in the curated list
    satisfies this.
    """
    cur: Any = model
    for seg in path.segments:
        cur = getattr(cur, seg)
    return cur


def _identity_model_lanes_agree(proj_claude: Any, proj_codex: Any) -> bool:
    """Comparator for `identity.model` cross-lane equality.

    `identity.model` is a `dict[TargetId, str]` and per-target by
    design (see `chameleon.schema.identity.IdentityModel`): each codec
    carries ONLY its own target's slice. The Claude codec recovers
    `{claude: ...}` (or None if the input had no claude key); the
    Codex codec recovers `{codex: ...}` (or None if no codex key).

    "Agreement" between the two lanes therefore cannot mean equality
    of the recovered dicts — that would never hold for inputs that set
    both keys. It means each lane carries its own target's value
    correctly, i.e. the union of the two recovered dicts is consistent
    with the input. The per-lane round-trip is verified separately by
    `test_no_silent_divergence_on_partial_input`'s lane-specific
    projection. Here we accept any pair where the two lanes don't
    contradict each other on a shared key (which they never can, since
    each lane only writes its own key — but the shape check guards
    against an accidental future regression).
    """
    claude_dict = proj_claude if isinstance(proj_claude, dict) else {}
    codex_dict = proj_codex if isinstance(proj_codex, dict) else {}
    # No overlap between target_key spaces is the design invariant; if
    # an overlap ever appears the values must agree. Today this is
    # vacuously true (each lane only sets its own key).
    overlap = set(claude_dict.keys()) & set(codex_dict.keys())
    return all(claude_dict[k] == codex_dict[k] for k in overlap)


def _per_target_model_projection_eq(
    proj_input: Any, proj_recovered: Any, *, target_key: Any
) -> bool:
    """Project `dict[TargetId, str]` to a single target's value for the
    chain-symmetry test. Inputs that have no entry for `target_key`
    project to None — and a recovered None matches; the lossless axis
    is "the target_key entry survived".
    """
    in_val = (proj_input or {}).get(target_key) if isinstance(proj_input, dict) else None
    out_val = (proj_recovered or {}).get(target_key) if isinstance(proj_recovered, dict) else None
    return in_val == out_val


# --- Identity strategies -------------------------------------------------

# `identity.reasoning_effort`: a sampled enum value from the cross-target
# vocabulary. Includes None for the "field absent" baseline.
_identity_with_reasoning_effort: st.SearchStrategy[Identity] = st.builds(
    Identity,
    reasoning_effort=st.one_of(st.none(), st.sampled_from(list(strats.ReasoningEffort))),
)

# `identity.model`: a per-target dict mapping. Both BUILTIN_CLAUDE and
# BUILTIN_CODEX must be tested independently and jointly, because the
# per-target model is the canonical place where "I set X for both targets,
# do they survive both lanes?" matters.
_identity_with_model: st.SearchStrategy[Identity] = st.builds(
    Identity,
    model=st.one_of(
        st.none(),
        st.dictionaries(
            keys=st.sampled_from([BUILTIN_CLAUDE, BUILTIN_CODEX]),
            values=st.text(min_size=1, max_size=64),
            min_size=1,
            max_size=2,
        ),
    ),
)

# `identity.thinking`: bool | None. Codex documents this as lossy
# (LossWarning on to_target); Claude carries it. The encode-symmetry
# assertion will detect the documented divergence and verify the
# warning is present.
_identity_with_thinking: st.SearchStrategy[Identity] = st.builds(
    Identity,
    thinking=st.one_of(st.none(), st.booleans()),
)


# --- Directives strategies -----------------------------------------------

_directives_with_commit_attribution: st.SearchStrategy[Directives] = st.builds(
    Directives,
    commit_attribution=st.one_of(st.none(), strats.unicode_torture()),
)

_directives_with_system_prompt_file: st.SearchStrategy[Directives] = st.builds(
    Directives,
    system_prompt_file=st.one_of(st.none(), strats.unicode_torture()),
)


# --- Capabilities strategies ---------------------------------------------

_capabilities_with_plugins: st.SearchStrategy[Capabilities] = st.builds(
    Capabilities,
    plugins=strats._cap_dict(strats._plugin_key, strats.plugin_entries),
)

_capabilities_with_plugin_marketplaces: st.SearchStrategy[Capabilities] = st.builds(
    Capabilities,
    plugin_marketplaces=strats._cap_dict(strats._short_text(), strats.plugin_marketplaces),
)

_capabilities_with_mcp_servers: st.SearchStrategy[Capabilities] = st.builds(
    Capabilities,
    mcp_servers=strats._cap_dict(strats._short_text(), strats.mcp_servers),
)


# --- Environment strategies ----------------------------------------------

_environment_with_variables: st.SearchStrategy[Environment] = st.builds(
    Environment,
    variables=strats._cap_dict(strats._short_text(), strats._short_text()),
)


# --- Per-path dispatch table ---------------------------------------------


PER_PATH: dict[FieldPath, dict[str, Any]] = {
    FieldPath(segments=("identity", "reasoning_effort")): {
        "value_strategy": _identity_with_reasoning_effort,
        "claude_codec": ClaudeIdentityCodec,
        "codex_codec": CodexIdentityCodec,
        # The projection drops the leading `identity.` segment because the
        # value strategy already produces an Identity, not a Neutral.
        "projection_path": FieldPath(segments=("reasoning_effort",)),
    },
    FieldPath(segments=("identity", "model")): {
        "value_strategy": _identity_with_model,
        "claude_codec": ClaudeIdentityCodec,
        "codex_codec": CodexIdentityCodec,
        "projection_path": FieldPath(segments=("model",)),
        # `identity.model` is per-target by design: each codec carries
        # only its own target's slice. The honest comparator checks the
        # per-target subkeys independently rather than expecting whole-
        # dict equality between the lanes.
        "encode_symmetry_eq": _identity_model_lanes_agree,
        "chain_symmetry_eq": (
            # The Claude->Codex chain only round-trips the Codex slice
            # of a per-target mapping (Claude's lane drops the Codex
            # entry, then Codex's lane is the only one that carries it).
            # Compare the Codex-key projection of input vs. chain output.
            lambda i, o: _per_target_model_projection_eq(i, o, target_key=BUILTIN_CODEX)
        ),
    },
    FieldPath(segments=("identity", "thinking")): {
        "value_strategy": _identity_with_thinking,
        "claude_codec": ClaudeIdentityCodec,
        "codex_codec": CodexIdentityCodec,
        "projection_path": FieldPath(segments=("thinking",)),
    },
    FieldPath(segments=("directives", "commit_attribution")): {
        "value_strategy": _directives_with_commit_attribution,
        "claude_codec": ClaudeDirectivesCodec,
        "codex_codec": CodexDirectivesCodec,
        "projection_path": FieldPath(segments=("commit_attribution",)),
    },
    FieldPath(segments=("directives", "system_prompt_file")): {
        "value_strategy": _directives_with_system_prompt_file,
        "claude_codec": ClaudeDirectivesCodec,
        "codex_codec": CodexDirectivesCodec,
        "projection_path": FieldPath(segments=("system_prompt_file",)),
    },
    FieldPath(segments=("capabilities", "plugins")): {
        "value_strategy": _capabilities_with_plugins,
        "claude_codec": ClaudeCapabilitiesCodec,
        "codex_codec": CodexCapabilitiesCodec,
        "projection_path": FieldPath(segments=("plugins",)),
    },
    FieldPath(segments=("capabilities", "plugin_marketplaces")): {
        "value_strategy": _capabilities_with_plugin_marketplaces,
        "claude_codec": ClaudeCapabilitiesCodec,
        "codex_codec": CodexCapabilitiesCodec,
        "projection_path": FieldPath(segments=("plugin_marketplaces",)),
    },
    FieldPath(segments=("capabilities", "mcp_servers")): {
        "value_strategy": _capabilities_with_mcp_servers,
        "claude_codec": ClaudeCapabilitiesCodec,
        "codex_codec": CodexCapabilitiesCodec,
        "projection_path": FieldPath(segments=("mcp_servers",)),
    },
    FieldPath(segments=("environment", "variables")): {
        "value_strategy": _environment_with_variables,
        "claude_codec": ClaudeEnvironmentCodec,
        "codex_codec": CodexEnvironmentCodec,
        "projection_path": FieldPath(segments=("variables",)),
    },
}


def _import_time_dispatch_completeness_check() -> None:
    """Fail fast at import time if the dispatch table drifts from the
    curated shared-path list.

    This is the symmetric guard to ``strategies._verify_cross_target_paths``:
    if a path is added to the curated list but no entry is wired here,
    the parametrized test would silently skip it. We refuse to let the
    cross-target safety net develop a hole that way.
    """
    curated = set(strats.cross_target_shared_paths())
    wired = set(PER_PATH.keys())
    missing = curated - wired
    extra = wired - curated
    if missing:
        msg = (
            f"PER_PATH is missing dispatch entries for paths in "
            f"strategies.cross_target_shared_paths(): "
            f"{sorted(p.render() for p in missing)}. "
            f"Add a value strategy + codec pair + projection path."
        )
        raise AssertionError(msg)
    if extra:
        msg = (
            f"PER_PATH has dispatch entries for paths NOT in "
            f"strategies.cross_target_shared_paths(): "
            f"{sorted(p.render() for p in extra)}. "
            f"Either remove them here or add them to the curated list."
        )
        raise AssertionError(msg)


_import_time_dispatch_completeness_check()


# Hypothesis settings tuned for parametrized fuzz: the default profile
# already caps examples at 50, but parametrize-fan-out times per-test
# cost would blow the local-smoke budget if we left the default deadline in
# place under unicode_torture's heavier draws. Suppressing
# `differing_executors` is necessary because pytest's parametrize ID
# becomes part of the test identifier and Hypothesis's per-example
# database keys off it; the suppression keeps the persistent DB
# usable across parametrization changes.
_PER_PATH_SETTINGS = settings(
    suppress_health_check=[
        HealthCheck.differing_executors,
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
        HealthCheck.filter_too_much,
    ],
)


# Parametrize over the curated path list. Using `cross_target_shared_paths()`
# directly (not the PER_PATH keys) makes the test fail loudly if a path
# is added without a dispatch entry — the import-time check above is the
# fast path; this is the runtime backstop.
_SHARED_PATHS = strats.cross_target_shared_paths()


# ----------------------------------------------------------------------
# Per-property xfail tables — one per property because different paths
# fail different properties (cwd loss fires on encode-symmetry and
# chain-symmetry but not idempotence; auto_update loss fires only on
# the partial-input lane check). Strict xfail so 's fixes flip
# the test red and we notice the moment the bug is gone.
# ----------------------------------------------------------------------

_XFAIL_ENCODE_SYMMETRY: dict[FieldPath, str] = {
    #  retired both former entries on this map:
    # - F-CWD never manifested under encode-symmetry (both lanes
    #   equally dropped McpServerStdio.cwd; after *
    #   both equally preserve, still symmetric).
    # - F-MP fix removed
    #   capabilities.plugin_marketplaces — the Codex codec preserves
    #   kind='github'/'url' and auto_update via Chameleon-namespaced
    #   extras. See tests/property/test_codex_marketplace_roundtrip.py.
}

_XFAIL_DECODE_SYMMETRY: dict[FieldPath, str] = {
    #  retired both former entries on this map:
    # - F-CWD on capabilities.mcp_servers:
    #   both _ClaudeMcpServerStdio and _CodexMcpServerStdio now carry
    #   cwd, so the chained property holds.
    # - F-MP-G on capabilities.plugin_marketplaces
    #  : the github -> git
    #   collapse no longer propagates through the Codex lane.
}

_XFAIL_IDEMPOTENCE: dict[FieldPath, str] = {
    #  F-MP fix removed ``capabilities.plugin_marketplaces`` —
    # the Codex encoder is now idempotent for ``kind='url'``
    # (``chameleon_kind`` tag survives the round-trip and the second
    # encode emits the same ``source_type=None``).
}

_XFAIL_PARTIAL_INPUT: dict[FieldPath, str] = {
    #  retired both former entries on this map:
    # - F-CWD on capabilities.mcp_servers.
    # - F-MP-G + F-AU on capabilities.plugin_marketplaces
    #  .
}


def _params_with_xfail(paths: list[FieldPath], xfail_map: dict[FieldPath, str]) -> list[Any]:
    """Build a parametrize-friendly list, applying strict xfail to the
    paths in ``xfail_map`` with the documented  reason.
    """
    out: list[Any] = []
    for p in paths:
        marks: list[Any] = []
        if p in xfail_map:
            marks.append(pytest.mark.xfail(strict=True, reason=xfail_map[p]))
        out.append(pytest.param(p, id=p.render(), marks=marks))
    return out


@pytest.mark.parametrize("path", _params_with_xfail(_SHARED_PATHS, _XFAIL_ENCODE_SYMMETRY))
def test_encode_symmetry(path: FieldPath) -> None:
    """Property 1 — Encode-symmetry under documented loss.

    ``decode(encode_claude(x)) == decode(encode_codex(x))`` projected at
    the shared path. Either lane may legitimately differ from the input
    along a documented-lossy axis (Codex drops Identity.thinking, Claude
    drops Codex-only identity tuning knobs, etc.); when the two lanes
    diverge, at least one side MUST have emitted a LossWarning. A
    silent divergence is the bug class this whole suite exists to find.
    """
    entry = PER_PATH[path]
    value_strategy = entry["value_strategy"]
    claude_codec = entry["claude_codec"]
    codex_codec = entry["codex_codec"]
    projection_path = entry["projection_path"]
    # `encode_symmetry_eq` defaults to plain `==` on the projected slice;
    # paths whose encode/decode shape is per-target by design override
    # it (see `identity.model`).
    eq_fn = entry.get("encode_symmetry_eq", lambda a, b: a == b)

    @given(value=value_strategy)
    @_PER_PATH_SETTINGS
    def _inner(value: Any) -> None:
        ctx_claude = TranspileCtx()
        claude_section = claude_codec.to_target(value, ctx_claude)
        recovered_claude = claude_codec.from_target(claude_section, ctx_claude)

        ctx_codex = TranspileCtx()
        codex_section = codex_codec.to_target(value, ctx_codex)
        recovered_codex = codex_codec.from_target(codex_section, ctx_codex)

        proj_claude = _project(recovered_claude, projection_path)
        proj_codex = _project(recovered_codex, projection_path)

        # Push the search toward inputs that exercise the path. A None
        # at the projected slice carries no signal for cross-target
        # comparison; rewarding non-None inputs nudges shrinking and
        # generation toward the cases the user actually cares about.
        hyp_target(1.0 if proj_claude is not None or proj_codex is not None else 0.0)

        if not eq_fn(proj_claude, proj_codex):
            warnings = list(ctx_claude.warnings) + list(ctx_codex.warnings)
            assert warnings, (
                f"SILENT cross-target divergence at {path.render()!r}:\n"
                f"  input value at path: {_project(value, projection_path)!r}\n"
                f"  Claude lane recovered: {proj_claude!r}\n"
                f"  Codex lane recovered: {proj_codex!r}\n"
                f"  no LossWarning emitted by either codec.\n"
                f"  HIGH-PRIORITY: this is the user's primary concern — "
                f"a valid settings input that fails to round-trip identically "
                f"between the two dialects without a documented loss."
            )

    _inner()


@pytest.mark.parametrize("path", _params_with_xfail(_SHARED_PATHS, _XFAIL_DECODE_SYMMETRY))
def test_decode_symmetry_via_cross_target(path: FieldPath) -> None:
    """Property 2 — Decode-symmetry across a cross-target hop.

    Encode through Claude, decode, then re-encode through Codex and
    decode again. Compare the projected slice on both sides of the
    hop. This probes the question "if Claude's lane writes something
    Codex's lane reads, does the operator intent survive intact?".

    A divergence here means: a value an operator put into Claude's
    settings, when promoted to neutral and pushed back out through
    Codex, loses meaning. Any divergence must again be witnessed by
    a LossWarning chain.
    """
    entry = PER_PATH[path]
    value_strategy = entry["value_strategy"]
    claude_codec = entry["claude_codec"]
    codex_codec = entry["codex_codec"]
    projection_path = entry["projection_path"]
    chain_eq = entry.get("chain_symmetry_eq", lambda a, b: a == b)

    @given(value=value_strategy)
    @_PER_PATH_SETTINGS
    def _inner(value: Any) -> None:
        ctx = TranspileCtx()

        # Claude lane -> recovered neutral.
        claude_section = claude_codec.to_target(value, ctx)
        recovered_via_claude = claude_codec.from_target(claude_section, ctx)

        # Now push the Claude-recovered neutral through the Codex lane.
        codex_section = codex_codec.to_target(recovered_via_claude, ctx)
        recovered_via_chain = codex_codec.from_target(codex_section, ctx)

        proj_input = _project(value, projection_path)
        proj_chain = _project(recovered_via_chain, projection_path)

        hyp_target(1.0 if proj_input is not None else 0.0)

        if not chain_eq(proj_input, proj_chain):
            assert ctx.warnings, (
                f"SILENT chain-divergence at {path.render()!r}:\n"
                f"  input value at path: {proj_input!r}\n"
                f"  after Claude->Codex chain: {proj_chain!r}\n"
                f"  no LossWarning emitted along the chain.\n"
                f"  HIGH-PRIORITY: a valid neutral value is being silently "
                f"reshaped by transit through the cross-target codec lanes."
            )

    _inner()


@pytest.mark.parametrize("path", _params_with_xfail(_SHARED_PATHS, _XFAIL_IDEMPOTENCE))
def test_idempotence_per_target(path: FieldPath) -> None:
    """Property 3 — Per-target idempotence baseline.

    ``encode(decode(encode(x))) == encode(x)`` for both Claude and
    Codex independently, projected at the shared path. This is the
    baseline against which the cross-target properties have meaning:
    if a single side is non-idempotent at the projected slice, then a
    cross-target divergence might be that bug, not a real cross-target
    issue. Asserting per-target idempotence first lets us trust the
    cross-target property when it fires.

    Comparison is on the section's `model_dump()` of the path slice,
    not on raw section equality, because some target sections have
    `extra="allow"` and accumulate auxiliary state across rounds that
    legitimately changes the section identity but not its semantics
    at the path.
    """
    entry = PER_PATH[path]
    value_strategy = entry["value_strategy"]
    claude_codec = entry["claude_codec"]
    codex_codec = entry["codex_codec"]
    projection_path = entry["projection_path"]

    @given(value=value_strategy)
    @_PER_PATH_SETTINGS
    def _inner(value: Any) -> None:
        for codec_name, codec in (("claude", claude_codec), ("codex", codex_codec)):
            ctx_a = TranspileCtx()
            section_a = codec.to_target(value, ctx_a)
            recovered = codec.from_target(section_a, ctx_a)

            ctx_b = TranspileCtx()
            section_b = codec.to_target(recovered, ctx_b)

            # Compare the projected SECTION slice — encode-on-encode
            # idempotence is what makes the round-trip oracle stable.
            # Use model_dump to elide `extra="allow"` accumulation that
            # is irrelevant to operator intent at this path.
            dump_a = section_a.model_dump(exclude_none=True)
            dump_b = section_b.model_dump(exclude_none=True)

            assert dump_a == dump_b, (
                f"NON-IDEMPOTENT {codec_name} encoder at {path.render()!r}:\n"
                f"  input projected: {_project(value, projection_path)!r}\n"
                f"  first encode: {dump_a!r}\n"
                f"  second encode: {dump_b!r}\n"
                f"  cross-target comparison results are unsafe to interpret "
                f"until per-target idempotence is restored."
            )

    _inner()


@pytest.mark.parametrize("path", _params_with_xfail(_SHARED_PATHS, _XFAIL_PARTIAL_INPUT))
def test_no_silent_divergence_on_partial_input(path: FieldPath) -> None:
    """Property 4 — No silent divergence on partial neutral inputs.

    The path-isolated strategies above leave most of the surrounding
    submodel at default. This test does the same property check but
    with a freshly-built default submodel where ONLY the path of
    interest is set — the most likely real-world shape, and the
    shape an operator who only cares about that one knob will write.

    If `encode_claude` and `encode_codex` of an operator-authored
    minimal input recover different neutrals at that path WITHOUT a
    LossWarning, that's the worst possible flavor of the user's
    primary concern: a config that LOOKS valid in both targets but
    silently means different things on each.
    """
    entry = PER_PATH[path]
    value_strategy = entry["value_strategy"]
    claude_codec = entry["claude_codec"]
    codex_codec = entry["codex_codec"]
    projection_path = entry["projection_path"]
    eq_fn = entry.get("encode_symmetry_eq", lambda a, b: a == b)
    chain_eq = entry.get("chain_symmetry_eq", lambda a, b: a == b)

    @given(value=value_strategy)
    @_PER_PATH_SETTINGS
    def _inner(value: Any) -> None:
        # Skip the trivially-empty case: an unset slice can't witness
        # a divergence.
        if _project(value, projection_path) is None:
            return

        ctx_claude = TranspileCtx()
        ctx_codex = TranspileCtx()
        claude_section = claude_codec.to_target(value, ctx_claude)
        codex_section = codex_codec.to_target(value, ctx_codex)
        recovered_claude = claude_codec.from_target(claude_section, ctx_claude)
        recovered_codex = codex_codec.from_target(codex_section, ctx_codex)

        proj_claude = _project(recovered_claude, projection_path)
        proj_codex = _project(recovered_codex, projection_path)
        proj_input = _project(value, projection_path)

        # Three-way property: each lane individually preserves the input
        # OR warns. The pair-equality (Claude == Codex) is property 1's
        # job; this is the per-lane "did your decode round-trip the
        # operator's value, or did you tell them why not?" check.
        for lane_name, ctx_lane, proj_lane in (
            ("claude", ctx_claude, proj_claude),
            ("codex", ctx_codex, proj_codex),
        ):
            # Use the lane-specific input projection. For per-target
            # paths like `identity.model`, the chain_symmetry_eq /
            # encode_symmetry_eq comparators define the lane-specific
            # equality semantics.
            target_key = BUILTIN_CLAUDE if lane_name == "claude" else BUILTIN_CODEX
            if path == FieldPath(segments=("identity", "model")):
                lane_eq = _per_target_model_projection_eq(
                    proj_input, proj_lane, target_key=target_key
                )
            else:
                lane_eq = proj_input == proj_lane
            if not lane_eq:
                assert ctx_lane.warnings, (
                    f"SILENT per-lane loss at {path.render()!r} "
                    f"({lane_name} lane):\n"
                    f"  operator input: {proj_input!r}\n"
                    f"  {lane_name} recovered: {proj_lane!r}\n"
                    f"  no LossWarning emitted by the {lane_name} codec.\n"
                    f"  HIGH-PRIORITY: a single codec is silently dropping "
                    f"or reshaping operator-authored input."
                )

        # Cross-lane equality: same as property 1 but on guaranteed-
        # non-empty inputs. Redundant under happy paths but kept as
        # the partial-input-specific failure trace.
        if not eq_fn(proj_claude, proj_codex):
            warnings = list(ctx_claude.warnings) + list(ctx_codex.warnings)
            assert warnings, (
                f"SILENT cross-lane divergence on partial input at "
                f"{path.render()!r}:\n"
                f"  operator input: {proj_input!r}\n"
                f"  Claude recovered: {proj_claude!r}\n"
                f"  Codex recovered: {proj_codex!r}\n"
                f"  no LossWarning emitted.\n"
                f"  HIGH-PRIORITY: minimal operator input means different "
                f"things on each side without an explanation."
            )

        # Suppress unused-variable lint on chain_eq for paths that don't
        # need it inside this property (the chain test uses it directly).
        _ = chain_eq

    _inner()
