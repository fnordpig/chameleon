"""P0-2 regression: a single malformed section must not abort disassemble.

Today both ``ClaudeAssembler.disassemble`` and ``CodexAssembler.disassemble``
call ``Section.model_validate(section_obj)`` unguarded inside a per-domain
fan-out. A real-world Claude / Codex config has surface chameleon's codecs
don't fully model — and even after P0-1's discriminator fix, defensive
resilience here is non-negotiable: one malformed key in any one domain
must not destroy every other domain's worth of validation work.

The contract the assembler must honour, post-P0-2:

  - A failing section's ``model_validate`` is caught (``ValidationError``).
  - A typed ``LossWarning`` describing the failure is emitted via the
    optional ``ctx: TranspileCtx`` parameter (Option B from the task spec).
    Message format: ``"could not disassemble {domain}: {error}; routing
    to pass-through"``.
  - The offending section's keys land in the pass-through bag verbatim
    instead of in ``per_domain``.
  - Other domains continue to disassemble normally — the entire merge
    must complete with exit 0 and the warning visible.

The end-to-end test exercises ``chameleon merge`` against a tmpdir HOME
with a malformed live ``~/.claude/settings.json``; asserts exit 0, the
warning is on stderr, and the malformed value lands in
``targets.claude.items.permissions`` of neutral.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chameleon import cli
from chameleon.codecs._protocol import TranspileCtx
from chameleon.io.yaml import load_yaml
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains
from chameleon.schema.neutral import Neutral
from chameleon.targets.claude.assembler import ClaudeAssembler
from chameleon.targets.codex.assembler import CodexAssembler


def test_claude_disassemble_survives_malformed_section() -> None:
    """A malformed ``permissions`` (string instead of object) must not crash.

    Other domains (``identity``, ``directives``, ``interface``) populate
    normally; ``authorization`` is absent from the per-domain dict; the
    bad value lands verbatim in pass-through; a ``LossWarning`` is on
    ``ctx.warnings`` naming the domain and surfacing the underlying error.
    """
    raw = json.dumps(
        {
            "model": "claude-sonnet-4-7",
            "effortLevel": "high",
            "permissions": "this should be an object, not a string",
            "outputStyle": "concise",
            "voiceEnabled": True,
        }
    ).encode("utf-8")

    ctx = TranspileCtx()
    domains, passthrough = ClaudeAssembler.disassemble(
        {ClaudeAssembler.SETTINGS_JSON: raw}, ctx=ctx
    )

    # Surviving domains populated.
    assert Domains.IDENTITY in domains
    assert Domains.DIRECTIVES in domains
    assert Domains.INTERFACE in domains

    # Bad domain absent.
    assert Domains.AUTHORIZATION not in domains

    # Bad keys land verbatim in pass-through.
    assert passthrough.get("permissions") == "this should be an object, not a string"

    # Exactly one warning, naming the domain and target, with the field name
    # in the message body (so the operator can find what blew up).
    auth_warnings = [w for w in ctx.warnings if w.domain == Domains.AUTHORIZATION]
    assert len(auth_warnings) == 1, f"expected 1 authorization warning, got {ctx.warnings!r}"
    w = auth_warnings[0]
    assert w.target == BUILTIN_CLAUDE
    assert "could not disassemble" in w.message
    assert "authorization" in w.message
    assert "routing to pass-through" in w.message


def test_codex_disassemble_survives_malformed_section() -> None:
    """Codex assembler mirrors the Claude shape: a malformed ``projects``
    entry surfaces as a warning + pass-through, other domains survive."""
    raw = (
        b'model = "gpt-5"\n'
        b"# `projects` must be a table-of-tables; a bare string here is\n"
        b"# the kind of malformed surface a real operator might hand-edit.\n"
        b'projects = "this should be a table, not a string"\n'
        b'[tui]\ntheme = "dark"\n'
    )

    ctx = TranspileCtx()
    domains, passthrough = CodexAssembler.disassemble({CodexAssembler.CONFIG_TOML: raw}, ctx=ctx)

    assert Domains.IDENTITY in domains
    assert Domains.INTERFACE in domains
    assert Domains.GOVERNANCE not in domains

    assert passthrough.get("projects") == "this should be a table, not a string"

    gov_warnings = [w for w in ctx.warnings if w.domain == Domains.GOVERNANCE]
    assert len(gov_warnings) == 1, f"expected 1 governance warning, got {ctx.warnings!r}"
    w = gov_warnings[0]
    assert w.target == BUILTIN_CODEX
    assert "could not disassemble" in w.message
    assert "governance" in w.message
    assert "routing to pass-through" in w.message


def test_disassemble_without_ctx_still_does_not_crash() -> None:
    """Backward-compat: callers that don't pass ctx (existing tests, the
    schema-drift suite) still must not see a ``ValidationError`` bubble
    out. The bad keys still route to pass-through; the warning is simply
    discarded because no collector was supplied.
    """
    raw = json.dumps({"permissions": "broken"}).encode("utf-8")
    domains, passthrough = ClaudeAssembler.disassemble({ClaudeAssembler.SETTINGS_JSON: raw})
    assert Domains.AUTHORIZATION not in domains
    assert passthrough.get("permissions") == "broken"


def _setup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    state.mkdir()
    config.mkdir()
    home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("HOME", str(home))
    return {"state": state, "config": config, "home": home}


def test_merge_end_to_end_surfaces_warning_and_routes_to_passthrough(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: ``chameleon merge`` against a malformed live file
    exits 0, prints the warning to stderr, and the malformed value lands
    under ``targets.claude.items.permissions`` of the neutral file.
    """
    paths = _setup_env(monkeypatch, tmp_path)

    live_settings = paths["home"] / ".claude" / "settings.json"
    live_settings.parent.mkdir(parents=True, exist_ok=True)
    live_settings.write_text(
        json.dumps(
            {
                "model": "claude-sonnet-4-7",
                "permissions": "this should be an object, not a string",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # init absorbs the live state.
    assert cli.main(["init"]) == 0
    # merge re-derives — must not crash on the malformed key.
    assert cli.main(["merge", "--on-conflict=fail"]) == 0

    captured = capsys.readouterr()
    assert "could not disassemble" in captured.err, (
        f"warning missing from stderr; stderr={captured.err!r}"
    )
    assert "authorization" in captured.err
    assert "routing to pass-through" in captured.err

    # Validate the neutral file through ``Neutral`` so the test stays typed
    # all the way through (ruamel returns CommentedMap; ty narrows .get()
    # against it to ``Never``). The bag for Claude must carry the malformed
    # ``permissions`` value verbatim — that's the contract: validation
    # failures route to pass-through, where the operator can see them.
    neutral_doc = load_yaml(paths["config"] / "chameleon" / "neutral.yaml")
    n = Neutral.model_validate(neutral_doc)
    claude_bag = n.targets.get(BUILTIN_CLAUDE)
    assert claude_bag is not None, "neutral missing targets.claude after malformed merge"
    assert claude_bag.items.get("permissions") == "this should be an object, not a string", (
        f"expected malformed permissions in targets.claude.items; got items={claude_bag.items!r}"
    )
