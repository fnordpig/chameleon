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
from chameleon.codecs.codex._generated import WebSearchMode
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
    # ``extra="allow"`` (B1) — upstream Codex may add fields like
    # ``startup_timeout_sec`` per server entry; preserve them through
    # round-trip via ``__pydantic_extra__`` rather than crashing on
    # unknown fields.
    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class _CodexMcpServerHttp(BaseModel):
    model_config = ConfigDict(extra="allow")
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

    ``source`` / ``source_type`` / ``ref`` are the codec-claimed fields —
    they round-trip through the neutral ``PluginMarketplaceSource`` shape.

    F2 (Wave-7): ``last_updated`` / ``last_revision`` / ``sparse_paths``
    are Codex-side operational state that belongs to the target, not to
    neutral. They are intentionally NOT modeled here — ``extra="allow"``
    routes them into ``__pydantic_extra__`` on disassemble, and the
    assembler's B1 extras-merge harvests them off the existing section
    and splices them back into the freshly-built ``[marketplaces.<name>]``
    table during ``assemble``. The recursion in
    ``targets._protocol._walk_field_extras`` walks the dict-of-BaseModel
    shape (``dict[str, _CodexMarketplaceEntry]``) and surfaces per-entry
    extras at the right nesting depth.

    Wave-11 F-MP fixes (round-trip preservation):

    * ``auto_update`` (F-AU) — the neutral ``PluginMarketplace.auto_update``
      flag had no Codex analogue and was previously dropped silently.
      Codex's upstream ``MarketplaceConfig`` is ``extra='allow'``, so we
      can carry the bit through as a plain key on the table; Codex itself
      ignores it on read, but the round-trip via Chameleon recovers it.
    * ``chameleon_kind`` / ``chameleon_repo`` (F-MP-G, F-MP-U) — neutral
      ``PluginMarketplaceSource`` distinguishes ``github``/``git``/``url``,
      but Codex's ``source_type`` enum only has ``git``/``local``. To
      round-trip the lost discriminator, we stash the original neutral
      ``kind`` (and the ``repo`` field for ``github``) as Chameleon-
      namespaced extras on the marketplace table. They appear in
      ``config.toml`` but are explicitly named so an operator can see
      what they are; Codex tolerates them via its own ``extra='allow'``
      shape and the next disassemble recovers the neutral form exactly.
    """

    model_config = ConfigDict(extra="allow")

    source: str | None = None
    source_type: Literal["git", "local"] | None = None
    ref: str | None = None
    auto_update: bool | None = None
    # Chameleon-private round-trip hints — preserve the neutral
    # ``kind`` discriminator that Codex's two-valued ``source_type``
    # cannot represent. Only set when the neutral ``kind`` is one of
    # the Codex-incompatible values (``github`` / ``url``); a plain
    # neutral ``kind='git'`` or ``kind='local'`` round-trips through
    # ``source_type`` alone and these stay ``None``.
    chameleon_kind: Literal["github", "url"] | None = None
    chameleon_repo: str | None = None


class CodexCapabilitiesSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    mcp_servers: dict[str, _CodexMcpServer] = Field(default_factory=dict)
    plugins: dict[str, _CodexPluginEntry] = Field(default_factory=dict)
    marketplaces: dict[str, _CodexMarketplaceEntry] = Field(default_factory=dict)
    # Wave-10 §15.x — capabilities.web_search ↔ web_search (top-level
    # ``WebSearchMode`` enum on ``ConfigToml``). Vocabulary matches the
    # neutral Literal exactly: ``disabled``/``cached``/``live``.
    web_search: WebSearchMode | None = None


class CodexCapabilitiesCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.CAPABILITIES
    target_section: ClassVar[type[BaseModel]] = CodexCapabilitiesSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("mcp_servers",)),
            FieldPath(segments=("plugins",)),
            FieldPath(segments=("marketplaces",)),
            # Wave-10 §15.x:
            FieldPath(segments=("web_search",)),
        }
    )

    @staticmethod
    def to_target(model: Capabilities, ctx: TranspileCtx) -> CodexCapabilitiesSection:
        # B2 (docs/superpowers/specs/2026-05-06-smoke-findings.md): emit
        # dict-keyed sub-tables in sorted-key order so the produced
        # section — and the resulting ``[mcp_servers.*]`` /
        # ``[plugins.*]`` / ``[marketplaces.*]`` blocks in
        # ``config.toml`` — are byte-stable across runs regardless of
        # how the engine populated the neutral dict.
        section = CodexCapabilitiesSection()
        for name in sorted(model.mcp_servers):
            server = model.mcp_servers[name]
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
        for plugin_key in sorted(model.plugins):
            section.plugins[plugin_key] = _CodexPluginEntry(
                enabled=model.plugins[plugin_key].enabled
            )
        for mp_name in sorted(model.plugin_marketplaces):
            section.marketplaces[mp_name] = _codex_marketplace_from_neutral(
                mp_name, model.plugin_marketplaces[mp_name], ctx
            )
        # Wave-10 §15.x — capabilities.web_search ↔ web_search. The Literal
        # vocabulary on the neutral side (``cached``/``live``/``disabled``)
        # was chosen to match Codex's ``WebSearchMode`` exactly, so this is
        # a direct lookup-by-value with no LossWarning paths.
        if model.web_search is not None:
            section.web_search = WebSearchMode(model.web_search)
        return section

    @staticmethod
    def from_target(section: CodexCapabilitiesSection, ctx: TranspileCtx) -> Capabilities:
        # B2: build neutral dicts in sorted-key order so cross-target
        # reconciliation is order-independent.
        servers: dict[str, McpServer] = {}
        for name in sorted(section.mcp_servers):
            raw = section.mcp_servers[name]
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
            key: PluginEntry(enabled=section.plugins[key].enabled)
            for key in sorted(section.plugins)
        }
        marketplaces: dict[str, PluginMarketplace] = {}
        for mp_name in sorted(section.marketplaces):
            entry = section.marketplaces[mp_name]
            neutral = _codex_marketplace_to_neutral(mp_name, entry, ctx)
            if neutral is not None:
                marketplaces[mp_name] = neutral
        # Wave-10 §15.x — reverse mapping for web_search. Pydantic accepts
        # the StrEnum instance for the neutral Literal field.
        web_search_value = section.web_search.value if section.web_search is not None else None
        return Capabilities(
            mcp_servers=servers,
            plugins=plugins,
            plugin_marketplaces=marketplaces,
            web_search=web_search_value,
        )


def _codex_marketplace_from_neutral(
    name: str, mp: PluginMarketplace, ctx: TranspileCtx
) -> _CodexMarketplaceEntry:
    src = mp.source
    auto_update = mp.auto_update
    if src.kind == "github":
        # Codex's ``source_type`` enum has no ``github`` value, so we
        # synthesize the canonical HTTPS URL and tag ``source_type='git'``
        # for upstream Codex compatibility. The original neutral ``kind``
        # and ``repo`` ride through as Chameleon-namespaced fields so
        # ``from_target`` can recover the exact ``PluginMarketplaceSource``
        # the operator authored — without these, ``kind='github'`` would
        # silently collapse to ``kind='git'`` (F-MP-G).
        url = src.url
        repo = src.repo
        if url is None and repo is not None:
            url = f"https://github.com/{repo}.git"
        if url is None:
            msg = f"marketplace {name!r}: PluginMarketplaceSource(kind='github') requires repo"
            raise ValueError(msg)
        return _CodexMarketplaceEntry(
            source=url,
            source_type="git",
            ref=src.ref,
            auto_update=auto_update,
            chameleon_kind="github",
            chameleon_repo=repo,
        )
    if src.kind == "git":
        if src.url is None:
            msg = f"marketplace {name!r}: PluginMarketplaceSource(kind='git') requires url"
            raise ValueError(msg)
        return _CodexMarketplaceEntry(
            source=src.url,
            source_type="git",
            ref=src.ref,
            auto_update=auto_update,
        )
    if src.kind == "url":
        if src.url is None:
            msg = f"marketplace {name!r}: PluginMarketplaceSource(kind='url') requires url"
            raise ValueError(msg)
        # Codex's ``source_type`` enum has no ``url`` value. We omit
        # ``source_type`` (so Codex won't try to git-clone the URL) and
        # tag ``chameleon_kind='url'`` so the next ``from_target`` recovers
        # ``kind='url'`` rather than collapsing to ``'git'`` (F-MP-U).
        return _CodexMarketplaceEntry(
            source=src.url,
            source_type=None,
            auto_update=auto_update,
            chameleon_kind="url",
        )
    if src.kind == "local":
        if src.path is None:
            msg = f"marketplace {name!r}: PluginMarketplaceSource(kind='local') requires path"
            raise ValueError(msg)
        return _CodexMarketplaceEntry(
            source=src.path,
            source_type="local",
            auto_update=auto_update,
        )
    # pragma: no cover — Literal exhaustion
    msg = f"marketplace {name!r}: unknown source kind {src.kind!r}"
    raise ValueError(msg)


def _codex_marketplace_to_neutral(
    name: str, entry: _CodexMarketplaceEntry, ctx: TranspileCtx
) -> PluginMarketplace | None:
    """Map a Codex ``[marketplaces.<name>]`` table to neutral.

    ``last_updated`` / ``last_revision`` / ``sparse_paths`` are intentionally
    DROPPED FROM NEUTRAL — they are Codex-side operational state and
    are not modeled here. They land in ``__pydantic_extra__`` on
    disassemble (via ``extra="allow"``) and are re-emitted by the
    assembler's B1 extras-merge during ``assemble`` — see
    ``_CodexMarketplaceEntry`` for the F2 mechanism.
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
    # Wave-11 F-MP-G/F-MP-U: if the encoder stashed a Chameleon-namespaced
    # neutral-kind hint, recover the original ``PluginMarketplaceSource``
    # shape exactly. Without these, ``github`` would collapse to ``git``
    # (the synthesized HTTPS URL is indistinguishable from a hand-written
    # one) and ``url`` would also collapse to ``git`` (since the only
    # remaining signal is the absence of ``source_type``, which a stale
    # write could erase).
    if entry.chameleon_kind == "github":
        neutral_src = PluginMarketplaceSource(
            kind="github",
            repo=entry.chameleon_repo,
            ref=entry.ref,
        )
    elif entry.chameleon_kind == "url":
        neutral_src = PluginMarketplaceSource(kind="url", url=entry.source)
    elif entry.source_type == "local":
        neutral_src = PluginMarketplaceSource(kind="local", path=entry.source)
    else:
        # source_type == "git" or unset; use ``git`` kind by default — this is
        # how Codex always stores remote marketplaces.
        neutral_src = PluginMarketplaceSource(kind="git", url=entry.source, ref=entry.ref)
    return PluginMarketplace(source=neutral_src, auto_update=entry.auto_update)


__all__ = ["CodexCapabilitiesCodec", "CodexCapabilitiesSection"]
