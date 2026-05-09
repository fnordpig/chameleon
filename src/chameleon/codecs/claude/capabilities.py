"""Claude codec for capabilities — V0 ships mcp_servers only.

Claude's MCP server definitions live in two files (per the design spec
): `~/.claude.json` (user-level mcpServers map) and `.mcp.json`
(project-level). For V0 we serialize the operator's neutral
`capabilities.mcp_servers` mapping into the user-level location; the
assembler is what splits across files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.codecs._url import parse_github_url
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.capabilities import (
    Capabilities,
    McpServer,
    McpServerStdio,
    McpServerStreamableHttp,
    PluginEntry,
    PluginMarketplace,
    PluginMarketplaceSource,
)


class _ClaudeMcpServerStdio(BaseModel):
    """Claude's user-level mcpServers entry shape (stdio variant).

    The `type` field is the on-disk discriminator real `~/.claude.json`
    entries carry; without it modelled here, `extra="forbid"` would
    reject every modern Claude config (parity-gap.md P0-1).
    """

    # ``extra="allow"`` — preserve upstream-introduced per-server
    # fields (e.g. ``startup_timeout_ms``, ``env_files``) through
    # round-trip via ``__pydantic_extra__``.
    model_config = ConfigDict(extra="allow")

    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # the on-disk Claude MCP stdio entry carries an
    # optional ``cwd`` (working directory) field — the same shape Codex
    # and the upstream MCP spec model. Without it, ``McpServerStdio.cwd``
    # silently dropped through ``to_target`` / ``from_target``: the
    # neutral schema's first-class ``cwd: str | None`` was not carried
    # by the Claude lane, and the cross-target fuzzer's
    # ``test_decode_symmetry_via_cross_target[capabilities.mcp_servers]``
    # caught the silent loss. Modelling it here closes the round-trip.
    cwd: str | None = None


class _ClaudeMcpServerHttp(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["http"] = "http"
    url: str
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] = Field(default_factory=dict)


# Discriminated by the on-disk `type` tag. Without `Field(discriminator=...)`,
# pydantic tries each branch in turn and surfaces the union of all branch
# errors — the misleading multi-error output in parity-gap.md P0-1.
_ClaudeMcpServer = Annotated[
    _ClaudeMcpServerStdio | _ClaudeMcpServerHttp,
    Field(discriminator="type"),
]


# Claude marketplace source variants — narrowed to the four kinds the
# neutral ``PluginMarketplaceSource`` models. Other Claude shapes
# (``hostPattern``, ``npm``, ``directory``) round-trip via per-target
# pass-through; the codec emits a ``LossWarning`` if it encounters them.
class _ClaudeMarketplaceSourceGithub(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: Literal["github"] = "github"
    repo: str
    ref: str | None = None


class _ClaudeMarketplaceSourceGit(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: Literal["git"] = "git"
    url: str
    ref: str | None = None


class _ClaudeMarketplaceSourceUrl(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: Literal["url"] = "url"
    url: str


class _ClaudeMarketplaceSourceLocal(BaseModel):
    """Maps to upstream ``directory`` (preferred) or ``file``."""

    model_config = ConfigDict(extra="allow")
    source: Literal["directory", "file"] = "directory"
    path: str


_ClaudeMarketplaceSource = (
    _ClaudeMarketplaceSourceGithub
    | _ClaudeMarketplaceSourceGit
    | _ClaudeMarketplaceSourceUrl
    | _ClaudeMarketplaceSourceLocal
)


# Marketplaces compiled into Claude Code itself — keys in
# ``enabledPlugins`` of the form ``<plugin>@<one-of-these>`` resolve
# without an ``extraKnownMarketplaces`` declaration. The list is
# small and stable; it grows when Anthropic ships a new built-in
# marketplace and we observe it in the wild.
#
# When chameleon assembles Claude's ``enabledPlugins`` from neutral,
# any key whose marketplace is NOT in this set AND not in
# ``model.plugin_marketplaces`` is dropped — Claude crashes on read
# with ``error: Plugin foo@bar is not cached at (not recorded)``,
# which is the exact failure the operator hit after running
# ``chameleon merge`` against a Codex config that declared plugins for
# marketplaces only Codex knew about.
_CLAUDE_BUILTIN_MARKETPLACES: frozenset[str] = frozenset(
    {
        "claude-plugins-official",
        "anthropic-agent-skills",
    }
)
_CLAUDE_INSTALLED_PLUGINS_PATH = Path.home() / ".claude" / "plugins" / "installed_plugins.json"


class _ClaudeMarketplace(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: _ClaudeMarketplaceSource = Field(discriminator="source")
    auto_update: bool | None = Field(default=None, alias="autoUpdate")


class ClaudeCapabilitiesSection(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    mcpServers: dict[str, _ClaudeMcpServer] = Field(default_factory=dict)  # noqa: N815
    enabledMcpjsonServers: list[str] = Field(default_factory=list)  # noqa: N815
    disabledMcpjsonServers: list[str] = Field(default_factory=list)  # noqa: N815
    # ``enabledPlugins`` and ``extraKnownMarketplaces`` live in
    # ``~/.claude/settings.json``, not ``~/.claude.json`` — the assembler
    # routes them accordingly.
    enabled_plugins: dict[str, bool] = Field(default_factory=dict, alias="enabledPlugins")
    extra_known_marketplaces: dict[str, _ClaudeMarketplace] = Field(
        default_factory=dict, alias="extraKnownMarketplaces"
    )


class ClaudeCapabilitiesCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.CAPABILITIES
    target_section: ClassVar[type[BaseModel]] = ClaudeCapabilitiesSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("mcpServers",)),
            FieldPath(segments=("enabledMcpjsonServers",)),
            FieldPath(segments=("disabledMcpjsonServers",)),
            # The schema-drift check resolves alias names against the
            # upstream-canonized ClaudeCodeSettings model — see ``_generated``
            # field aliases ``enabledPlugins`` / ``extraKnownMarketplaces``.
            FieldPath(segments=("enabledPlugins",)),
            FieldPath(segments=("extraKnownMarketplaces",)),
        }
    )

    @staticmethod
    def to_target(model: Capabilities, ctx: TranspileCtx) -> ClaudeCapabilitiesSection:
        # B2 (docs/superpowers/specs/2026-05-06-smoke-findings.md): emit
        # dict-keyed sub-tables in sorted-key order so the produced
        # section — and any downstream JSON/TOML serialization — is
        # byte-stable across runs. Without this, the order in which the
        # engine populated ``model.plugins`` / ``plugin_marketplaces`` /
        # ``mcp_servers`` (which depends on per-target reverse-codec
        # iteration) leaks into ``settings.json``.
        section = ClaudeCapabilitiesSection()
        # Claude has no top-level web-search mode setting.
        # The web-search lane is gated by ``permissions.allow``/``deny``
        # against the WebFetch / WebSearch tool names, which is structurally
        # different from neutral's ``cached``/``live``/``disabled`` axis.
        # Surface the drop as a typed warning rather than guess.
        if model.web_search is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.CAPABILITIES,
                    target=BUILTIN_CLAUDE,
                    message=(
                        f"capabilities.web_search ({model.web_search!r}) has no "
                        "Claude analogue (Claude gates web-search via the "
                        "WebFetch/WebSearch permissions tool-pattern, not a "
                        "tri-valued cached/live/disabled axis); dropping during "
                        "to_target."
                    ),
                    field_path=FieldPath(segments=("web_search",)),
                )
            )
        for name in sorted(model.mcp_servers):
            server = model.mcp_servers[name]
            if isinstance(server, McpServerStdio):
                section.mcpServers[name] = _ClaudeMcpServerStdio(
                    command=server.command,
                    args=list(server.args),
                    env=dict(server.env),
                    cwd=server.cwd,
                )
            elif isinstance(server, McpServerStreamableHttp):
                section.mcpServers[name] = _ClaudeMcpServerHttp(
                    url=str(server.url),
                    bearer_token_env_var=server.bearer_token_env_var,
                    http_headers=dict(server.http_headers),
                )
        # Defensive plugin emit: only write ``enabledPlugins[k]`` when
        # the marketplace component (after ``@``) is something Claude
        # can resolve at runtime — either declared in
        # ``plugin_marketplaces`` (assembled into ``extraKnownMarketplaces``
        # alongside) or one of Claude's compiled-in builtins. Codex
        # tolerates plugin keys whose marketplaces aren't declared
        # locally, but Claude raises ``error: Plugin foo@bar is not
        # cached at (not recorded)`` on read for any unresolvable key.
        # Surface the drop via a single LossWarning that lists every
        # affected key so the operator can either declare the missing
        # marketplace or accept the drop.
        known_marketplaces: frozenset[str] = (
            frozenset(model.plugin_marketplaces) | _CLAUDE_BUILTIN_MARKETPLACES
        )
        cached_plugins = _load_claude_installed_plugin_keys(_CLAUDE_INSTALLED_PLUGINS_PATH)
        dropped: list[tuple[str, str, str]] = []  # (plugin_key, marketplace_id-or-empty, reason)
        for plugin_key in sorted(model.plugins):
            mp_id = plugin_key.rsplit("@", 1)[1] if "@" in plugin_key else ""
            if mp_id and mp_id in known_marketplaces:
                if (
                    mp_id not in _CLAUDE_BUILTIN_MARKETPLACES
                    and cached_plugins is not None
                    and plugin_key not in cached_plugins
                ):
                    dropped.append((plugin_key, mp_id, "not cached"))
                    continue
                section.enabled_plugins[plugin_key] = model.plugins[plugin_key].enabled
            else:
                dropped.append((plugin_key, mp_id, "marketplace not declared"))
        if dropped:
            dropped_summary = ", ".join(
                (
                    f"{k!r} (marketplace={m!r}, reason={reason!r})"
                    if m
                    else f"{k!r} (no @marketplace)"
                )
                for k, m, reason in dropped
            )
            ctx.warn(
                LossWarning(
                    domain=Domains.CAPABILITIES,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "dropping enabledPlugins entries from source because they "
                        "are not supported in this environment: "
                        f"{dropped_summary}"
                    ),
                    field_path=FieldPath(segments=("enabledPlugins",)),
                )
            )
        for mp_name in sorted(model.plugin_marketplaces):
            section.extra_known_marketplaces[mp_name] = _claude_marketplace_from_neutral(
                model.plugin_marketplaces[mp_name]
            )
        return section

    @staticmethod
    def from_target(section: ClaudeCapabilitiesSection, ctx: TranspileCtx) -> Capabilities:
        # B2: build neutral dicts in sorted-key order so cross-target
        # reconciliation (and any subsequent re-derive) is independent of
        # the order in which the live file happened to enumerate entries.
        servers: dict[str, McpServer] = {}
        for name in sorted(section.mcpServers):
            raw = section.mcpServers[name]
            if isinstance(raw, _ClaudeMcpServerStdio):
                servers[name] = McpServerStdio(
                    command=raw.command,
                    args=list(raw.args),
                    env=dict(raw.env),
                    cwd=raw.cwd,
                )
            elif isinstance(raw, _ClaudeMcpServerHttp):
                # Use model_validate so Pydantic coerces the str URL into AnyHttpUrl;
                # direct keyword construction would fail static type checking.
                servers[name] = McpServerStreamableHttp.model_validate(
                    {
                        "url": raw.url,
                        "bearer_token_env_var": raw.bearer_token_env_var,
                        "http_headers": dict(raw.http_headers),
                    }
                )
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.CAPABILITIES,
                        target=BUILTIN_CLAUDE,
                        message=f"unknown mcpServers entry shape for {name!r}; dropping",
                        field_path=FieldPath(segments=("mcpServers",)),
                    )
                )
        # On disassembly, mirror the runtime-compatibility filter used at
        # assemble time: only load plugin entries whose marketplace is
        # either a known Claude builtin or explicitly declared in
        # extraKnownMarketplaces. This keeps typos (for example
        # ``archiuvium-plugin-creator@my-claude-plugins``) from being
        # reintroduced into neutral, which would get re-emitted as
        # runtime errors on the next compile unless manually cleaned.
        known_marketplaces = (
            frozenset(section.extra_known_marketplaces) | _CLAUDE_BUILTIN_MARKETPLACES
        )
        cached_plugins = _load_claude_installed_plugin_keys(_CLAUDE_INSTALLED_PLUGINS_PATH)
        dropped: list[tuple[str, str, str]] = []  # (plugin_key, marketplace_id-or-empty, reason)
        plugins: dict[str, PluginEntry] = {}
        for key in sorted(section.enabled_plugins):
            mp_id = key.rsplit("@", 1)[1] if "@" in key else ""
            if mp_id and mp_id in known_marketplaces:
                if (
                    mp_id not in _CLAUDE_BUILTIN_MARKETPLACES
                    and cached_plugins is not None
                    and key not in cached_plugins
                ):
                    dropped.append((key, mp_id, "not cached"))
                    continue
                plugins[key] = PluginEntry(enabled=section.enabled_plugins[key])
            else:
                dropped.append((key, mp_id, "marketplace not declared"))
        if dropped:
            dropped_summary = ", ".join(
                (
                    f"{k!r} (marketplace={m!r}, reason={reason!r})"
                    if m
                    else f"{k!r} (no @marketplace)"
                )
                for k, m, reason in dropped
            )
            ctx.warn(
                LossWarning(
                    domain=Domains.CAPABILITIES,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "dropping enabledPlugins entries from source because they "
                        "are not supported in this environment: "
                        f"{dropped_summary}"
                    ),
                    field_path=FieldPath(segments=("enabledPlugins",)),
                )
            )
        marketplaces: dict[str, PluginMarketplace] = {}
        for mp_name in sorted(section.extra_known_marketplaces):
            mp = section.extra_known_marketplaces[mp_name]
            neutral = _claude_marketplace_to_neutral(mp_name, mp, ctx)
            if neutral is not None:
                marketplaces[mp_name] = neutral
        return Capabilities(
            mcp_servers=servers,
            plugins=plugins,
            plugin_marketplaces=marketplaces,
        )


def _load_claude_installed_plugin_keys(path: Path) -> set[str] | None:
    try:
        if not path.exists():
            return None
    except OSError:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {k for k in data if isinstance(k, str)}


def _claude_marketplace_from_neutral(mp: PluginMarketplace) -> _ClaudeMarketplace:
    src = mp.source
    inner: _ClaudeMarketplaceSource
    if src.kind == "github":
        if src.repo is None:
            msg = "PluginMarketplaceSource(kind='github') requires repo"
            raise ValueError(msg)
        inner = _ClaudeMarketplaceSourceGithub(repo=src.repo, ref=src.ref)
    elif src.kind == "git":
        if src.url is None:
            msg = "PluginMarketplaceSource(kind='git') requires url"
            raise ValueError(msg)
        inner = _ClaudeMarketplaceSourceGit(url=src.url, ref=src.ref)
    elif src.kind == "url":
        if src.url is None:
            msg = "PluginMarketplaceSource(kind='url') requires url"
            raise ValueError(msg)
        inner = _ClaudeMarketplaceSourceUrl(url=src.url)
    elif src.kind == "local":
        if src.path is None:
            msg = "PluginMarketplaceSource(kind='local') requires path"
            raise ValueError(msg)
        inner = _ClaudeMarketplaceSourceLocal(path=src.path)
    else:  # pragma: no cover — Literal exhausts the kind set
        msg = f"unknown marketplace source kind {src.kind!r}"
        raise ValueError(msg)
    return _ClaudeMarketplace(source=inner, autoUpdate=mp.auto_update)


def _claude_marketplace_to_neutral(
    name: str, mp: _ClaudeMarketplace, ctx: TranspileCtx
) -> PluginMarketplace | None:
    s = mp.source
    if isinstance(s, _ClaudeMarketplaceSourceGithub):
        neutral_src = PluginMarketplaceSource(kind="github", repo=s.repo, ref=s.ref)
    elif isinstance(s, _ClaudeMarketplaceSourceGit):
        # Canonicalize hand-authored ``source: 'git'`` entries whose URL is
        # actually a github repo — the operator may have written the
        # ``git`` form, but Claude's native preferred shape for github-
        # hosted plugins is ``source: 'github'`` with structured
        # ``repo: 'owner/name'``. Neutral always holds the higher-detail
        # form so the next assemble re-emits Claude in its preferred
        # shape and cross-target merge sees a single canonical value.
        gh = parse_github_url(s.url)
        if gh is not None:
            owner, repo_name = gh
            neutral_src = PluginMarketplaceSource(
                kind="github",
                repo=f"{owner}/{repo_name}",
                ref=s.ref,
            )
        else:
            neutral_src = PluginMarketplaceSource(kind="git", url=s.url, ref=s.ref)
    elif isinstance(s, _ClaudeMarketplaceSourceUrl):
        neutral_src = PluginMarketplaceSource(kind="url", url=s.url)
    elif isinstance(s, _ClaudeMarketplaceSourceLocal):
        neutral_src = PluginMarketplaceSource(kind="local", path=s.path)
    else:  # pragma: no cover — discriminator exhaustion
        ctx.warn(
            LossWarning(
                domain=Domains.CAPABILITIES,
                target=BUILTIN_CLAUDE,
                message=(
                    f"marketplace {name!r}: unsupported Claude source shape; "
                    "routing to per-target pass-through"
                ),
                field_path=FieldPath(segments=("extraKnownMarketplaces", name)),
            )
        )
        return None
    return PluginMarketplace(source=neutral_src, auto_update=mp.auto_update)


__all__ = ["ClaudeCapabilitiesCodec", "ClaudeCapabilitiesSection"]
