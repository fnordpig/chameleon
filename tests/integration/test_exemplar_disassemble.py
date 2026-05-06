"""End-to-end disassemble of the sanitized exemplar fixture.

This is the test that catches gaps between codec-level fixes and
assembler-level routing. Wave-1 Agent C correctly fixed the directives
codec to accept three legacy commit-attribution aliases but flagged
that the assembler's hardcoded ``directives_keys`` set didn't route
them through. Without an end-to-end test like this one, that gap would
ship invisibly. The fix lives in the same merge-cleanup commit as this
test.
"""

from __future__ import annotations

from pathlib import Path

from chameleon.schema._constants import Domains
from chameleon.targets.claude.assembler import ClaudeAssembler
from chameleon.targets.codex.assembler import CodexAssembler

FIXTURE_HOME = Path(__file__).parent.parent / "fixtures" / "exemplar" / "home"


def test_claude_disassemble_against_exemplar_does_not_crash() -> None:
    """The exemplar exists *because* a real init crashed on it. This is the
    fixture-grounded regression test for P0-1 (MCP type discriminator).
    """
    settings_bytes = (FIXTURE_HOME / "_claude" / "settings.json").read_bytes()
    dotclaude_bytes = (FIXTURE_HOME / "_claude.json").read_bytes()
    domains, _ = ClaudeAssembler.disassemble(
        {
            ClaudeAssembler.SETTINGS_JSON: settings_bytes,
            ClaudeAssembler.DOTCLAUDE_JSON: dotclaude_bytes,
        }
    )
    assert Domains.IDENTITY in domains
    assert Domains.AUTHORIZATION in domains
    assert Domains.INTERFACE in domains


def test_claude_disassemble_routes_legacy_attribution_aliases() -> None:
    """Regression for the wiring gap Agent C flagged after P1-D.

    The exemplar's ``settings.json`` has all three of the bool aliases
    (``includeCoAuthoredBy``, ``coauthoredBy``, ``gitAttribution``) all set
    to ``false``. End-to-end disassemble must surface a directives section
    whose ``commit_attribution`` resolves correctly — not silently route
    the aliases to pass-through (which is what would happen if the
    assembler's ``directives_keys`` set didn't include them).
    """
    settings_bytes = (FIXTURE_HOME / "_claude" / "settings.json").read_bytes()
    dotclaude_bytes = (FIXTURE_HOME / "_claude.json").read_bytes()
    domains, passthrough = ClaudeAssembler.disassemble(
        {
            ClaudeAssembler.SETTINGS_JSON: settings_bytes,
            ClaudeAssembler.DOTCLAUDE_JSON: dotclaude_bytes,
        }
    )
    assert Domains.DIRECTIVES in domains, (
        "directives section is missing — "
        "the assembler likely failed to route legacy aliases to the codec"
    )
    # And specifically: those alias keys should NOT have leaked to pass-through.
    leaked = set(passthrough) & {
        "includeCoAuthoredBy",
        "coauthoredBy",
        "gitAttribution",
    }
    assert not leaked, (
        f"legacy attribution aliases leaked to pass-through: {leaked}; "
        "fix ClaudeAssembler.disassemble's directives_keys set"
    )


def test_codex_disassemble_against_exemplar_routes_known_keys() -> None:
    """Codex side: the exemplar is rich (~40 plugins, 9 marketplaces) but the
    V0 codec slice is narrow. Confirm what we DO claim works, and that
    the unclaimed top-level tables route to pass-through cleanly.
    """
    config_bytes = (FIXTURE_HOME / "_codex" / "config.toml").read_bytes()
    domains, passthrough = CodexAssembler.disassemble({CodexAssembler.CONFIG_TOML: config_bytes})
    # What we currently claim from this exemplar:
    assert Domains.IDENTITY in domains
    assert Domains.INTERFACE in domains
    assert Domains.GOVERNANCE in domains
    assert Domains.CAPABILITIES in domains, (
        "P1-A claimed Codex's [plugins.*] and [marketplaces.*] tables; "
        "the capabilities domain should now disassemble from this exemplar"
    )
    # What the parity-gap doc explicitly notes as unclaimed and
    # therefore SHOULD land in pass-through. ``marketplaces`` and ``plugins``
    # used to be on this list; they were claimed by P1-A and are now in
    # the capabilities domain (see assertion above).
    expected_passthrough = {
        "personality",
        "model_context_window",
        "model_auto_compact_token_limit",
        "model_catalog_json",
        "approvals_reviewer",
    }
    missing = expected_passthrough - set(passthrough)
    assert not missing, (
        f"expected passthrough keys missing: {missing}; got passthrough={sorted(passthrough)}"
    )
    # Conversely: ensure ``plugins`` / ``marketplaces`` are NOT in passthrough
    # any more — the codec owns them now.
    leaked = {"plugins", "marketplaces"} & set(passthrough)
    assert not leaked, f"P1-A-claimed keys leaked to pass-through: {leaked}"
