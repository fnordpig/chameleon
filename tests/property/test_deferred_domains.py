"""Round-trip tests for the four formerly-deferred domains
(authorization, lifecycle, interface, governance) on both targets."""

from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.authorization import ClaudeAuthorizationCodec
from chameleon.codecs.claude.governance import ClaudeGovernanceCodec
from chameleon.codecs.claude.interface import ClaudeInterfaceCodec
from chameleon.codecs.claude.lifecycle import ClaudeLifecycleCodec
from chameleon.codecs.codex.authorization import CodexAuthorizationCodec
from chameleon.codecs.codex.governance import CodexGovernanceCodec
from chameleon.codecs.codex.interface import CodexInterfaceCodec
from chameleon.codecs.codex.lifecycle import CodexLifecycleCodec
from chameleon.schema.authorization import Authorization, DefaultMode
from chameleon.schema.governance import Governance, Trust, Updates, UpdatesChannel
from chameleon.schema.interface import Interface, Voice
from chameleon.schema.lifecycle import History, HistoryPersistence, Lifecycle

# ---- Authorization ----------------------------------------------------------


def test_claude_authorization_round_trip_default_mode_filesystem_network() -> None:
    orig = Authorization(
        default_mode=DefaultMode.WORKSPACE_WRITE,
        allow_patterns=["Bash(npm run *)"],
        deny_patterns=["Bash(curl *)"],
    )
    orig.filesystem.allow_write.append("/tmp/build")
    orig.network.allowed_domains.append("github.com")
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(orig, ctx)
    restored = ClaudeAuthorizationCodec.from_target(section, ctx)
    assert restored.default_mode is DefaultMode.WORKSPACE_WRITE
    assert restored.allow_patterns == ["Bash(npm run *)"]
    assert restored.deny_patterns == ["Bash(curl *)"]
    assert restored.filesystem.allow_write == ["/tmp/build"]
    assert restored.network.allowed_domains == ["github.com"]


def test_codex_authorization_round_trip_sandbox_mode_writable_roots() -> None:
    orig = Authorization(default_mode=DefaultMode.READ_ONLY)
    orig.filesystem.allow_write.append("/tmp/build")
    ctx = TranspileCtx()
    section = CodexAuthorizationCodec.to_target(orig, ctx)
    restored = CodexAuthorizationCodec.from_target(section, ctx)
    assert restored.default_mode is DefaultMode.READ_ONLY
    assert restored.filesystem.allow_write == ["/tmp/build"]


def test_codex_authorization_warns_on_claude_only_patterns() -> None:
    ctx = TranspileCtx()
    CodexAuthorizationCodec.to_target(Authorization(allow_patterns=["Bash(npm run *)"]), ctx)
    assert any("allow,ask,deny" in w.message for w in ctx.warnings)


# ---- Lifecycle --------------------------------------------------------------


def test_claude_lifecycle_round_trip_cleanup() -> None:
    orig = Lifecycle(cleanup_period_days=14)
    ctx = TranspileCtx()
    section = ClaudeLifecycleCodec.to_target(orig, ctx)
    restored = ClaudeLifecycleCodec.from_target(section, ctx)
    assert restored.cleanup_period_days == 14


def test_codex_lifecycle_round_trip_history() -> None:
    orig = Lifecycle(history=History(persistence=HistoryPersistence.SAVE_ALL, max_bytes=5_242_880))
    ctx = TranspileCtx()
    section = CodexLifecycleCodec.to_target(orig, ctx)
    restored = CodexLifecycleCodec.from_target(section, ctx)
    assert restored.history.persistence is HistoryPersistence.SAVE_ALL
    assert restored.history.max_bytes == 5_242_880


# ---- Interface --------------------------------------------------------------


def test_claude_interface_round_trip() -> None:
    orig = Interface(
        fullscreen=True,
        status_line_command="~/.claude/statusline.sh",
        voice=Voice(enabled=True),
        motion_reduced=False,
    )
    ctx = TranspileCtx()
    section = ClaudeInterfaceCodec.to_target(orig, ctx)
    restored = ClaudeInterfaceCodec.from_target(section, ctx)
    assert restored.fullscreen is True
    assert restored.status_line_command == "~/.claude/statusline.sh"
    assert restored.voice is not None
    assert restored.voice.enabled is True
    assert restored.motion_reduced is False


def test_codex_interface_round_trip() -> None:
    orig = Interface(theme="catppuccin-mocha", file_opener="vscode", fullscreen=True)
    ctx = TranspileCtx()
    section = CodexInterfaceCodec.to_target(orig, ctx)
    restored = CodexInterfaceCodec.from_target(section, ctx)
    assert restored.theme == "catppuccin-mocha"
    assert restored.file_opener == "vscode"
    assert restored.fullscreen is True


# ---- Governance -------------------------------------------------------------


def test_claude_governance_round_trip_updates() -> None:
    orig = Governance(updates=Updates(channel=UpdatesChannel.STABLE, minimum_version="2.1.100"))
    ctx = TranspileCtx()
    section = ClaudeGovernanceCodec.to_target(orig, ctx)
    restored = ClaudeGovernanceCodec.from_target(section, ctx)
    assert restored.updates.channel is UpdatesChannel.STABLE
    assert restored.updates.minimum_version == "2.1.100"


def test_codex_governance_round_trip_features_trust() -> None:
    orig = Governance(
        features={"shell_tool": True, "fast_mode": True},
        trust=Trust(trusted_paths=["/repo/foo"], untrusted_paths=["/tmp/sketchy"]),
    )
    ctx = TranspileCtx()
    section = CodexGovernanceCodec.to_target(orig, ctx)
    restored = CodexGovernanceCodec.from_target(section, ctx)
    assert restored.features == {"shell_tool": True, "fast_mode": True}
    assert restored.trust.trusted_paths == ["/repo/foo"]
    assert restored.trust.untrusted_paths == ["/tmp/sketchy"]
