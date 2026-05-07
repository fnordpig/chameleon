"""FUZZ-2: assemblers MUST disassemble any valid wire instance without
raising.

The disassembler is the operator-facing entry point — when chameleon
reads a live ``settings.json`` or ``config.toml``, an unhandled
exception from ``disassemble`` would surface as a stack trace where
the operator expected a typed ``LossWarning``. The contract under
test:

    For every valid wire instance ``w`` of the target's full upstream
    model, ``Target.assembler.disassemble({path: dump(w)})`` returns a
    ``(per_domain, passthrough)`` pair without raising. Any
    per-section ``ValidationError`` MUST be caught by
    :func:`safe_validate_section` and re-routed via ``LossWarning``
    + pass-through; a malformed sub-table MUST NOT abort the whole
    disassemble (P0-2).

Two complementary input sources cover the wire surface:

1. :func:`st.builds` of the upstream-canonized full model
   (``ClaudeCodeSettings``, ``ConfigToml``). Pydantic's
   ``model_dump_json`` / dict-of-dicts gives us bytes that exercise
   the live shape with all upstream fields populated by their
   declared defaults — the "stock" instance.

2. A composite wire-bytes generator that synthesises the union of
   modelled section keys plus arbitrary unmodelled top-level keys
   (pass-through) plus deeply-nested unicode-bearing values. This
   exercises the validate-or-passthrough fallback rather than the
   stock-defaults path.

target() directives bias the search toward inputs whose serialised
form is large and toward higher unicode codepoints — those expand
the per-example state space at no cost to the assertion's strictness.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any

import pytest
from hypothesis import given, target
from hypothesis import strategies as st

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude._generated import ClaudeCodeSettings
from chameleon.codecs.codex._generated import ConfigToml
from chameleon.io.json import dump_json
from chameleon.io.toml import dump_toml
from chameleon.targets.claude import ClaudeTarget
from chameleon.targets.codex import CodexTarget

# Importing strategies wires the registrations conftest already loaded —
# the explicit re-import documents the dependency for readers.
from tests.fuzz import strategies as _strategies

pytestmark = pytest.mark.fuzz


# Settings.json's allowable top-level keys: claimed by codecs above,
# plus arbitrary extras that go to pass-through. The fuzzer must
# exercise BOTH so we know the disassembler tolerates unknown keys
# (the "extra=allow" fall-through on the upstream-canonized full
# model) and the codec section validators correctly reject malformed
# section bodies (the LossWarning lane).
_CLAUDE_CLAIMED_TOP_KEYS: tuple[str, ...] = (
    "model",
    "effortLevel",
    "alwaysThinkingEnabled",
    "outputStyle",
    "attribution",
    "includeCoAuthoredBy",
    "coauthoredBy",
    "gitAttribution",
    "env",
    "permissions",
    "sandbox",
    "cleanupPeriodDays",
    "hooks",
    "tui",
    "statusLine",
    "voice",
    "voiceEnabled",
    "prefersReducedMotion",
    "autoUpdatesChannel",
    "minimumVersion",
    "enabledPlugins",
    "extraKnownMarketplaces",
)

_CODEX_CLAIMED_TOP_KEYS: tuple[str, ...] = (
    "model",
    "model_reasoning_effort",
    "model_context_window",
    "model_auto_compact_token_limit",
    "model_catalog_json",
    "model_instructions_file",
    "commit_attribution",
    "personality",
    "mcp_servers",
    "plugins",
    "marketplaces",
    "shell_environment_policy",
    "sandbox_mode",
    "sandbox_workspace_write",
    "approvals_reviewer",
    "history",
    "tui",
    "file_opener",
    "features",
    "projects",
)


# ----------------------------------------------------------------------
# Source 1: full upstream model. ``st.builds`` constructs a
# ClaudeCodeSettings with all defaults — diversity comes from the
# section-level fuzzers in source 2; this lane locks in "the stock
# upstream instance must round-trip through disassemble" as a
# regression guard.
#
# ``ClaudeCodeSettings`` and ``ConfigToml`` cannot be auto-strategised
# via ``st.from_type`` because they contain ``dict[str, Any]`` fields
# (typing.Any has no runtime extension). ``st.builds(Cls)`` sidesteps
# that — Pydantic accepts ``Cls()`` because every field is optional —
# but the produced instances are uniform. That is a lower-coverage
# probe; source 2 is the diverse one.
# ----------------------------------------------------------------------


@st.composite
def _claude_full_model_bytes(draw: st.DrawFn) -> bytes:
    settings = draw(st.builds(ClaudeCodeSettings))
    # Pydantic's serializer sometimes warns about enum-vs-str
    # coercions when the upstream-canonized defaults round-trip; those
    # are upstream artifacts, not assembler bugs.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return settings.model_dump_json(by_alias=True).encode("utf-8")


@st.composite
def _codex_full_model_bytes(draw: st.DrawFn) -> bytes:
    config = draw(st.builds(ConfigToml))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        as_dict = config.model_dump(by_alias=True, exclude_none=True)
    # ConfigToml dumps to a dict that tomlkit can serialise. Keep the
    # exclude_none=True shape — a None value at the TOML layer would be
    # ambiguous (TOML has no null) so we drop them, matching the
    # assembler's emission discipline.
    return dump_toml(as_dict).encode("utf-8")


# ----------------------------------------------------------------------
# Source 2: synthesised wire dicts. A composite that draws optional
# section bodies plus arbitrary unmodelled extras at the top level.
# This is the diverse lane.
# ----------------------------------------------------------------------


@st.composite
def _claude_section_dict(draw: st.DrawFn) -> dict[str, Any]:
    """Build a settings.json-shaped dict from a SUBSET of codec section
    strategies plus arbitrary extras.

    Drawing every domain on every example is wasted work: the
    disassembler routes by top-level key, so a 2-3 section instance
    exercises the same code paths as an 8-section instance. We pick a
    random subset (size 0..3) so coverage spans empty dicts, single-
    section dicts, and small multi-section dicts. Extras land under
    unmodelled top-level keys to exercise the pass-through lane.
    """
    out: dict[str, Any] = {}
    section_names = draw(
        st.sets(
            st.sampled_from(
                [
                    "identity",
                    "directives",
                    "capabilities",
                    "environment",
                    "authorization",
                    "lifecycle",
                    "interface",
                    "governance",
                ]
            ),
            max_size=3,
        )
    )

    if "identity" in section_names:
        section = ClaudeTarget.codecs[0].to_target(  # type: ignore[attr-defined]
            draw(_strategies.identities), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True))
    if "directives" in section_names:
        section = ClaudeTarget.codecs[1].to_target(  # type: ignore[attr-defined]
            draw(_strategies.directives), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True, by_alias=True))
    if "capabilities" in section_names:
        section = ClaudeTarget.codecs[2].to_target(  # type: ignore[attr-defined]
            draw(_strategies.capabilities), TranspileCtx()
        )
        # Capabilities's mcpServers live in ``~/.claude.json``; only
        # ``enabledPlugins`` and ``extraKnownMarketplaces`` belong in
        # settings.json. Mirror the assembler's split.
        if section.enabled_plugins:
            out["enabledPlugins"] = dict(section.enabled_plugins)
        if section.extra_known_marketplaces:
            out["extraKnownMarketplaces"] = {
                k: v.model_dump(by_alias=True, exclude_none=True)
                for k, v in section.extra_known_marketplaces.items()
            }
    if "environment" in section_names:
        section = ClaudeTarget.codecs[3].to_target(  # type: ignore[attr-defined]
            draw(_strategies.environments), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True))
    if "authorization" in section_names:
        section = ClaudeTarget.codecs[4].to_target(  # type: ignore[attr-defined]
            draw(_strategies.authorizations), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True, exclude_defaults=True))
    if "lifecycle" in section_names:
        section = ClaudeTarget.codecs[5].to_target(  # type: ignore[attr-defined]
            draw(_strategies.lifecycles), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True))
    if "interface" in section_names:
        section = ClaudeTarget.codecs[6].to_target(  # type: ignore[attr-defined]
            draw(_strategies.interfaces), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True, exclude_defaults=True))
    if "governance" in section_names:
        section = ClaudeTarget.codecs[7].to_target(  # type: ignore[attr-defined]
            draw(_strategies.governances), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True))

    extras = draw(
        st.dictionaries(
            keys=st.from_regex(r"\A[A-Za-z][A-Za-z0-9_]{0,15}\Z", fullmatch=True).filter(
                lambda k: k not in _CLAUDE_CLAIMED_TOP_KEYS
            ),
            values=_strategies.json_value,
            max_size=4,
        )
    )
    out.update(extras)
    return out


@st.composite
def _codex_section_dict(draw: st.DrawFn) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
    """Mirror of :func:`_claude_section_dict` for Codex's TOML side.

    Same subset-of-domains discipline so per-example cost stays
    bounded — the disassembler routes by top-level key, so a 0-3
    section instance covers the same code paths as a fully-populated
    one without doing 8x the strategy work.
    """
    out: dict[str, Any] = {}
    section_names = draw(
        st.sets(
            st.sampled_from(
                [
                    "identity",
                    "directives",
                    "capabilities",
                    "environment",
                    "authorization",
                    "lifecycle",
                    "interface",
                    "governance",
                ]
            ),
            max_size=3,
        )
    )

    if "identity" in section_names:
        section = CodexTarget.codecs[0].to_target(  # type: ignore[attr-defined]
            draw(_strategies.identities), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True))
    if "directives" in section_names:
        section = CodexTarget.codecs[1].to_target(  # type: ignore[attr-defined]
            draw(_strategies.directives), TranspileCtx()
        )
        out.update(section.model_dump(exclude_none=True))
    if "capabilities" in section_names:
        section = CodexTarget.codecs[2].to_target(  # type: ignore[attr-defined]
            draw(_strategies.capabilities), TranspileCtx()
        )
        if section.mcp_servers:
            out["mcp_servers"] = {
                k: v.model_dump(exclude_none=True) for k, v in section.mcp_servers.items()
            }
        if section.plugins:
            out["plugins"] = {
                k: v.model_dump(exclude_none=True) for k, v in section.plugins.items()
            }
        if section.marketplaces:
            out["marketplaces"] = {
                k: v.model_dump(exclude_none=True) for k, v in section.marketplaces.items()
            }
    if "environment" in section_names:
        section = CodexTarget.codecs[3].to_target(  # type: ignore[attr-defined]
            draw(_strategies.environments), TranspileCtx()
        )
        sep = section.shell_environment_policy.model_dump(exclude_none=True)
        if sep:
            out["shell_environment_policy"] = sep
    if "authorization" in section_names:
        section = CodexTarget.codecs[4].to_target(  # type: ignore[attr-defined]
            draw(_strategies.authorizations), TranspileCtx()
        )
        if section.sandbox_mode is not None:
            out["sandbox_mode"] = section.sandbox_mode
        ws = section.sandbox_workspace_write.model_dump(exclude_none=True)
        if ws and ws.get("writable_roots"):
            out["sandbox_workspace_write"] = ws
        if section.approvals_reviewer is not None:
            out["approvals_reviewer"] = section.approvals_reviewer
    if "lifecycle" in section_names:
        section = CodexTarget.codecs[5].to_target(  # type: ignore[attr-defined]
            draw(_strategies.lifecycles), TranspileCtx()
        )
        history_dump = section.history.model_dump(exclude_none=True)
        if history_dump:
            out["history"] = history_dump
    if "interface" in section_names:
        section = CodexTarget.codecs[6].to_target(  # type: ignore[attr-defined]
            draw(_strategies.interfaces), TranspileCtx()
        )
        tui_dump = section.tui.model_dump(exclude_none=True)
        if tui_dump:
            out["tui"] = tui_dump
        if section.file_opener is not None:
            out["file_opener"] = section.file_opener
    if "governance" in section_names:
        section = CodexTarget.codecs[7].to_target(  # type: ignore[attr-defined]
            draw(_strategies.governances), TranspileCtx()
        )
        if section.features:
            out["features"] = dict(section.features)
        if section.projects:
            out["projects"] = {
                p: proj.model_dump(exclude_none=True) for p, proj in section.projects.items()
            }

    # Codex extras must be TOML-compatible at the wire. We still draw
    # a JsonValue strategy but constrain extras to scalar values (TOML
    # forbids null and is fussier about heterogeneous lists).
    extras = draw(
        st.dictionaries(
            keys=st.from_regex(r"\A[a-z][a-z0-9_]{0,15}\Z", fullmatch=True).filter(
                lambda k: k not in _CODEX_CLAIMED_TOP_KEYS
            ),
            values=st.one_of(
                st.booleans(),
                st.integers(min_value=-(2**31), max_value=2**31 - 1),
                st.floats(allow_nan=False, allow_infinity=False, width=32),
                st.text(max_size=64),
            ),
            max_size=4,
        )
    )
    out.update(extras)
    return out


# ----------------------------------------------------------------------
# target() helpers — bias the search toward harder examples.
# ----------------------------------------------------------------------


def _max_unicode_codepoint(payload: bytes) -> float:
    """Highest unicode codepoint observed in the decoded payload.

    Hypothesis biases the search toward larger metric values, so a
    high codepoint pushes the SMP / RTL / combining-mark coverage up.
    Returns 0.0 for empty or undecodable payloads (the assembler
    handles UTF-8 only, but we try to decode for the metric).
    """
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return 0.0
    if not text:
        return 0.0
    return float(max(ord(c) for c in text))


def _payload_size(payload: bytes) -> float:
    return float(len(payload))


# ----------------------------------------------------------------------
# Test cases.
# ----------------------------------------------------------------------


@given(payload=_claude_full_model_bytes())
def test_claude_full_model_disassembles(payload: bytes) -> None:
    """``ClaudeAssembler.disassemble`` accepts the upstream-default
    instance without raising. This locks in the regression guard for
    "stock ClaudeCodeSettings serialisation always parses".
    """
    target(_payload_size(payload), label="claude_full_size")
    files = {ClaudeTarget.assembler.SETTINGS_JSON: payload}  # type: ignore[attr-defined]
    ctx = TranspileCtx()
    per_domain, passthrough = ClaudeTarget.assembler.disassemble(files, ctx=ctx)
    _assert_shape(per_domain, passthrough)


@given(payload=_codex_full_model_bytes())
def test_codex_full_model_disassembles(payload: bytes) -> None:
    """``CodexAssembler.disassemble`` accepts the upstream-default
    instance without raising. Mirror of the Claude full-model lane.
    """
    target(_payload_size(payload), label="codex_full_size")
    files = {CodexTarget.assembler.CONFIG_TOML: payload}  # type: ignore[attr-defined]
    ctx = TranspileCtx()
    per_domain, passthrough = CodexTarget.assembler.disassemble(files, ctx=ctx)
    _assert_shape(per_domain, passthrough)


@given(wire=_claude_section_dict())
def test_claude_section_dict_disassembles(wire: dict[str, Any]) -> None:
    """Synthesised settings.json bytes (modelled sections + arbitrary
    extras) MUST disassemble.

    Adversarial axis: extras at the top level land in pass-through;
    section bodies span the full domain coverage. Validation errors
    inside any section MUST be caught by ``safe_validate_section``
    and surfaced as a typed LossWarning, never as an unhandled
    exception.
    """
    payload = dump_json(wire).encode("utf-8")
    target(_payload_size(payload), label="claude_section_size")
    target(_max_unicode_codepoint(payload), label="claude_section_codepoint")
    files = {ClaudeTarget.assembler.SETTINGS_JSON: payload}  # type: ignore[attr-defined]
    ctx = TranspileCtx()
    per_domain, passthrough = ClaudeTarget.assembler.disassemble(files, ctx=ctx)
    _assert_shape(per_domain, passthrough)


@given(wire=_codex_section_dict())
def test_codex_section_dict_disassembles(wire: dict[str, Any]) -> None:
    """Synthesised config.toml bytes (modelled sections + extras) MUST
    disassemble. Mirror of the Claude section-dict lane for Codex's
    TOML path.
    """
    payload = dump_toml(wire).encode("utf-8")
    target(_payload_size(payload), label="codex_section_size")
    target(_max_unicode_codepoint(payload), label="codex_section_codepoint")
    files = {CodexTarget.assembler.CONFIG_TOML: payload}  # type: ignore[attr-defined]
    ctx = TranspileCtx()
    per_domain, passthrough = CodexTarget.assembler.disassemble(files, ctx=ctx)
    _assert_shape(per_domain, passthrough)


def _assert_shape(per_domain: Mapping[Any, Any], passthrough: Mapping[str, Any]) -> None:
    """Sanity: ``disassemble`` returns the documented pair shape.

    The pair contract is more important than the contents — the
    fuzzer's job is to surface crashes, not to assert codec
    behaviour. The shape check here catches a class of "returned
    None" / "returned a single value" regressions cheaply.
    """
    assert isinstance(per_domain, Mapping), f"per_domain is {type(per_domain)!r}, want Mapping"
    assert isinstance(passthrough, Mapping), f"passthrough is {type(passthrough)!r}, want Mapping"
