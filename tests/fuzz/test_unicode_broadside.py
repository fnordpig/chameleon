"""FUZZ-6 — Unicode broadside (Wave-9).

Wave-5 B4 fixed a single em-dash escape bug in
:func:`chameleon.state.locks.partial_owned_write` (the partial-owned
JSON write path was passing user content through stdlib :func:`json.dumps`
without ``ensure_ascii=False``). That fix flipped one switch on one
write path; it is not, by itself, evidence that every other string-bearing
field in chameleon survives a hostile-Unicode round-trip.

This module exercises end-to-end Unicode handling across:

1. **Codec round-trip** for the string-bearing fields in the three
   domains where the parity-gap doc puts the most operator-authored
   content (identity, directives, environment), driven by both the
   Claude and Codex codec lanes.
2. **I/O serializer round-trip** for the three on-disk encodings
   chameleon emits (JSON, YAML, TOML) — these are the layers that
   carry the actual escape semantics, and any regression here would
   reproduce B4 elsewhere.

The Unicode strategies here are local to this test (the constraint is:
do not modify ``strategies.py``). They draw from the same blocks as
:func:`tests.fuzz.strategies.unicode_torture` — BMP, SMP/astral,
RTL, combining marks, variation selectors — but they are tagged with
:func:`hypothesis.target` calls so the search biases toward
high-codepoint and combining-mark-dense examples. That is the part
of the input space where naive normalisation, escape, and width
assumptions break.

Surrogates are intentionally excluded: Python's ``str`` cannot legally
hold an unpaired surrogate (Cs category) and Hypothesis's
``st.characters()`` excludes them by default. Surrogate-related
crashes that surface in JSON serialisation are bugs in the *serializer*
(and in the user code that synthesised an unpaired surrogate), not in
chameleon — so we do not generate them.

A genuine Unicode round-trip failure detected here is a Wave-11
finding (B4-class regression) and should be reported with the failing
example, the codec / serializer involved, and the codepoint ranges
implicated.
"""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import HealthCheck, given, settings, target
from hypothesis import strategies as st

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.directives import ClaudeDirectivesCodec
from chameleon.codecs.claude.environment import ClaudeEnvironmentCodec
from chameleon.codecs.claude.identity import ClaudeIdentityCodec
from chameleon.codecs.codex.directives import CodexDirectivesCodec
from chameleon.codecs.codex.environment import CodexEnvironmentCodec
from chameleon.codecs.codex.identity import CodexIdentityCodec
from chameleon.io.json import dump_json, load_json
from chameleon.io.toml import dump_toml, load_toml
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.directives import Directives
from chameleon.schema.environment import Environment
from chameleon.schema.identity import Identity

pytestmark = pytest.mark.fuzz


# ----------------------------------------------------------------------
# Profile-aware caps. The ``fuzz`` profile (nightly, 500 examples, 10s
# deadline) exercises longer strings; the ``default`` profile (50
# examples, 200ms) keeps strings short so the smoke pass stays under
# the deadline. Detection is by ``settings().max_examples`` because
# that is the field the conftest uses to discriminate the two profiles
# and reading it avoids coupling to the profile-name string.
# ----------------------------------------------------------------------

_FUZZ_MAX_EXAMPLES_THRESHOLD: int = 200
_DEFAULT_PROFILE_STRING_CAP: int = 64
_FUZZ_PROFILE_STRING_CAP: int = 256


def _string_cap() -> int:
    """Return the per-string codepoint cap for the active Hypothesis profile."""
    return (
        _FUZZ_PROFILE_STRING_CAP
        if settings().max_examples >= _FUZZ_MAX_EXAMPLES_THRESHOLD
        else _DEFAULT_PROFILE_STRING_CAP
    )


# ----------------------------------------------------------------------
# Targeting metrics — these are what :func:`hypothesis.target` biases
# the search toward. ``_max_codepoint`` pushes examples into SMP/astral
# territory; ``_combining_density`` pushes them toward strings made up
# largely of combining marks (Mn/Mc/Me). Together they drive coverage
# into the regions that historically broke escape and width logic.
# ----------------------------------------------------------------------


def _max_codepoint(s: str) -> int:
    """Largest codepoint in ``s``; 0 for the empty string."""
    return max((ord(c) for c in s), default=0)


def _combining_density(s: str) -> int:
    """Count of combining marks (Mn/Mc/Me) in ``s``.

    Returned as ``int`` (not a ratio) because :func:`hypothesis.target`
    accepts ``int | float`` and an absolute count gives Hypothesis a
    monotonic objective even when the string is short.
    """
    return sum(1 for c in s if unicodedata.category(c) in {"Mn", "Mc", "Me"})


