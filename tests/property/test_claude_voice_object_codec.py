"""P1-C: interface.voice as a structured object.

The Claude exemplar's `~/.claude/settings.json` carries both the legacy
documented `voiceEnabled: bool` AND a newer undocumented `voice` object
of shape ``{"enabled": bool, "mode": str}``. The two booleans CAN
disagree at runtime — Claude writes both during /voice flows and the
two paths are not always re-synced atomically.

P1-C lifts `voice` into a first-class neutral concept (``schema.interface.Voice``)
and replaces the V0-era ``Interface.voice_enabled`` bool with a
structured ``Interface.voice`` field. The Claude codec accepts either
input shape (bool-only, object-only, or both), prefers the structured
form when they disagree, and warns on disagreement. The Codex codec
warns when neutral voice is set since Codex has no equivalent.
"""

from __future__ import annotations

import json

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.interface import (
    ClaudeInterfaceCodec,
    ClaudeInterfaceSection,
)
from chameleon.codecs.codex.interface import CodexInterfaceCodec
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains
from chameleon.schema.interface import Interface, Voice, VoiceMode
from chameleon.targets.claude.assembler import ClaudeAssembler


def test_voice_object_round_trips_through_claude_codec() -> None:
    orig = Interface(voice=Voice(enabled=True, mode=VoiceMode.HOLD))
    ctx = TranspileCtx()
    section = ClaudeInterfaceCodec.to_target(orig, ctx)
    restored = ClaudeInterfaceCodec.from_target(section, ctx)
    assert restored.voice is not None
    assert restored.voice.enabled is True
    assert restored.voice.mode is VoiceMode.HOLD
    # No disagreement → no LossWarning.
    assert all(
        "voice" not in w.message.lower() or "disagree" not in w.message.lower()
        for w in ctx.warnings
    )


def test_voice_object_round_trips_disabled_with_mode() -> None:
    orig = Interface(voice=Voice(enabled=False, mode=VoiceMode.HOLD))
    ctx = TranspileCtx()
    section = ClaudeInterfaceCodec.to_target(orig, ctx)
    restored = ClaudeInterfaceCodec.from_target(section, ctx)
    assert restored.voice is not None
    assert restored.voice.enabled is False
    assert restored.voice.mode is VoiceMode.HOLD


def test_disassemble_exemplar_populates_voice() -> None:
    """The fixture has voice={"enabled": false, "mode": "hold"} AND
    voiceEnabled=false (in agreement). The codec should populate
    interface.voice with both fields."""
    raw = json.dumps(
        {
            "voice": {"enabled": False, "mode": "hold"},
            "voiceEnabled": False,
        }
    ).encode("utf-8")
    domains, passthrough = ClaudeAssembler.disassemble({ClaudeAssembler.SETTINGS_JSON: raw})
    # voice must NOT land in passthrough — P1-C claims it.
    assert "voice" not in passthrough
    assert "voiceEnabled" not in passthrough
    interface = domains[Domains.INTERFACE]
    assert isinstance(interface, ClaudeInterfaceSection)
    iface = ClaudeInterfaceCodec.from_target(interface, TranspileCtx())
    assert iface.voice is not None
    assert iface.voice.enabled is False
    assert iface.voice.mode is VoiceMode.HOLD


def test_disagreement_prefers_structured_and_warns() -> None:
    """When voiceEnabled and voice.enabled disagree, the structured form
    wins and the codec emits a LossWarning. The structured form is the
    canonical shape; voiceEnabled is the documented-but-legacy alias."""
    section = ClaudeInterfaceSection.model_validate(
        {
            "voice": {"enabled": False, "mode": "hold"},
            "voiceEnabled": True,
        }
    )
    ctx = TranspileCtx()
    iface = ClaudeInterfaceCodec.from_target(section, ctx)
    assert iface.voice is not None
    # Structured wins: enabled=False (from voice.enabled), not True (voiceEnabled).
    assert iface.voice.enabled is False
    assert iface.voice.mode is VoiceMode.HOLD
    # Disagreement must surface as a typed warning naming the conflict.
    disagreement_warnings = [
        w
        for w in ctx.warnings
        if w.target == BUILTIN_CLAUDE
        and w.domain == Domains.INTERFACE
        and "disagree" in w.message.lower()
    ]
    assert len(disagreement_warnings) == 1


def test_to_target_writes_both_upstream_forms_for_compat() -> None:
    """Claude documents only `voiceEnabled`; the structured `voice` object
    is undocumented in the upstream JSON schema but is what the runtime
    writes. To maximize compat with both /voice (which writes the
    structured form) and the documented schema (which knows only
    voiceEnabled), to_target writes BOTH and keeps them in agreement."""
    orig = Interface(voice=Voice(enabled=True, mode=VoiceMode.HOLD))
    ctx = TranspileCtx()
    section = ClaudeInterfaceCodec.to_target(orig, ctx)
    assert section.voice is not None
    assert section.voice.enabled is True
    assert section.voice.mode == "hold"
    # Legacy bool is mirrored from the structured form's enabled.
    assert section.voiceEnabled is True


def test_to_target_with_only_enabled_set_omits_mode() -> None:
    orig = Interface(voice=Voice(enabled=True))
    ctx = TranspileCtx()
    section = ClaudeInterfaceCodec.to_target(orig, ctx)
    assert section.voice is not None
    assert section.voice.enabled is True
    assert section.voice.mode is None
    assert section.voiceEnabled is True


def test_codex_warns_when_neutral_voice_is_set() -> None:
    orig = Interface(voice=Voice(enabled=True, mode=VoiceMode.HOLD))
    ctx = TranspileCtx()
    CodexInterfaceCodec.to_target(orig, ctx)
    voice_warnings = [
        w
        for w in ctx.warnings
        if w.target == BUILTIN_CODEX
        and w.domain == Domains.INTERFACE
        and "voice" in w.message.lower()
    ]
    assert len(voice_warnings) >= 1


def test_legacy_only_voice_enabled_still_disassembles() -> None:
    """Backwards-compatibility: a settings.json with only the legacy
    `voiceEnabled` (no structured `voice`) must still produce a populated
    Interface.voice with enabled set and mode=None."""
    section = ClaudeInterfaceSection.model_validate({"voiceEnabled": True})
    ctx = TranspileCtx()
    iface = ClaudeInterfaceCodec.from_target(section, ctx)
    assert iface.voice is not None
    assert iface.voice.enabled is True
    assert iface.voice.mode is None


def test_neither_voice_form_present_yields_none() -> None:
    section = ClaudeInterfaceSection()
    ctx = TranspileCtx()
    iface = ClaudeInterfaceCodec.from_target(section, ctx)
    assert iface.voice is None
