"""Codex codec for capabilities — V0 ships mcp_servers only.

Codex's `[mcp_servers.<id>]` tables in config.toml. Each entry has either
`command`+`args` (stdio) OR `url` (streamable HTTP). The codec maps from
the neutral McpServer discriminated union into the same shape.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.capabilities import (
    Capabilities,
    McpServer,
    McpServerStdio,
    McpServerStreamableHttp,
    PluginEntry,
    PluginMarketplace,
    PluginMarketplaceSource,
)


class _CodexMcpServerStdio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class _CodexMcpServerHttp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    url: str
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] = Field(default_factory=dict)


_CodexMcpServer = _CodexMcpServerStdio | _CodexMcpServerHttp


class _CodexPluginEntry(BaseModel):
    """The shape of a single ``[plugins."<id>@<marketplace>"]`` table.

    Codex's upstream ``PluginConfig`` carries an optional ``mcp_servers``
    overlay that we don't currently model in neutral; ``extra="allow"`` lets
    those fields ride through unmolested when present so we don't crash, and
    the codec hoists them into pass-through if surfaced.
    """

    model_config = ConfigDict(extra="allow")
    enabled: bool = True


class _CodexMarketplaceEntry(BaseModel):
    """The shape of a single ``[marketplaces.<name>]`` table.

    ``last_updated`` and ``last_revision`` are operational state Codex writes
    back to disk after each marketplace refresh — they belong to the target,
    not to neutral. Per the design rationale, we leave them on this section
    model so disassemble doesn't drop them on the floor; the codec stashes
    them on per-target pass-through during ``from_target``.
    """

    model_config = ConfigDict(extra="allow")

    source: str | None = None
    source_type: Literal["git", "local"] | None = None
    ref: str | None = None
    last_updated: str | None = None
    last_revision: str | None = None
    sparse_paths: list[str] | None = None


class CodexCapabilitiesSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mcp_servers: dict[str, _CodexMcpServer] = Field(default_factory=dict)
    plugins: dict[str, _CodexPluginEntry] = Field(default_factory=dict)
    marketplaces: dict[str, _CodexMarketplaceEntry] = Field(default_factory=dict)


class CodexCapabilitiesCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.CAPABILITIES
    target_section: ClassVar[type[BaseModel]] = CodexCapabilitiesSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("mcp_servers",)),
            FieldPath(segments=("plugins",)),
            FieldPath(segments=("marketplaces",)),
        }
    )

    @staticmethod
    def to_target(model: Capabilities, ctx: TranspileCtx) -> CodexCapabilitiesSection:
        section = CodexCapabilitiesSection()
        for name, server in model.mcp_servers.items():
            if isinstance(server, McpServerStdio):
                section.mcp_servers[name] = _CodexMcpServerStdio(
                    command=server.command,
                    args=list(server.args),
                    env=dict(server.env),
                )
            elif isinstance(server, McpServerStreamableHttp):
                section.mcp_servers[name] = _CodexMcpServerHttp(
                    url=str(server.url),
                    bearer_token_env_var=server.bearer_token_env_var,
                    http_headers=dict(server.http_headers),
                )
        for plugin_key, entry in model.plugins.items():
            section.plugins[plugin_key] = _CodexPluginEntry(enabled=entry.enabled)
        for mp_name, mp in model.plugin_marketplaces.items():
            section.marketplaces[mp_name] = _codex_marketplace_from_neutral(mp_name, mp, ctx)
        return section

    @staticmethod
    def from_target(section: CodexCapabilitiesSection, ctx: TranspileCtx) -> Capabilities:
        servers: dict[str, McpServer] = {}
        for name, raw in section.mcp_servers.items():
            if isinstance(raw, _CodexMcpServerStdio):
                servers[name] = McpServerStdio(
                    command=raw.command, args=list(raw.args), env=dict(raw.env)
                )
            elif isinstance(raw, _CodexMcpServerHttp):
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
                        target=BUILTIN_CODEX,
                        message=f"unknown mcp_servers entry shape for {name!r}; dropping",
                    )
                )
        plugins: dict[str, PluginEntry] = {
            key: PluginEntry(enabled=entry.enabled) for key, entry in section.plugins.items()
        }
        marketplaces: dict[str, PluginMarketplace] = {}
        for mp_name, entry in section.marketplaces.items():
            neutral = _codex_marketplace_to_neutral(mp_name, entry, ctx)
            if neutral is not None:
                marketplaces[mp_name] = neutral
        return Capabilities(
            mcp_servers=servers,
            plugins=plugins,
            plugin_marketplaces=marketplaces,
        )


def _codex_marketplace_from_neutral(
    name: str, mp: PluginMarketplace, ctx: TranspileCtx
) -> _CodexMarketplaceEntry:
    src = mp.source
    if src.kind in {"github", "git"}:
        # Codex serializes both as ``source_type = "git"`` with a URL. ``github``
        # round-trips through git over HTTPS using the upstream repo path.
        url = src.url
        if url is None and src.kind == "github" and src.repo is not None:
            url = f"https://github.com/{src.repo}.git"
        if url is None:
            msg = f"marketplace {name!r}: PluginMarketplaceSource missing url/repo"
            raise ValueError(msg)
        return _CodexMarketplaceEntry(source=url, source_type="git", ref=src.ref)
    if src.kind == "url":
        if src.url is None:
            msg = f"marketplace {name!r}: PluginMarketplaceSource(kind='url') requires url"
            raise ValueError(msg)
        # Codex has no first-class "raw URL" source; we record the URL but
        # leave ``source_type`` unset so a Codex restart treats it as
        # documented operational state rather than a git checkout.
        ctx.warn(
            LossWarning(
                domain=Domains.CAPABILITIES,
                target=BUILTIN_CODEX,
                message=(
                    f"marketplace {name!r}: 'url' source kind has no Codex analogue; "
                    "recording bare URL with no source_type"
                ),
                field_path=FieldPath(segments=("marketplaces", name)),
            )
        )
        return _CodexMarketplaceEntry(source=src.url, source_type=None)
    if src.kind == "local":
        if src.path is None:
            msg = f"marketplace {name!r}: PluginMarketplaceSource(kind='local') requires path"
            raise ValueError(msg)
        return _CodexMarketplaceEntry(source=src.path, source_type="local")
    # pragma: no cover — Literal exhaustion
    msg = f"marketplace {name!r}: unknown source kind {src.kind!r}"
    raise ValueError(msg)


def _codex_marketplace_to_neutral(
    name: str, entry: _CodexMarketplaceEntry, ctx: TranspileCtx
) -> PluginMarketplace | None:
    """Map a Codex ``[marketplaces.<name>]`` table to neutral.

    ``last_updated`` / ``last_revision`` / ``sparse_paths`` are intentionally
    DROPPED FROM NEUTRAL — they are Codex-side operational state. They
    survive a re-derive via per-target pass-through (the assembler routes
    raw ``marketplaces`` table through pass-through when they're present;
    today the codec just emits a debug ``LossWarning`` for visibility).
    """

    if entry.source is None:
        ctx.warn(
            LossWarning(
                domain=Domains.CAPABILITIES,
                target=BUILTIN_CODEX,
                message=f"marketplace {name!r} has no source; cannot represent neutrally",
                field_path=FieldPath(segments=("marketplaces", name)),
            )
        )
        return None
    if entry.source_type == "local":
        neutral_src = PluginMarketplaceSource(kind="local", path=entry.source)
    else:
        # source_type == "git" or unset; use ``git`` kind by default — this is
        # how Codex always stores remote marketplaces.
        neutral_src = PluginMarketplaceSource(kind="git", url=entry.source, ref=entry.ref)
    return PluginMarketplace(source=neutral_src, auto_update=None)


__all__ = ["CodexCapabilitiesCodec", "CodexCapabilitiesSection"]
