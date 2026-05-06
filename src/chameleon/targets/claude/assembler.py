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
from chameleon.codecs.claude import ClaudeSettings
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesSection
from chameleon.codecs.claude.directives import ClaudeDirectivesSection
from chameleon.codecs.claude.environment import ClaudeEnvironmentSection
from chameleon.codecs.claude.identity import ClaudeIdentitySection
from chameleon.io.json import dump_json, load_json
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains


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
    def assemble(
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

        capabilities = per_domain.get(Domains.CAPABILITIES)
        dotclaude_overlay: dict[str, object] = {}
        if isinstance(capabilities, ClaudeCapabilitiesSection):
            dotclaude_overlay["mcpServers"] = {
                k: v.model_dump(exclude_none=True) for k, v in capabilities.mcpServers.items()
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
    def disassemble(
        files: Mapping[str, bytes],
    ) -> tuple[dict[Domains, BaseModel], dict[str, object]]:
        per_domain: dict[Domains, BaseModel] = {}
        passthrough: dict[str, object] = {}

        settings_raw = files.get(ClaudeAssembler.SETTINGS_JSON, b"{}")
        settings = load_json(settings_raw) or {}
        if not isinstance(settings, dict):
            settings = {}

        identity_keys = {"model", "effortLevel", "alwaysThinkingEnabled"}
        directives_keys = {"outputStyle", "attribution"}
        environment_keys = {"env"}

        identity_obj = {k: v for k, v in settings.items() if k in identity_keys}
        if identity_obj:
            per_domain[Domains.IDENTITY] = ClaudeIdentitySection.model_validate(identity_obj)
        directives_obj = {k: v for k, v in settings.items() if k in directives_keys}
        if directives_obj:
            per_domain[Domains.DIRECTIVES] = ClaudeDirectivesSection.model_validate(directives_obj)
        environment_obj = {k: v for k, v in settings.items() if k in environment_keys}
        if environment_obj:
            per_domain[Domains.ENVIRONMENT] = ClaudeEnvironmentSection.model_validate(
                environment_obj
            )

        claimed = identity_keys | directives_keys | environment_keys
        for k, v in settings.items():
            if k not in claimed:
                passthrough[k] = v

        dotclaude_raw = files.get(ClaudeAssembler.DOTCLAUDE_JSON, b"{}")
        dotclaude = load_json(dotclaude_raw) or {}
        if isinstance(dotclaude, dict) and "mcpServers" in dotclaude:
            section_obj = {"mcpServers": dotclaude["mcpServers"]}
            per_domain[Domains.CAPABILITIES] = ClaudeCapabilitiesSection.model_validate(section_obj)

        return per_domain, passthrough


__all__ = ["ClaudeAssembler"]
