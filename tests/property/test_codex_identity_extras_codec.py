"""P1-F: Codex-only identity tuning knobs.

The Codex exemplar at tests/fixtures/exemplar/home/_codex/config.toml has
three top-level identity tuning keys that Claude has no analogue for:

    model_context_window           -> identity.context_window
    model_auto_compact_token_limit -> identity.compact_threshold
    model_catalog_json             -> identity.model_catalog_path

Per the parity-gap doc P1-F section, these are real per-target identity
tuning knobs we promote to neutral as Codex-only fields. The Claude codec
must emit a LossWarning naming P1-F when neutral has any of these set.
"""

from __future__ import annotations

import tomlkit

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.identity import ClaudeIdentityCodec
from chameleon.codecs.codex.identity import CodexIdentityCodec, CodexIdentitySection
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains
from chameleon.schema.identity import Identity
from chameleon.targets.codex.assembler import CodexAssembler


def test_round_trip_context_window() -> None:
    orig = Identity(context_window=600000)
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(orig, ctx)
    assert section.model_context_window == 600000
    restored = CodexIdentityCodec.from_target(section, ctx)
    assert restored.context_window == 600000


def test_round_trip_compact_threshold() -> None:
    orig = Identity(compact_threshold=540000)
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(orig, ctx)
    assert section.model_auto_compact_token_limit == 540000
    restored = CodexIdentityCodec.from_target(section, ctx)
    assert restored.compact_threshold == 540000


def test_round_trip_model_catalog_path() -> None:
    path = "/Users/exampleuser/.codex/model-catalog-600k.json"
    orig = Identity(model_catalog_path=path)
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(orig, ctx)
    assert section.model_catalog_json == path
    restored = CodexIdentityCodec.from_target(section, ctx)
    assert restored.model_catalog_path == path


def test_round_trip_all_three_combined() -> None:
    orig = Identity(
        context_window=600000,
        compact_threshold=540000,
        model_catalog_path="/Users/exampleuser/.codex/model-catalog-600k.json",
    )
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(orig, ctx)
    restored = CodexIdentityCodec.from_target(section, ctx)
    assert restored.context_window == 600000
    assert restored.compact_threshold == 540000
    assert restored.model_catalog_path == "/Users/exampleuser/.codex/model-catalog-600k.json"


def test_disassemble_exemplar_populates_three_extras() -> None:
    """Disassembling the exemplar's config.toml routes the three P1-F keys
    into identity (not pass-through)."""
    doc = tomlkit.document()
    doc["model"] = "gpt-5.5"
    doc["model_reasoning_effort"] = "xhigh"
    doc["model_context_window"] = 600000
    doc["model_auto_compact_token_limit"] = 540000
    doc["model_catalog_json"] = "/Users/exampleuser/.codex/model-catalog-600k.json"
    raw = tomlkit.dumps(doc).encode("utf-8")

    domains, passthrough = CodexAssembler.disassemble({CodexAssembler.CONFIG_TOML: raw})
    assert Domains.IDENTITY in domains
    section = domains[Domains.IDENTITY]
    assert isinstance(section, CodexIdentitySection)
    assert section.model_context_window == 600000
    assert section.model_auto_compact_token_limit == 540000
    assert section.model_catalog_json == "/Users/exampleuser/.codex/model-catalog-600k.json"
    # And none of them leak to passthrough.
    leaked = {
        "model_context_window",
        "model_auto_compact_token_limit",
        "model_catalog_json",
    } & set(passthrough)
    assert not leaked, f"P1-F-claimed keys leaked to pass-through: {leaked}"


def test_claude_emits_loss_warning_for_context_window() -> None:
    ident = Identity(context_window=600000)
    ctx = TranspileCtx()
    ClaudeIdentityCodec.to_target(ident, ctx)
    matching = [w for w in ctx.warnings if "P1-F" in w.message and "context_window" in w.message]
    assert len(matching) == 1, f"expected one P1-F context_window LossWarning, got {ctx.warnings}"
    assert matching[0].domain == Domains.IDENTITY
    assert matching[0].target == BUILTIN_CLAUDE


def test_claude_emits_loss_warning_for_compact_threshold() -> None:
    ident = Identity(compact_threshold=540000)
    ctx = TranspileCtx()
    ClaudeIdentityCodec.to_target(ident, ctx)
    matching = [w for w in ctx.warnings if "P1-F" in w.message and "compact_threshold" in w.message]
    assert len(matching) == 1, (
        f"expected one P1-F compact_threshold LossWarning, got {ctx.warnings}"
    )
    assert matching[0].domain == Domains.IDENTITY
    assert matching[0].target == BUILTIN_CLAUDE


def test_claude_emits_loss_warning_for_model_catalog_path() -> None:
    ident = Identity(model_catalog_path="/tmp/catalog.json")
    ctx = TranspileCtx()
    ClaudeIdentityCodec.to_target(ident, ctx)
    matching = [
        w for w in ctx.warnings if "P1-F" in w.message and "model_catalog_path" in w.message
    ]
    assert len(matching) == 1, (
        f"expected one P1-F model_catalog_path LossWarning, got {ctx.warnings}"
    )
    assert matching[0].domain == Domains.IDENTITY
    assert matching[0].target == BUILTIN_CLAUDE


def test_claude_to_target_emits_no_warning_when_extras_unset() -> None:
    ident = Identity()
    ctx = TranspileCtx()
    ClaudeIdentityCodec.to_target(ident, ctx)
    p1f = [w for w in ctx.warnings if "P1-F" in w.message]
    assert p1f == [], f"unexpected P1-F warnings on empty Identity: {p1f}"


def test_codex_codec_claims_three_paths() -> None:
    """The codec's claimed_paths set must include the three new fields so
    the schema-drift check verifies them and the assembler routes correctly."""
    rendered = {p.render() for p in CodexIdentityCodec.claimed_paths}
    assert "model_context_window" in rendered
    assert "model_auto_compact_token_limit" in rendered
    assert "model_catalog_json" in rendered


def test_codex_extras_do_not_emit_warnings_on_codex_to_target() -> None:
    """Forward through the Codex codec is lossless for the three fields."""
    ident = Identity(
        context_window=600000,
        compact_threshold=540000,
        model_catalog_path="/Users/exampleuser/.codex/model-catalog-600k.json",
    )
    ctx = TranspileCtx()
    CodexIdentityCodec.to_target(ident, ctx)
    extras_warnings = [
        w
        for w in ctx.warnings
        if any(
            tok in w.message
            for tok in ("context_window", "compact_threshold", "model_catalog_path")
        )
    ]
    assert extras_warnings == [], f"unexpected warnings: {extras_warnings}"


def test_codex_codec_imports_dont_strip_unset() -> None:
    """from_target on a section with only the new fields produces an Identity
    where unrelated knobs (model, reasoning_effort) remain None."""
    section = CodexIdentitySection(
        model_context_window=600000,
        model_auto_compact_token_limit=540000,
        model_catalog_json="/Users/exampleuser/.codex/model-catalog-600k.json",
    )
    ctx = TranspileCtx()
    ident = CodexIdentityCodec.from_target(section, ctx)
    assert ident.context_window == 600000
    assert ident.compact_threshold == 540000
    assert ident.model_catalog_path == "/Users/exampleuser/.codex/model-catalog-600k.json"
    assert ident.model is None
    assert ident.reasoning_effort is None
    # BUILTIN_CODEX is referenced in disassemble + claim assertions above; the
    # explicit binding here documents that Identity is target-agnostic at this
    # field level (the values live as scalars, not per-target dicts).
    _ = BUILTIN_CODEX