# ----------------------------------------------------------------------
# Local Unicode-broadside text strategies. These mirror the building
# blocks in :func:`tests.fuzz.strategies.unicode_torture` but compose
# them in a single text strategy keyed off the active profile's cap.
#
# We deliberately *do not* import ``unicode_torture`` from
# ``strategies.py`` — that strategy concatenates 1..4 chunks of up to
# ``MAX_STRING_SIZE`` each and clips the result, which gives us no
# control over the per-test cap. The strategies below honour
# :func:`_string_cap` directly.
# ----------------------------------------------------------------------


def _bmp_text(max_size: int) -> st.SearchStrategy[str]:
    """BMP minus surrogates and ASCII control range."""
    return st.text(
        alphabet=st.characters(
            min_codepoint=0x0020,
            max_codepoint=0xFFFF,
            blacklist_categories=("Cs",),
        ),
        max_size=max_size,
    )


def _smp_text(max_size: int) -> st.SearchStrategy[str]:
    """SMP/astral plane (emoji, historic scripts).

    Capped at U+1FFFF rather than U+10FFFF — the higher private-use
    planes contain unallocated codepoints that hit Hypothesis's
    ``Cn`` filter ~all the time and starve the strategy.
    """
    return st.text(
        alphabet=st.characters(
            min_codepoint=0x10000,
            max_codepoint=0x1FFFF,
            blacklist_categories=("Cs", "Cn"),
        ),
        max_size=max_size,
    )


def _rtl_text(max_size: int) -> st.SearchStrategy[str]:
    """Hebrew, Arabic, Syriac, NKo, Samaritan, Mandaic, Thaana."""
    return st.text(
        alphabet=st.characters(
            min_codepoint=0x0590,
            max_codepoint=0x08FF,
            blacklist_categories=("Cs",),
        ),
        max_size=max_size,
    )


_COMBINING_MARKS: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(whitelist_categories=("Mn", "Mc", "Me")),
    min_size=1,
    max_size=8,
)

_VARIATION_SELECTORS: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(min_codepoint=0xFE00, max_codepoint=0xFE0F),
    min_size=1,
    max_size=4,
)


