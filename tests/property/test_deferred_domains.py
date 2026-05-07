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
from chameleon.schema.authorization import Authorization, PermissionMode, SandboxMode
from chameleon.schema.governance import Governance, Trust, Updates, UpdatesChannel
from chameleon.schema.interface import Interface, Voice
from chameleon.schema.lifecycle import History, HistoryPersistence, Lifecycle

# ---- Authorization ----------------------------------------------------------


def test_claude_authorization_round_trip_permission_mode_filesystem_network() -> None:
    #  S2: Claude IS the permission_mode axis (LCD lossless side).
    # ``sandbox_mode`` is now Codex-only — see the LossWarning surface in
    # ``test_claude_authorization_codec.py``.
    orig = Authorization(
        permission_mode=PermissionMode.ACCEPT_EDITS,
        allow_patterns=["Bash(npm run *)"],
        deny_patterns=["Bash(curl *)"],
    )
    orig.filesystem.allow_write.append("/tmp/build")
    orig.network.allowed_domains.append("github.com")
    ctx = TranspileCtx()
    section = ClaudeAuthorizationCodec.to_target(orig, ctx)
    restored = ClaudeAuthorizationCodec.from_target(section, ctx)
    assert restored.permission_mode is PermissionMode.ACCEPT_EDITS
    assert restored.allow_patterns == ["Bash(npm run *)"]
    assert restored.deny_patterns == ["Bash(curl *)"]
    assert restored.filesystem.allow_write == ["/tmp/build"]
    assert restored.network.allowed_domains == ["github.com"]


def test_codex_authorization_round_trip_sandbox_mode_writable_roots() -> None:
    orig = Authorization(sandbox_mode=SandboxMode.READ_ONLY)
    orig.filesystem.allow_write.append("/tmp/build")
    ctx = TranspileCtx()
    section = CodexAuthorizationCodec.to_target(orig, ctx)
    restored = CodexAuthorizationCodec.from_target(section, ctx)
    assert restored.sandbox_mode is SandboxMode.READ_ONLY
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


# ---- Trust canonicalisation ( D-IDEM regression) ---------------------
#
# The Codex governance codec serialises ``trust.{trusted,untrusted}_paths`` to
# a path-keyed ``[projects."<path>"].trust_level`` map. Two natural list
# shapes have no faithful representation in that wire model:
#
#   * Duplicate paths within a single list (a dict can't carry two values
#     for the same key).
#   * The same path in BOTH lists (the second write to ``projects."<path>"``
#     overwrites the first).
#
# Both situations were the root cause of the  state-machine fuzz's
# ``merge_twice_idempotent`` violations on adversarial governance edits:
# neutral ``[/a, /a]`` round-tripped to ``[/a]`` through the Codex codec,
# the engine classified the difference as TARGET-source drift on the second
# merge, and the per-target-CONSENSUAL re-derive overwrote N₁'s authored
# duplicates. The canonical fix lives in the schema (``Trust._canonicalise_paths``)
# rather than in either codec — neither target's wire shape supports the
# non-canonical input, so neutral itself canonicalises on construction.


def test_trust_dedupes_within_each_list() -> None:
    """Repeated paths in a single list collapse to first-occurrence order."""
    trust = Trust(trusted_paths=["/a", "/b", "/a", "/c"], untrusted_paths=["/x", "/x"])
    assert trust.trusted_paths == ["/a", "/b", "/c"]
    assert trust.untrusted_paths == ["/x"]


def test_trust_overlap_resolves_to_untrusted_wins() -> None:
    """A path in both lists is kept only on the untrusted side.

    Matches the Codex codec's write order (``trusted`` first, ``untrusted``
    overwrites) so the schema's canonical form and the Codex round-trip
    agree on adversarial inputs without lossy classification.
    """
    trust = Trust(trusted_paths=["/a", "/b"], untrusted_paths=["/b", "/c"])
    assert trust.trusted_paths == ["/a"]
    assert trust.untrusted_paths == ["/b", "/c"]


def test_trust_canonical_form_round_trips_through_codex() -> None:
    """The post-canonicalisation Trust round-trips byte-identically."""
    orig = Trust(trusted_paths=["/a", "/a", "/b"], untrusted_paths=["/c", "/b"])
    # After canonicalisation: trusted=['/a'], untrusted=['/c', '/b']
    governance = Governance(trust=orig)
    ctx = TranspileCtx()
    section = CodexGovernanceCodec.to_target(governance, ctx)
    restored = CodexGovernanceCodec.from_target(section, ctx)
    assert restored.trust.trusted_paths == orig.trusted_paths
    assert restored.trust.untrusted_paths == orig.untrusted_paths
