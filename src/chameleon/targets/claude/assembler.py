"""Claude assembler — composes/decomposes settings.json + ~/.claude.json.

Owns:
  - ~/.claude/settings.json  (FULL ownership)
  - ~/.claude.json           (PARTIAL ownership — only `mcpServers` key)

The assembler does not touch the live filesystem; that's the merge engine's
job. The assembler operates purely on bytes-in / bytes-out plus typed
per-domain section dicts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel

from chameleon._types import FileFormat, FileOwnership, FileSpec, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude import ClaudeSettings
from chameleon.codecs.claude.authorization import ClaudeAuthorizationSection
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesSection
from chameleon.codecs.claude.directives import ClaudeDirectivesSection
from chameleon.codecs.claude.environment import ClaudeEnvironmentSection
from chameleon.codecs.claude.governance import ClaudeGovernanceSection
from chameleon.codecs.claude.identity import ClaudeIdentitySection
from chameleon.codecs.claude.interface import ClaudeInterfaceSection
from chameleon.codecs.claude.lifecycle import ClaudeLifecycleSection
from chameleon.io.json import dump_json, load_json
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.targets._protocol import safe_validate_section


class ClaudeAssembler:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE

    SETTINGS_JSON: ClassVar[str] = "settings/settings.json"
    DOTCLAUDE_JSON: ClassVar[str] = "settings/dotfiles/claude.json"

    files: ClassVar[tuple[FileSpec, ...]] = (
        FileSpec(
            live_path="~/.claude/settings.json",
            repo_path=SETTINGS_JSON,
            ownership=FileOwnership.FULL,
            format=FileFormat.JSON,
        ),
        FileSpec(
            live_path="~/.claude.json",
            repo_path=DOTCLAUDE_JSON,
            ownership=FileOwnership.PARTIAL,
            format=FileFormat.JSON,
            owned_keys=frozenset({"mcpServers"}),
        ),
    )

    # `full_model` is the upstream-canonized root model. The schema-drift
    # test walks every codec's claimed_paths through it; codecs reference
    # field names that must exist in this model.
    full_model: ClassVar[type[BaseModel]] = ClaudeSettings

    @staticmethod
    def assemble(  # noqa: PLR0912 — fans out across 8 domains by design
        per_domain: Mapping[Domains, BaseModel],
        passthrough: Mapping[str, object],
        *,
        existing: Mapping[str, bytes] | None = None,
    ) -> dict[str, bytes]:
        settings_obj: dict[str, object] = {}

        identity = per_domain.get(Domains.IDENTITY)
        if isinstance(identity, ClaudeIdentitySection):
            for k, v in identity.model_dump(exclude_none=True).items():
                settings_obj[k] = v

        directives = per_domain.get(Domains.DIRECTIVES)
        if isinstance(directives, ClaudeDirectivesSection):
            for k, v in directives.model_dump(exclude_none=True, exclude_defaults=True).items():
                settings_obj[k] = v

        environment = per_domain.get(Domains.ENVIRONMENT)
        if isinstance(environment, ClaudeEnvironmentSection):
            settings_obj.update(environment.model_dump(exclude_none=True))

        authorization = per_domain.get(Domains.AUTHORIZATION)
        if isinstance(authorization, ClaudeAuthorizationSection):
            for k, v in authorization.model_dump(exclude_none=True, exclude_defaults=True).items():
                settings_obj[k] = v

        lifecycle = per_domain.get(Domains.LIFECYCLE)
        if isinstance(lifecycle, ClaudeLifecycleSection):
            for k, v in lifecycle.model_dump(exclude_none=True).items():
                settings_obj[k] = v

        interface = per_domain.get(Domains.INTERFACE)
        if isinstance(interface, ClaudeInterfaceSection):
            for k, v in interface.model_dump(exclude_none=True, exclude_defaults=True).items():
                settings_obj[k] = v

        governance = per_domain.get(Domains.GOVERNANCE)
        if isinstance(governance, ClaudeGovernanceSection):
            for k, v in governance.model_dump(exclude_none=True).items():
                settings_obj[k] = v

        capabilities = per_domain.get(Domains.CAPABILITIES)
        dotclaude_overlay: dict[str, object] = {}
        if isinstance(capabilities, ClaudeCapabilitiesSection):
            dotclaude_overlay["mcpServers"] = {
                k: v.model_dump(exclude_none=True) for k, v in capabilities.mcpServers.items()
            }
            # ``enabledPlugins`` and ``extraKnownMarketplaces`` live in
            # settings.json (NOT ~/.claude.json — Claude's user-level plugin
            # config is in the same file as everything else).
            if capabilities.enabled_plugins:
                settings_obj["enabledPlugins"] = dict(capabilities.enabled_plugins)
            if capabilities.extra_known_marketplaces:
                settings_obj["extraKnownMarketplaces"] = {
                    name: mp.model_dump(by_alias=True, exclude_none=True)
                    for name, mp in capabilities.extra_known_marketplaces.items()
                }

        # Splice pass-through into settings.json verbatim.
        for k, v in passthrough.items():
            if k not in settings_obj:
                settings_obj[k] = v

        dotclaude_obj: dict[str, object] = {}
        if existing is not None and ClaudeAssembler.DOTCLAUDE_JSON in existing:
            loaded = load_json(existing[ClaudeAssembler.DOTCLAUDE_JSON]) or {}
            if isinstance(loaded, dict):
                dotclaude_obj = loaded
        dotclaude_obj.update(dotclaude_overlay)

        return {
            ClaudeAssembler.SETTINGS_JSON: dump_json(settings_obj).encode("utf-8"),
            ClaudeAssembler.DOTCLAUDE_JSON: dump_json(dotclaude_obj).encode("utf-8"),
        }

    @staticmethod
    def disassemble(  # noqa: PLR0912, PLR0915 — fans across 8 domains plus capabilities multi-file
        files: Mapping[str, bytes],
        *,
        ctx: TranspileCtx | None = None,
    ) -> tuple[dict[Domains, BaseModel], dict[str, object]]:
        """Disassemble Claude live files into per-domain sections + bag.

        ``ctx`` is optional. When supplied, per-domain ``ValidationError``s
        are caught and surfaced as typed ``LossWarning``s; the offending
        keys land in pass-through. When omitted (e.g., direct unit tests),
        the catch-and-route still happens but the warning is discarded —
        callers that don't ask for warnings still don't crash.
        """
        per_domain: dict[Domains, BaseModel] = {}
        passthrough: dict[str, object] = {}

        settings_raw = files.get(ClaudeAssembler.SETTINGS_JSON, b"{}")
        settings = load_json(settings_raw) or {}
        if not isinstance(settings, dict):
            settings = {}

        identity_keys = {"model", "effortLevel", "alwaysThinkingEnabled"}
        # The legacy bool aliases (coauthoredBy, gitAttribution) are
        # community/older variants the schemastore-derived ClaudeSettings
        # does not model; the directives codec accepts them at section
        # validation time and resolves precedence across all four. We
        # route all of them here so end-to-end disassemble surfaces
        # them — see commit 7a12e47 for the codec-side rationale.
        directives_keys = {
            "outputStyle",
            "attribution",
            "includeCoAuthoredBy",
            "coauthoredBy",
            "gitAttribution",
        }
        environment_keys = {"env"}
        authorization_keys = {"permissions", "sandbox"}
        # ``hooks`` is the event-keyed hook bindings object (P1-B,
        # parity-gap.md). Routing it here means the lifecycle codec —
        # not the pass-through bag — owns the operator's hook config.
        lifecycle_keys = {"cleanupPeriodDays", "hooks"}
        interface_keys = {
            "tui",
            "statusLine",
            "voiceEnabled",
            "prefersReducedMotion",
        }
        governance_keys = {"autoUpdatesChannel", "minimumVersion"}
        # Capabilities keys that live in settings.json (the MCP keys live in
        # ~/.claude.json — see below). The plugin/marketplace keys are added
        # in P1-A; both are claimed by ``ClaudeCapabilitiesCodec``.
        capabilities_settings_keys = {"enabledPlugins", "extraKnownMarketplaces"}

        def _validate(
            section_cls: type[BaseModel],
            section_obj: Mapping[str, object],
            domain: Domains,
        ) -> None:
            safe_validate_section(
                section_cls,
                section_obj,
                domain,
                ClaudeAssembler.target,
                ctx=ctx,
                per_domain=per_domain,
                passthrough=passthrough,
            )

        identity_obj = {k: v for k, v in settings.items() if k in identity_keys}
        if identity_obj:
            _validate(ClaudeIdentitySection, identity_obj, Domains.IDENTITY)
        directives_obj = {k: v for k, v in settings.items() if k in directives_keys}
        if directives_obj:
            _validate(ClaudeDirectivesSection, directives_obj, Domains.DIRECTIVES)
        environment_obj = {k: v for k, v in settings.items() if k in environment_keys}
        if environment_obj:
            _validate(ClaudeEnvironmentSection, environment_obj, Domains.ENVIRONMENT)
        authorization_obj = {k: v for k, v in settings.items() if k in authorization_keys}
        if authorization_obj:
            _validate(ClaudeAuthorizationSection, authorization_obj, Domains.AUTHORIZATION)
        lifecycle_obj = {k: v for k, v in settings.items() if k in lifecycle_keys}
        if lifecycle_obj:
            _validate(ClaudeLifecycleSection, lifecycle_obj, Domains.LIFECYCLE)
        interface_obj = {k: v for k, v in settings.items() if k in interface_keys}
        if interface_obj:
            _validate(ClaudeInterfaceSection, interface_obj, Domains.INTERFACE)
        governance_obj = {k: v for k, v in settings.items() if k in governance_keys}
        if governance_obj:
            _validate(ClaudeGovernanceSection, governance_obj, Domains.GOVERNANCE)

        claimed = (
            identity_keys
            | directives_keys
            | environment_keys
            | authorization_keys
            | lifecycle_keys
            | interface_keys
            | governance_keys
            | capabilities_settings_keys
        )
        for k, v in settings.items():
            if k not in claimed and k not in passthrough:
                passthrough[k] = v

        # Capabilities is unique: its keys span TWO files. The mcp_servers
        # live in ~/.claude.json; the enabledPlugins / extraKnownMarketplaces
        # live in settings.json. Both feed the same codec section.
        dotclaude_raw = files.get(ClaudeAssembler.DOTCLAUDE_JSON, b"{}")
        dotclaude = load_json(dotclaude_raw) or {}
        capabilities_obj: dict[str, object] = {}
        if isinstance(dotclaude, dict) and "mcpServers" in dotclaude:
            capabilities_obj["mcpServers"] = dotclaude["mcpServers"]
        if "enabledPlugins" in settings:
            capabilities_obj["enabledPlugins"] = settings["enabledPlugins"]
        if "extraKnownMarketplaces" in settings:
            capabilities_obj["extraKnownMarketplaces"] = settings["extraKnownMarketplaces"]
        if capabilities_obj:
            _validate(ClaudeCapabilitiesSection, capabilities_obj, Domains.CAPABILITIES)

        return per_domain, passthrough


__all__ = ["ClaudeAssembler"]