@st.composite
def unicode_broadside_text(draw: st.DrawFn, max_size: int | None = None) -> str:
    """Adversarial Unicode text drawn from BMP, SMP, RTL, combining
    marks, and variation selectors.

    Each draw concatenates 1..4 chunks from the sub-strategies and
    truncates to the profile-aware cap. The result is well-formed
    Unicode (no lone surrogates) and intentionally exercises the
    portions of the codepoint space that historically broke
    chameleon's I/O.
    """
    cap = max_size if max_size is not None else _string_cap()
    n_chunks = draw(st.integers(min_value=1, max_value=4))
    # Per-chunk cap: divide the budget so concatenation can't overshoot
    # by more than the small base-character padding combining/variation
    # chunks add. The final ``[:cap]`` is the hard guarantee.
    per_chunk = max(1, cap // n_chunks)
    parts: list[str] = []
    for _ in range(n_chunks):
        kind = draw(st.sampled_from(["bmp", "smp", "rtl", "combining", "variation", "ascii"]))
        if kind == "bmp":
            parts.append(draw(_bmp_text(per_chunk)))
        elif kind == "smp":
            parts.append(draw(_smp_text(per_chunk)))
        elif kind == "rtl":
            parts.append(draw(_rtl_text(per_chunk)))
        elif kind == "combining":
            base = draw(st.characters(min_codepoint=0x0041, max_codepoint=0x007A))
            parts.append(base + draw(_COMBINING_MARKS))
        elif kind == "variation":
            base = draw(st.characters(min_codepoint=0x2600, max_codepoint=0x26FF))
            parts.append(base + draw(_VARIATION_SELECTORS))
        else:  # ascii — keeps shrinking informative
            parts.append(draw(st.text(alphabet="abc—é日🜲", max_size=per_chunk)))
    return "".join(parts)[:cap]


def _bias_targets(*strings: str) -> None:
    """Apply the two ``target`` calls used across the suite.

    Hypothesis enforces one ``target(...)`` call per ``label`` per
    example, so this helper aggregates across every string in the
    example: it reports the *maximum* codepoint and the *sum* of
    combining-mark counts. Both aggregates are monotonic — the search
    will still climb toward higher individual codepoints and denser
    combining-mark strings — and each label appears exactly once per
    example.
    """
    if not strings:
        return
    target(float(max(_max_codepoint(s) for s in strings)), label="max_codepoint")
    target(
        float(sum(_combining_density(s) for s in strings)),
        label="combining_density",
    )


# ----------------------------------------------------------------------
# Codec round-trip — Identity domain (Claude lane).
# ----------------------------------------------------------------------


# Per-test settings: relax the per-example deadline for these specific
# tests because the full BMP/SMP draw plus a Pydantic round-trip can
# brush against the default profile's 200ms ceiling on a busy CI
# runner. The fuzz profile already has a 10s deadline so the override
# is functionally a no-op there.
_PER_TEST_SETTINGS = settings(
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@_PER_TEST_SETTINGS
@given(
    service_tier=unicode_broadside_text(),
    claude_model=unicode_broadside_text(),
    api_key_helper=unicode_broadside_text(),
)
def test_identity_unicode_round_trip_claude(
    service_tier: str,
    claude_model: str,
    api_key_helper: str,
) -> None:
    """Identity codec (Claude lane) preserves Unicode in the fields it claims.

    The Claude codec carries ``model[claude]`` and the (Codex-only)
    fields warn-and-drop. We only assert preservation of the
    Claude-claimed axis (``model[claude]``); ``service_tier`` and
    ``api_key_helper`` ride along to give Hypothesis non-trivial
    surface area to target on, but the Claude codec does not write
    them out (they are Codex-only / nested under ``auth``), so we do
    not assert them here.
    """
    _bias_targets(service_tier, claude_model, api_key_helper)
    orig = Identity(
        service_tier=service_tier or None,
        model={BUILTIN_CLAUDE: claude_model},
    )
    ctx = TranspileCtx()
    section = ClaudeIdentityCodec.to_target(orig, ctx)
    recovered = ClaudeIdentityCodec.from_target(section, ctx)
    assert recovered.model is not None
    assert recovered.model[BUILTIN_CLAUDE] == claude_model


@_PER_TEST_SETTINGS
@given(
    service_tier=unicode_broadside_text(),
    codex_model=unicode_broadside_text(),
    catalog_path=unicode_broadside_text(),
)
def test_identity_unicode_round_trip_codex(
    service_tier: str,
    codex_model: str,
    catalog_path: str,
) -> None:
    """Identity codec (Codex lane) preserves Unicode in claimed fields.

    Codex claims ``model[codex]`` and ``model_catalog_path``. Both
    are checked through the round-trip.
    """
    _bias_targets(service_tier, codex_model, catalog_path)
    orig = Identity(
        service_tier=service_tier or None,
        model={BUILTIN_CODEX: codex_model},
        model_catalog_path=catalog_path or None,
    )
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(orig, ctx)
    recovered = CodexIdentityCodec.from_target(section, ctx)
    assert recovered.model is not None
    assert recovered.model[BUILTIN_CODEX] == codex_model
    assert recovered.model_catalog_path == (catalog_path or None)


# ----------------------------------------------------------------------
# Codec round-trip — Directives domain.
# ----------------------------------------------------------------------


@_PER_TEST_SETTINGS
@given(
    system_prompt_file=unicode_broadside_text(),
    commit_attribution=unicode_broadside_text(),
)
def test_directives_unicode_round_trip_claude(
    system_prompt_file: str,
    commit_attribution: str,
) -> None:
    """Claude directives codec preserves Unicode in claimed string fields."""
    _bias_targets(system_prompt_file, commit_attribution)
    # ``commit_attribution=""`` is the documented "hide" sentinel; both
    # empty and non-empty strings must round-trip unchanged.
    orig = Directives(
        system_prompt_file=system_prompt_file or None,
        commit_attribution=commit_attribution,
    )
    ctx = TranspileCtx()
    section = ClaudeDirectivesCodec.to_target(orig, ctx)
    recovered = ClaudeDirectivesCodec.from_target(section, ctx)
    assert recovered.system_prompt_file == orig.system_prompt_file
    assert recovered.commit_attribution == orig.commit_attribution


@_PER_TEST_SETTINGS
@given(
    system_prompt_file=unicode_broadside_text(),
    commit_attribution=unicode_broadside_text(),
)
def test_directives_unicode_round_trip_codex(
    system_prompt_file: str,
    commit_attribution: str,
) -> None:
    """Codex directives codec preserves Unicode in claimed string fields."""
    _bias_targets(system_prompt_file, commit_attribution)
    orig = Directives(
        system_prompt_file=system_prompt_file or None,
        commit_attribution=commit_attribution,
    )
    ctx = TranspileCtx()
    section = CodexDirectivesCodec.to_target(orig, ctx)
    recovered = CodexDirectivesCodec.from_target(section, ctx)
    assert recovered.system_prompt_file == orig.system_prompt_file
    assert recovered.commit_attribution == orig.commit_attribution


# ----------------------------------------------------------------------
# Codec round-trip — Environment.variables.
#
# This is the highest-leverage test in the file: ``variables`` is a
# ``dict[str, str]`` where both keys and values are operator-authored
# free-form text. Real configs have UTF-8 in both (e.g. localized
# greeting envvars, file-path values containing CJK). The strategy
# below caps the dict at four entries to keep examples cheap; the
# value cap matches the profile-aware string cap.
# ----------------------------------------------------------------------


def _env_dict_strategy() -> st.SearchStrategy[dict[str, str]]:
    return st.dictionaries(
        keys=unicode_broadside_text(max_size=min(_string_cap(), 32)),
        values=unicode_broadside_text(),
        max_size=4,
    )


@_PER_TEST_SETTINGS
@given(variables=_env_dict_strategy())
def test_environment_variables_unicode_round_trip_claude(
    variables: dict[str, str],
) -> None:
    """Claude environment codec preserves Unicode in env keys and values."""
    _bias_targets(*variables.keys(), *variables.values())
    orig = Environment(variables=variables)
    ctx = TranspileCtx()
    section = ClaudeEnvironmentCodec.to_target(orig, ctx)
    recovered = ClaudeEnvironmentCodec.from_target(section, ctx)
    assert recovered.variables == orig.variables


@_PER_TEST_SETTINGS
@given(variables=_env_dict_strategy())
def test_environment_variables_unicode_round_trip_codex(
    variables: dict[str, str],
) -> None:
    """Codex environment codec preserves Unicode in env keys and values."""
    _bias_targets(*variables.keys(), *variables.values())
    orig = Environment(variables=variables)
    ctx = TranspileCtx()
    section = CodexEnvironmentCodec.to_target(orig, ctx)
    recovered = CodexEnvironmentCodec.from_target(section, ctx)
    assert recovered.variables == orig.variables


# ----------------------------------------------------------------------
# I/O round-trip — JSON, YAML, TOML.
#
# These tests are the analogue of B4 for the *other* serializers.
# B4 was specifically about JSON; YAML and TOML have their own
# escape-and-encoding machinery (ruamel.yaml's flow scalar quoting,
# tomlkit's basic-vs-literal-string heuristic) that we want to put
# under the same hostile-input pressure.
#
# The leaf value is a Unicode-broadside string. Keys are also drawn
# from the broadside but capped tighter so dict shape doesn't dominate.
# ----------------------------------------------------------------------


def _io_dict_strategy() -> st.SearchStrategy[dict[str, str]]:
    return st.dictionaries(
        keys=unicode_broadside_text(max_size=min(_string_cap(), 32)),
        values=unicode_broadside_text(),
        max_size=4,
    )


@_PER_TEST_SETTINGS
@given(payload=_io_dict_strategy())
def test_io_json_unicode_round_trip(payload: dict[str, str]) -> None:
    """``dump_json`` -> ``load_json`` preserves Unicode keys and values.

    The B4 fix ensured ``dump_json`` writes ``ensure_ascii=False``;
    this test re-asserts the invariant via the public I/O surface so
    a future regression on that switch is caught here rather than at
    a downstream layer.
    """
    _bias_targets(*payload.keys(), *payload.values())
    text = dump_json(payload)
    recovered = load_json(text)
    assert recovered == payload


@_PER_TEST_SETTINGS
@given(payload=_io_dict_strategy())
def test_io_yaml_unicode_round_trip(payload: dict[str, str]) -> None:
    """``dump_yaml`` -> ``load_yaml`` preserves Unicode keys and values.

    ruamel.yaml's round-trip mode quotes scalars containing characters
    it considers ambiguous; this verifies the quoting (and any
    subsequent ``allow_unicode``-class settings) does not corrupt the
    payload across the round trip.
    """
    _bias_targets(*payload.keys(), *payload.values())
    text = dump_yaml(payload)
    recovered = load_yaml(text)
    # ruamel.yaml returns CommentedMap; equality with plain dict works
    # because CommentedMap subclasses dict.
    assert recovered == payload


@_PER_TEST_SETTINGS
@given(payload=_io_dict_strategy())
def test_io_toml_unicode_round_trip(payload: dict[str, str]) -> None:
    """``dump_toml`` -> ``load_toml`` preserves Unicode keys and values.

    TOML is the most restrictive of the three serializers — bare keys
    are ASCII-only, so any non-ASCII key must be emitted as a quoted
    key. This test ensures tomlkit does the right thing under
    Unicode-broadside input.

    Filter: TOML disallows ASCII control characters (U+0000..U+001F
    minus tab/newline) in *strings*; chameleon's I/O is already
    constrained to UTF-8 text, but the broadside strategy starts at
    U+0020 so this is structurally avoided.
    """
    _bias_targets(*payload.keys(), *payload.values())
    # ``dump_toml`` accepts ``dict[str, object]``; widen explicitly so
    # the type-checker accepts the call without coupling the test
    # signature to ``object``-typed values.
    widened: dict[str, object] = dict(payload)
    text = dump_toml(widened)
    recovered = load_toml(text)
    # tomlkit returns a TOMLDocument; equality with a plain dict works
    # because TOMLDocument exposes a dict-like interface.
    assert dict(recovered) == payload
