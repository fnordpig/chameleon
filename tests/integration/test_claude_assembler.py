from __future__ import annotations

from chameleon.codecs.claude.identity import ClaudeIdentitySection
from chameleon.schema._constants import Domains
from chameleon.targets.claude.assembler import ClaudeAssembler


def test_assemble_writes_settings_json_with_only_owned_keys() -> None:
    section = ClaudeIdentitySection(model="claude-sonnet-4-7", effortLevel="high")
    files = ClaudeAssembler.assemble(
        per_domain={Domains.IDENTITY: section},
        passthrough={},
    )
    settings_bytes = files[ClaudeAssembler.SETTINGS_JSON]
    text = settings_bytes.decode("utf-8")
    assert '"model"' in text
    assert '"effortLevel"' in text


def test_disassemble_round_trips_minimal() -> None:
    section = ClaudeIdentitySection(model="claude-sonnet-4-7")
    files = ClaudeAssembler.assemble(
        per_domain={Domains.IDENTITY: section},
        passthrough={},
    )
    domains, _passthrough = ClaudeAssembler.disassemble(files)
    assert Domains.IDENTITY in domains
    restored = domains[Domains.IDENTITY]
    assert getattr(restored, "model", None) == "claude-sonnet-4-7"


def test_disassemble_routes_unclaimed_keys_to_passthrough() -> None:
    # `voice` is now claimed by ClaudeInterfaceCodec (P1-C); use a key that
    # no codec claims as the canary for the pass-through path.
    raw = b'{"someUnknownFutureKey": {"x": true}}'
    _domains, passthrough = ClaudeAssembler.disassemble({ClaudeAssembler.SETTINGS_JSON: raw})
    assert "someUnknownFutureKey" in passthrough
