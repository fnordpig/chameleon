from __future__ import annotations

from chameleon.codecs.codex.identity import CodexIdentitySection
from chameleon.schema._constants import Domains
from chameleon.targets.codex.assembler import CodexAssembler


def test_assemble_writes_config_toml() -> None:
    section = CodexIdentitySection(model="gpt-5.4", model_reasoning_effort="high")
    files = CodexAssembler.assemble(per_domain={Domains.IDENTITY: section}, passthrough={})
    text = files[CodexAssembler.CONFIG_TOML].decode("utf-8")
    assert 'model = "gpt-5.4"' in text
    assert 'model_reasoning_effort = "high"' in text


def test_disassemble_round_trips() -> None:
    raw = b'model = "gpt-5.4"\nmodel_reasoning_effort = "high"\n'
    domains, _passthrough = CodexAssembler.disassemble({CodexAssembler.CONFIG_TOML: raw})
    assert Domains.IDENTITY in domains
