"""Claude codec for capabilities — V0 ships mcp_servers only.

Claude's MCP server definitions live in two files (per the design spec
§10.2): `~/.claude.json` (user-level mcpServers map) and `.mcp.json`
(project-level). For V0 we serialize the operator's neutral
`capabilities.mcp_servers` mapping into the user-level location; the
assembler is what splits across files.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
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

    # ``extra="allow"`` (B1) — preserve upstream-introduced per-server
    # fields (e.g. ``startup_timeout_ms``, ``env_files``) through
    # round-trip via ``__pydantic_extra__``.
    model_config = ConfigDict(extra="allow")

    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


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
        for name in sorted(model.mcp_servers):
            server = model.mcp_servers[name]
            if isinstance(server, McpServerStdio):
                section.mcpServers[name] = _ClaudeMcpServerStdio(
                    command=server.command,
                    args=list(server.args),
                    env=dict(server.env),
                )
            elif isinstance(server, McpServerStreamableHttp):
                section.mcpServers[name] = _ClaudeMcpServerHttp(
                    url=str(server.url),
                    bearer_token_env_var=server.bearer_token_env_var,
                    http_headers=dict(server.http_headers),
                )
        for plugin_key in sorted(model.plugins):
            section.enabled_plugins[plugin_key] = model.plugins[plugin_key].enabled
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
        plugins: dict[str, PluginEntry] = {
            key: PluginEntry(enabled=section.enabled_plugins[key])
            for key in sorted(section.enabled_plugins)
        }
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
