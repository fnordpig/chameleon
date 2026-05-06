"""capabilities domain — what tools/skills/MCP/subagents are available."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Discriminator, Field, Tag

from chameleon._types import FieldPath, TargetId
from chameleon.schema._constants import Domains


class McpServerStdio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class McpServerStreamableHttp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["http"] = "http"
    url: AnyHttpUrl
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] = Field(default_factory=dict)


def _mcp_server_discriminator(v: object) -> str:
    if isinstance(v, dict):
        if "url" in v:
            return "http"
        return "stdio"
    return getattr(v, "transport", "stdio")


McpServer = Annotated[
    Annotated[McpServerStdio, Tag("stdio")] | Annotated[McpServerStreamableHttp, Tag("http")],
    Discriminator(_mcp_server_discriminator),
]


class PluginEntry(BaseModel):
    """A single enabled/disabled plugin keyed by ``<plugin>@<marketplace>``.

    Both Claude (``enabledPlugins``) and Codex (``[plugins.<id>]``) key plugins
    by the same ``<plugin>@<marketplace>`` identifier, so the neutral model
    uses that as the dict key. Claude encodes the value as a bare ``bool``;
    Codex encodes it as a TOML table whose only field today is ``enabled:
    bool``. ``PluginEntry`` is a model (not a bare ``bool``) so that future
    per-plugin overlays both targets grow can be added here without
    flipping the dict-value type and breaking every persisted neutral.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class PluginMarketplaceSource(BaseModel):
    """Cross-target marketplace source descriptor.

    Both Claude and Codex express marketplace sources, but with different
    shapes:

    * Claude: structured discriminated union — ``{"source": "github", "repo":
      "owner/name"}`` / ``{"source": "git", "url": "...", "ref": "..."}`` /
      etc. (``url``, ``hostPattern``, ``github``, ``git``, ``npm``, ``file``,
      ``directory``).
    * Codex: ``source`` (string URL or path) plus ``source_type`` (``git`` |
      ``local``).

    The neutral form normalizes to a small ``kind`` discriminator that round-
    trips both; ``npm`` / ``hostPattern`` / ``file`` / ``directory`` shapes
    are Claude-only and the codec emits a ``LossWarning`` when it sees them
    (they have no Codex analogue).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["github", "git", "url", "local"]
    repo: str | None = Field(
        default=None,
        description="GitHub ``owner/name``. Set iff ``kind == 'github'``.",
    )
    url: str | None = Field(
        default=None,
        description="Direct URL. Set iff ``kind in {'git', 'url'}``.",
    )
    path: str | None = Field(
        default=None,
        description="Local filesystem path. Set iff ``kind == 'local'``.",
    )
    ref: str | None = Field(
        default=None,
        description="Git ref (branch/tag/SHA). Optional for ``git``/``github``.",
    )


class PluginMarketplace(BaseModel):
    """A single registered plugin marketplace.

    The Codex-only operational state fields (``last_updated``, ``last_revision``,
    ``sparse_paths``) are intentionally NOT modelled here — they are runtime
    state Codex writes back to disk after each marketplace refresh. They
    ride along via the per-target pass-through bag (``targets.codex.items``)
    so a re-derive preserves them, but neutral does not own them.
    """

    model_config = ConfigDict(extra="forbid")

    source: PluginMarketplaceSource
    auto_update: bool | None = None


class Capabilities(BaseModel):
    """What tools/MCP/skills/subagents the agent can use.

    V0 codecs cover ``mcp_servers``, ``plugins``, and ``plugin_marketplaces``;
    remaining fields are typed schema with deferred codec implementation.
    """

    model_config = ConfigDict(extra="forbid")

    mcp_servers: dict[str, McpServer] = Field(default_factory=dict)
    plugins: dict[str, PluginEntry] = Field(
        default_factory=dict,
        description=(
            "Enabled/disabled plugins keyed by ``<plugin>@<marketplace>`` — "
            "matches both Claude's ``enabledPlugins`` keys and Codex's "
            "``[plugins.<id>]`` table names verbatim."
        ),
    )
    plugin_marketplaces: dict[str, PluginMarketplace] = Field(
        default_factory=dict,
        description=(
            "Registered plugin marketplaces keyed by marketplace name. Maps "
            "to Claude's ``extraKnownMarketplaces`` and Codex's "
            "``[marketplaces.<name>]`` tables."
        ),
    )
    skills: list[str] = Field(default_factory=list)
    subagents: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of subagent name to a config file path.",
    )
    web_search: Literal["cached", "live", "disabled"] | None = None


class PluginDisagreement(BaseModel):
    """Audit record emitted when targets disagree on a plugin's enabled state.

    Not a ``LossWarning`` (which is per-target codec scope) — disagreements
    are inherently cross-target. The merge engine's per-FieldPath classifier
    (P2-1, sibling agent's branch) will consume this record type to surface
    the conflict in the resolver UI.
    """

    model_config = ConfigDict(frozen=True)

    plugin_key: str
    per_target: dict[TargetId, bool]
    field_path: FieldPath
    domain: Domains


def reconcile_plugins(
    per_target: dict[TargetId, dict[str, PluginEntry]],
) -> tuple[dict[str, PluginEntry], list[PluginDisagreement]]:
    """Cross-target unification helper.

    Each target's ``from_target`` codec produces its own view of which plugins
    are enabled. When the same ``<plugin>@<marketplace>`` key appears in
    multiple targets with conflicting ``enabled`` values, the merge engine
    eventually resolves the conflict per ``classify_change`` rules (P2-1).
    Until then, this helper produces:

    * The union of all keys across all targets.
    * For every key where any two targets disagree, a ``PluginDisagreement``
      record listing the offending targets and their values.

    The unification rule for the union view: if any target says ``enabled =
    True`` and another says ``False``, the union view records ``True`` (the
    permissive value) and the disagreement is surfaced separately so the
    operator sees it. This is documented behaviour, not silent loss — the
    ``PluginDisagreement`` list is the audit trail.
    """

    union: dict[str, PluginEntry] = {}
    by_key: dict[str, dict[TargetId, bool]] = {}
    for tid, entries in per_target.items():
        for key, entry in entries.items():
            by_key.setdefault(key, {})[tid] = entry.enabled

    disagreements: list[PluginDisagreement] = []
    for key, target_values in by_key.items():
        values = set(target_values.values())
        # Permissive union: any True wins, otherwise False.
        union[key] = PluginEntry(enabled=any(target_values.values()))
        if len(values) > 1:
            disagreements.append(
                PluginDisagreement(
                    plugin_key=key,
                    per_target=dict(target_values),
                    field_path=FieldPath(segments=("capabilities", "plugins", key, "enabled")),
                    domain=Domains.CAPABILITIES,
                )
            )
    return union, disagreements


__all__ = [
    "Capabilities",
    "McpServer",
    "McpServerStdio",
    "McpServerStreamableHttp",
    "PluginDisagreement",
    "PluginEntry",
    "PluginMarketplace",
    "PluginMarketplaceSource",
    "reconcile_plugins",
]
