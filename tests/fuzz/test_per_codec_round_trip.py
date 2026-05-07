"""FUZZ-1: per-codec round-trip property fuzz.

For every registered (target, domain) codec, generate any valid
neutral submodel via the registered Hypothesis strategies, encode
through ``to_target``, decode back through ``from_target``, and assert
round-trip equivalence on the codec's *claimed slice* — the subset of
neutral fields the codec actively translates. Fields outside the
claimed slice (Codex-only data fed to the Claude codec, Claude-only
data fed to Codex, ``cwd`` on MCP stdio servers, telemetry, etc.) are
documented-lossy and intentionally excluded from the comparison: those
axes round-trip via the *other* target's codec lane or via
pass-through, not through this codec.

The discipline this test enforces is:

  ``claim_extractor(from_target(to_target(x))) ==
   claim_extractor(canonicalize(x))``

where ``claim_extractor`` projects out the claimed slice as a
comparable plain-Python value, and ``canonicalize`` mirrors any
codec-side normalisation (e.g. Codex collapsing
``PluginMarketplaceSource(kind='github', repo=...)`` into a git URL,
the Codex marketplace codec dropping ``auto_update`` because Codex has
no analogue, etc.). When canonicalisation is identity the two sides
collapse and the assertion is the literal codec round-trip identity
from the design spec.

This file is parametrized by a list of :class:`_CodecCase` records — 16
total, one per (target, domain) pair — so a single test function
exercises every codec under both Hypothesis profiles. Adding a new
codec is one row in :data:`_CODEC_CASES`; removing one is one
deletion. The list is the registry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from hypothesis import given, target
from hypothesis import strategies as st

from chameleon.codecs._protocol import Codec, TranspileCtx
from chameleon.codecs.claude.authorization import ClaudeAuthorizationCodec
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.codecs.claude.directives import ClaudeDirectivesCodec
from chameleon.codecs.claude.environment import ClaudeEnvironmentCodec
from chameleon.codecs.claude.governance import ClaudeGovernanceCodec
from chameleon.codecs.claude.identity import ClaudeIdentityCodec
from chameleon.codecs.claude.interface import ClaudeInterfaceCodec
from chameleon.codecs.claude.lifecycle import ClaudeLifecycleCodec
from chameleon.codecs.codex.authorization import CodexAuthorizationCodec
from chameleon.codecs.codex.capabilities import CodexCapabilitiesCodec
from chameleon.codecs.codex.directives import CodexDirectivesCodec
from chameleon.codecs.codex.environment import CodexEnvironmentCodec
from chameleon.codecs.codex.governance import CodexGovernanceCodec
from chameleon.codecs.codex.identity import CodexIdentityCodec
from chameleon.codecs.codex.interface import CodexInterfaceCodec
from chameleon.codecs.codex.lifecycle import CodexLifecycleCodec
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.authorization import Authorization
from chameleon.schema.capabilities import (
    Capabilities,
    McpServerStdio,
    McpServerStreamableHttp,
)
from chameleon.schema.directives import Directives
from chameleon.schema.environment import Environment
from chameleon.schema.governance import Governance
from chameleon.schema.identity import Identity
from chameleon.schema.interface import Interface
from chameleon.schema.lifecycle import Lifecycle
from chameleon.targets.claude import ClaudeTarget
from chameleon.targets.codex import CodexTarget

# Importing strategies wires the registrations conftest already loaded —
# the explicit re-import documents the dependency for readers.
from tests.fuzz import strategies as _strategies

pytestmark = pytest.mark.fuzz


# ----------------------------------------------------------------------
# Per-codec extractors. Each returns a plain-Python comparable value
# (dicts/tuples/scalars) describing exactly the slice the codec claims
# after canonicalisation. They MUST NOT consult any field the codec
# does not claim — see the module docstring.
# ----------------------------------------------------------------------


def _identity_claude_claims(x: Identity) -> dict[str, Any]:
    return {
        "reasoning_effort": x.reasoning_effort,
        "thinking": x.thinking,
        "claude_model": x.model.get(BUILTIN_CLAUDE) if x.model is not None else None,
    }


def _identity_codex_claims(x: Identity) -> dict[str, Any]:
    return {
        "reasoning_effort": x.reasoning_effort,
        "codex_model": x.model.get(BUILTIN_CODEX) if x.model is not None else None,
        "context_window": x.context_window,
        "compact_threshold": x.compact_threshold,
        "model_catalog_path": x.model_catalog_path,
    }


def _directives_claude_claims(x: Directives) -> dict[str, Any]:
    return {
        "system_prompt_file": x.system_prompt_file,
        "commit_attribution": x.commit_attribution,
    }


def _directives_codex_claims(x: Directives) -> dict[str, Any]:
    return {
        "system_prompt_file": x.system_prompt_file,
        "commit_attribution": x.commit_attribution,
        "personality": x.personality,
    }


def _environment_claims(x: Environment) -> dict[str, Any]:
    # Both targets claim only the variables map; inherit/include_only/etc.
    # are unclaimed in V0 and round-trip via pass-through.
    return {"variables": dict(x.variables)}


def _authorization_claude_claims(x: Authorization) -> dict[str, Any]:
    # Wave-13 LCD: Claude claims permission_mode (not sandbox_mode — that's
    # Codex-only and LossWarn'd on Claude encode). filesystem and network
    # flat-list shapes plus pattern lists remain Claude-claimed.
    return {
        "permission_mode": x.permission_mode,
        "fs_allow_read": list(x.filesystem.allow_read),
        "fs_allow_write": list(x.filesystem.allow_write),
        "fs_deny_read": list(x.filesystem.deny_read),
        "fs_deny_write": list(x.filesystem.deny_write),
        "net_allowed": list(x.network.allowed_domains),
        "net_denied": list(x.network.denied_domains),
        "net_allow_local": x.network.allow_local_binding,
        "allow_patterns": list(x.allow_patterns),
        "ask_patterns": list(x.ask_patterns),
        "deny_patterns": list(x.deny_patterns),
    }


def _authorization_codex_claims(x: Authorization) -> dict[str, Any]:
    # Wave-13 LCD: Codex claims sandbox_mode and approval_policy (both
    # Codex-aligned axes). filesystem.allow_write and reviewer also
    # claimed. permission_mode is Claude-only and LossWarn'd on Codex.
    return {
        "sandbox_mode": x.sandbox_mode,
        "approval_policy": x.approval_policy,
        "fs_allow_write": list(x.filesystem.allow_write),
        "reviewer": x.reviewer,
    }


def _lifecycle_claude_claims(x: Lifecycle) -> dict[str, Any]:
    # Hooks: walk each event, describing its matchers and shell commands.
    # Only the claimed (event-keyed) hooks subtree is in scope.
    return {
        "cleanup_period_days": x.cleanup_period_days,
        "hooks": _normalize_hooks(x),
    }


def _lifecycle_codex_claims(x: Lifecycle) -> dict[str, Any]:
    return {
        "history_persistence": x.history.persistence,
        "history_max_bytes": x.history.max_bytes,
    }


def _normalize_hooks(lifecycle: Lifecycle) -> dict[str, Any]:
    """Render the modelled, command-typed hook entries comparably.

    Mirrors what the Claude lifecycle codec preserves: each event's
    matcher list, each matcher's optional regex, each command's
    ``command`` string and optional ``timeout``. The ``type`` field is
    fixed at ``"command"`` for every variant V0 round-trips (other
    variants emit a LossWarning and drop). Hook events not set on the
    input remain unset on the output; an explicitly-empty list of
    matchers also round-trips as an empty list.
    """
    out: dict[str, Any] = {}
    for field_name in [
        "pre_tool_use",
        "post_tool_use",
        "notification",
        "user_prompt_submit",
        "stop",
        "subagent_stop",
        "pre_compact",
        "session_start",
        "session_end",
    ]:
        matchers = getattr(lifecycle.hooks, field_name, None)
        if matchers is None:
            continue
        out[field_name] = [
            {
                "matcher": m.matcher,
                "hooks": [
                    {"command": c.command, "timeout": c.timeout, "type": c.type} for c in m.hooks
                ],
            }
            for m in matchers
        ]
    return out


def _interface_claude_claims(x: Interface) -> dict[str, Any]:
    voice = None
    if x.voice is not None:
        voice = {"enabled": x.voice.enabled, "mode": x.voice.mode}
    return {
        "fullscreen": x.fullscreen,
        "status_line_command": x.status_line_command,
        "voice": voice,
        "motion_reduced": x.motion_reduced,
    }


def _interface_codex_claims(x: Interface) -> dict[str, Any]:
    return {
        "fullscreen": x.fullscreen,
        "theme": x.theme,
        "file_opener": x.file_opener,
    }


def _governance_claude_claims(x: Governance) -> dict[str, Any]:
    return {
        "channel": x.updates.channel,
        "minimum_version": x.updates.minimum_version,
    }


def _governance_codex_claims(x: Governance) -> dict[str, Any]:
    # The codec serializes trusted/untrusted paths to a dict-keyed
    # ``[projects."<path>"].trust_level`` table — list-to-dict
    # normalization. Two consequences for the claimed slice:
    #
    #   * Duplicates collapse (a path repeated in ``trusted_paths``
    #     becomes a single ``[projects."<path>"]`` row).
    #   * A path appearing in BOTH ``trusted_paths`` and
    #     ``untrusted_paths`` resolves to whichever is iterated last
    #     (``untrusted`` wins because the codec writes trusted first).
    #
    # Both are documented list-to-dict canonicalisations rather than
    # codec bugs (the wire model literally has no list shape). The
    # extractor compares post-canonicalisation: "what is the trust
    # level of each unique path?", which is exactly what the codec
    # round-trips. Operators relying on duplicates or trusted+untrusted
    # overlap will see the canonical dict view in their Codex config.
    levels: dict[str, str] = {}
    for path in x.trust.trusted_paths:
        levels[path] = "trusted"
    for path in x.trust.untrusted_paths:
        levels[path] = "untrusted"
    return {
        "features": dict(x.features),
        "trust_levels": levels,
    }


def _capabilities_claude_claims(x: Capabilities) -> dict[str, Any]:
    return {
        "mcp_servers": _normalize_mcp_servers(x),
        "plugins": {k: v.enabled for k, v in x.plugins.items()},
        # Claude codec preserves the discriminated marketplace shape AND
        # auto_update faithfully — it is a structurally lossless mapping.
        "plugin_marketplaces": {
            k: _normalize_marketplace_claude(v) for k, v in x.plugin_marketplaces.items()
        },
    }


def _capabilities_codex_claims(x: Capabilities) -> dict[str, Any]:
    return {
        "mcp_servers": _normalize_mcp_servers(x),
        "plugins": {k: v.enabled for k, v in x.plugins.items()},
        # Codex codec collapses every marketplace ``kind`` onto a single
        # ``source``-string + optional ``source_type``. Claude-style
        # ``kind=github`` -> git URL, ``kind=url`` -> URL with no
        # source_type. ``auto_update`` has no Codex analogue and is
        # dropped. Compare on the canonicalised post-codec shape.
        "plugin_marketplaces": {
            k: _normalize_marketplace_codex(v) for k, v in x.plugin_marketplaces.items()
        },
    }


def _normalize_mcp_servers(caps: Capabilities) -> dict[str, Any]:
    """Drop ``cwd`` from the claimed slice — neither target codec
    preserves it (the wire shape has no equivalent field). Stdio and
    streamable-http variants share the comparable ``transport`` tag.
    """
    out: dict[str, Any] = {}
    for name, server in caps.mcp_servers.items():
        if isinstance(server, McpServerStdio):
            out[name] = {
                "transport": "stdio",
                "command": server.command,
                "args": list(server.args),
                "env": dict(server.env),
            }
        elif isinstance(server, McpServerStreamableHttp):
            out[name] = {
                "transport": "http",
                "url": str(server.url),
                "bearer_token_env_var": server.bearer_token_env_var,
                "http_headers": dict(server.http_headers),
            }
    return out


def _normalize_marketplace_claude(mp: Any) -> dict[str, Any]:
    s = mp.source
    return {
        "kind": s.kind,
        "repo": s.repo,
        "url": s.url,
        "path": s.path,
        "ref": s.ref,
        "auto_update": mp.auto_update,
    }


def _normalize_marketplace_codex(mp: Any) -> dict[str, Any]:
    """Match the codec's collapse rule.

    The Codex codec writes:
      - ``kind in {github, git}`` -> ``source = url-or-derived``,
        ``source_type = "git"`` -> reverse maps to ``kind="git"``,
        ``url=source`` (so ``kind="github"`` collapses to ``kind="git"``
        with the inferred URL as the canonical form).
      - ``kind == "url"`` -> ``source = url``, ``source_type = None``
        -> reverse maps to ``kind="git"``, ``url=source`` (codec emits a
        LossWarning explaining there is no Codex analogue for raw URLs).
      - ``kind == "local"`` -> ``source = path``,
        ``source_type = "local"`` -> reverse maps to ``kind="local"``.

    We mirror this canonicalisation here so the round-trip assertion
    compares post-codec shapes, not pre-codec inputs the codec is
    documented to normalise.
    """
    s = mp.source
    if s.kind == "github":
        # github -> git URL canonicalisation (built from owner/name).
        canon_url = (
            f"https://github.com/{s.repo}.git" if (s.url is None and s.repo is not None) else s.url
        )
        return {"kind": "git", "url": canon_url, "ref": s.ref}
    if s.kind in {"git", "url"}:
        return {"kind": "git", "url": s.url, "ref": s.ref}
    # "local"
    return {"kind": "local", "path": s.path, "ref": None}


# ----------------------------------------------------------------------
# Codec-case registry. One row per registered codec.
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _CodecCase:
    """One parametrised round-trip case."""

    label: str
    codec: type[Codec]
    strategy: st.SearchStrategy[Any]
    extractor: Callable[[Any], Any]


_CODEC_CASES: tuple[_CodecCase, ...] = (
    _CodecCase(
        "claude/identity",
        ClaudeIdentityCodec,  # type: ignore[type-abstract]
        _strategies.identities,
        _identity_claude_claims,
    ),
    _CodecCase(
        "codex/identity",
        CodexIdentityCodec,  # type: ignore[type-abstract]
        _strategies.identities,
        _identity_codex_claims,
    ),
    _CodecCase(
        "claude/directives",
        ClaudeDirectivesCodec,  # type: ignore[type-abstract]
        _strategies.directives,
        _directives_claude_claims,
    ),
    _CodecCase(
        "codex/directives",
        CodexDirectivesCodec,  # type: ignore[type-abstract]
        _strategies.directives,
        _directives_codex_claims,
    ),
    _CodecCase(
        "claude/capabilities",
        ClaudeCapabilitiesCodec,  # type: ignore[type-abstract]
        _strategies.capabilities,
        _capabilities_claude_claims,
    ),
    _CodecCase(
        "codex/capabilities",
        CodexCapabilitiesCodec,  # type: ignore[type-abstract]
        _strategies.capabilities,
        _capabilities_codex_claims,
    ),
    _CodecCase(
        "claude/authorization",
        ClaudeAuthorizationCodec,  # type: ignore[type-abstract]
        _strategies.authorizations,
        _authorization_claude_claims,
    ),
    _CodecCase(
        "codex/authorization",
        CodexAuthorizationCodec,  # type: ignore[type-abstract]
        _strategies.authorizations,
        _authorization_codex_claims,
    ),
    _CodecCase(
        "claude/environment",
        ClaudeEnvironmentCodec,  # type: ignore[type-abstract]
        _strategies.environments,
        _environment_claims,
    ),
    _CodecCase(
        "codex/environment",
        CodexEnvironmentCodec,  # type: ignore[type-abstract]
        _strategies.environments,
        _environment_claims,
    ),
    _CodecCase(
        "claude/lifecycle",
        ClaudeLifecycleCodec,  # type: ignore[type-abstract]
        _strategies.lifecycles,
        _lifecycle_claude_claims,
    ),
    _CodecCase(
        "codex/lifecycle",
        CodexLifecycleCodec,  # type: ignore[type-abstract]
        _strategies.lifecycles,
        _lifecycle_codex_claims,
    ),
    _CodecCase(
        "claude/interface",
        ClaudeInterfaceCodec,  # type: ignore[type-abstract]
        _strategies.interfaces,
        _interface_claude_claims,
    ),
    _CodecCase(
        "codex/interface",
        CodexInterfaceCodec,  # type: ignore[type-abstract]
        _strategies.interfaces,
        _interface_codex_claims,
    ),
    _CodecCase(
        "claude/governance",
        ClaudeGovernanceCodec,  # type: ignore[type-abstract]
        _strategies.governances,
        _governance_claude_claims,
    ),
    _CodecCase(
        "codex/governance",
        CodexGovernanceCodec,  # type: ignore[type-abstract]
        _strategies.governances,
        _governance_codex_claims,
    ),
)


# ----------------------------------------------------------------------
# Coverage sanity check: the case list must cover every (target, domain)
# pair that exists in the registry. If a future codec lands without a
# matching row, this guard fires before any fuzz example is drawn.
# ----------------------------------------------------------------------


def test_codec_cases_cover_every_registered_codec() -> None:
    expected = {
        (BUILTIN_CLAUDE, c.domain)
        for c in ClaudeTarget.codecs  # type: ignore[attr-defined]
    } | {
        (BUILTIN_CODEX, c.domain)
        for c in CodexTarget.codecs  # type: ignore[attr-defined]
    }
    covered = {(case.codec.target, case.codec.domain) for case in _CODEC_CASES}
    assert covered == expected, (
        f"FUZZ-1 case list out of sync with registry: "
        f"missing={expected - covered} extra={covered - expected}"
    )


# ----------------------------------------------------------------------
# The single fuzz test, parametrised over every codec case.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("case", _CODEC_CASES, ids=lambda c: c.label)
def test_codec_round_trip(case: _CodecCase) -> None:
    """Round-trip every codec on its claimed slice.

    Each parametrised case wires its own strategy and per-codec
    ``extractor``. Hypothesis draws an instance, the codec runs forward
    + reverse, and the extracted-claimed-slice values must match. The
    fuzz profile widens the example budget; the default profile is the
    smoke pass.
    """

    @given(model=case.strategy)
    def _check(model: Any) -> None:
        ctx = TranspileCtx()
        section = case.codec.to_target(model, ctx)
        recovered = case.codec.from_target(section, ctx)
        # target() biases the search toward inputs that exercise more
        # of the claimed surface — it is metric-driven, not
        # filter-driven, so it never narrows coverage.
        target(_observed_size(case.extractor(model)), label=f"claimed_size:{case.label}")
        assert case.extractor(recovered) == case.extractor(model), (
            f"{case.label}: round-trip diverged on claimed slice\n"
            f"  input:    {case.extractor(model)!r}\n"
            f"  recovered:{case.extractor(recovered)!r}"
        )

    _check()


def _observed_size(value: Any) -> float:
    """Approximate "claim density" of an extracted slice.

    Used as the :func:`hypothesis.target` metric. Hypothesis biases its
    search toward examples whose target value is high; we want examples
    that exercise more of the codec's claimed surface (longer lists,
    more populated dicts). Returns a non-negative float.
    """
    if value is None:
        return 0.0
    if isinstance(value, (list, dict, str)):
        return float(len(value))
    if isinstance(value, bool):
        return 1.0
    if isinstance(value, (int, float)):
        # Cap to avoid swamping the metric with one large integer.
        return min(abs(float(value)), 1024.0)
    if isinstance(value, dict):
        return sum(_observed_size(v) for v in value.values())
    return 1.0
