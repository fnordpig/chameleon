"""P1-E — directives.personality is a first-class neutral concept.

The Codex exemplar has a top-level ``personality = "pragmatic"`` key.
Pre-P1-E, the Codex directives codec did not claim it; it landed in
pass-through. Claude has no equivalent concept.

P1-E promotes ``personality`` to a typed neutral schema field
(``directives.personality``) backed by a fixed-vocabulary enum that
mirrors Codex's upstream-canonized ``Personality`` StrEnum
(``none``, ``friendly``, ``pragmatic``). The enum modelling — rather
than a free string — is justified by the upstream schema: Codex's
generated model rejects values outside that set, so a free string would
permit neutral configurations that fail to round-trip into Codex.

The Claude codec, having no equivalent, emits a ``LossWarning``
referencing P1-E when ``directives.personality`` is set in neutral.
"""

from __future__ import annotations

from pathlib import Path

from chameleon._types import FieldPath
from chameleon.codecs._protocol import TranspileCtx, validate_claimed_paths
from chameleon.codecs.claude.directives import (
    ClaudeDirectivesCodec,
    ClaudeDirectivesSection,
)
from chameleon.codecs.codex import CodexConfig
from chameleon.codecs.codex.directives import (
    CodexDirectivesCodec,
    CodexDirectivesSection,
)
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.directives import Directives, Personality
from chameleon.targets.codex.assembler import CodexAssembler

FIXTURE_HOME = Path(__file__).parent.parent / "fixtures" / "exemplar" / "home"


# -- Codex round-trip ---------------------------------------------------------


def test_codex_round_trip_pragmatic() -> None:
    orig = Directives(personality=Personality.PRAGMATIC)
    ctx = TranspileCtx()
    section = CodexDirectivesCodec.to_target(orig, ctx)
    restored = CodexDirectivesCodec.from_target(section, ctx)
    assert restored.personality == Personality.PRAGMATIC
    assert ctx.warnings == []


def test_codex_round_trip_friendly() -> None:
    orig = Directives(personality=Personality.FRIENDLY)
    ctx = TranspileCtx()
    section = CodexDirectivesCodec.to_target(orig, ctx)
    restored = CodexDirectivesCodec.from_target(section, ctx)
    assert restored.personality == Personality.FRIENDLY


def test_codex_round_trip_none_value() -> None:
    """``Personality.NONE`` is a real upstream value (the literal string
    ``"none"``, distinct from a Python ``None``/absent field). It must
    survive round-trip without collapsing to absence.
    """
    orig = Directives(personality=Personality.NONE)
    ctx = TranspileCtx()
    section = CodexDirectivesCodec.to_target(orig, ctx)
    restored = CodexDirectivesCodec.from_target(section, ctx)
    assert restored.personality == Personality.NONE


def test_codex_unset_personality_round_trips_as_unset() -> None:
    orig = Directives()
    ctx = TranspileCtx()
    section = CodexDirectivesCodec.to_target(orig, ctx)
    restored = CodexDirectivesCodec.from_target(section, ctx)
    assert restored.personality is None


# -- Exemplar disassembly -----------------------------------------------------


def test_exemplar_codex_personality_populates_directives() -> None:
    """The exemplar fixture has ``personality = "pragmatic"`` at the top
    level of ``~/.codex/config.toml``. After P1-E, that value MUST land
    inside the directives section, not pass-through.
    """
    config_bytes = (FIXTURE_HOME / "_codex" / "config.toml").read_bytes()
    domains, passthrough = CodexAssembler.disassemble({CodexAssembler.CONFIG_TOML: config_bytes})
    assert "personality" not in passthrough, (
        "personality leaked to pass-through; CodexAssembler.directives_keys "
        "did not route it after P1-E"
    )
    assert Domains.DIRECTIVES in domains
    section = domains[Domains.DIRECTIVES]
    assert isinstance(section, CodexDirectivesSection)
    assert section.personality == Personality.PRAGMATIC


# -- Schema-drift: claimed_paths resolves cleanly -----------------------------


def test_codex_personality_claimed_path_resolves_against_full_model() -> None:
    """The newly claimed ``personality`` path must resolve to a real field
    in the generated upstream model. This is the same gate the schema-drift
    suite enforces; the per-codec assertion here makes a regression fail
    closer to the relevant edit.
    """
    assert FieldPath(segments=("personality",)) in CodexDirectivesCodec.claimed_paths
    validate_claimed_paths(CodexDirectivesCodec, CodexConfig)


# -- Claude side: LossWarning ------------------------------------------------


def test_claude_setting_personality_emits_loss_warning() -> None:
    """Claude has no personality equivalent. Setting ``directives.personality``
    in neutral and lowering it through the Claude codec MUST emit a typed
    ``LossWarning`` that references P1-E and names the lost field.
    """
    ctx = TranspileCtx()
    ClaudeDirectivesCodec.to_target(Directives(personality=Personality.PRAGMATIC), ctx)
    assert len(ctx.warnings) == 1
    warning = ctx.warnings[0]
    assert warning.domain == Domains.DIRECTIVES
    assert warning.target == BUILTIN_CLAUDE
    assert "P1-E" in warning.message
    assert "personality" in warning.message
    assert warning.field_path == FieldPath(segments=("personality",))


def test_claude_unset_personality_does_not_warn() -> None:
    ctx = TranspileCtx()
    ClaudeDirectivesCodec.to_target(Directives(), ctx)
    assert ctx.warnings == []


def test_claude_other_directives_fields_still_round_trip() -> None:
    """Sanity check: introducing the personality LossWarning path must not
    regress the existing commit_attribution / system_prompt_file mapping.
    """
    ctx = TranspileCtx()
    section = ClaudeDirectivesCodec.to_target(
        Directives(system_prompt_file="concise", commit_attribution=""),
        ctx,
    )
    assert isinstance(section, ClaudeDirectivesSection)
    assert section.outputStyle == "concise"
    assert section.attribution.commit == ""
    assert ctx.warnings == []
