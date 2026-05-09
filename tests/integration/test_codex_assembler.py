from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex.identity import CodexIdentitySection
from chameleon.schema._constants import BUILTIN_CODEX, Domains
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


def test_assemble_sanitizes_codex_hooks_to_hooks() -> None:
    files = CodexAssembler.assemble(per_domain={}, passthrough={"features": {"codex_hooks": True}})
    text = files[CodexAssembler.CONFIG_TOML].decode("utf-8")
    assert "codex_hooks" not in text
    assert "hooks = true" in text


def test_assemble_prefers_hooks_over_codex_hooks() -> None:
    files = CodexAssembler.assemble(
        per_domain={},
        passthrough={"features": {"hooks": False, "codex_hooks": True}},
    )
    text = files[CodexAssembler.CONFIG_TOML].decode("utf-8")
    assert "codex_hooks" not in text
    assert "hooks = false" in text


def test_disassemble_invalid_toml_reports_parse_failure_warning() -> None:
    raw = b"[features]\nhooks = true\n["  # broken TOML
    ctx = TranspileCtx()
    domains, passthrough = CodexAssembler.disassemble({CodexAssembler.CONFIG_TOML: raw}, ctx=ctx)
    assert domains == {}
    assert passthrough == {}
    assert len(ctx.warnings) == 1
    warning = ctx.warnings[0]
    assert warning.domain == Domains.GOVERNANCE
    assert warning.target == BUILTIN_CODEX
    assert "parse failure" in warning.message
