"""Codex assembler — composes/decomposes config.toml (and requirements.toml).

V0 owns:
  - ~/.codex/config.toml         (FULL ownership)
  - ~/.codex/requirements.toml   (FULL — managed enforcement; not exercised by V0
                                   codecs but the file is declared in `files` for
                                   future use).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import tomlkit
from pydantic import BaseModel

from chameleon._types import FileFormat, FileOwnership, FileSpec, TargetId
from chameleon.codecs.codex import CodexConfig
from chameleon.codecs.codex.authorization import CodexAuthorizationSection
from chameleon.codecs.codex.capabilities import CodexCapabilitiesSection
from chameleon.codecs.codex.directives import CodexDirectivesSection
from chameleon.codecs.codex.environment import CodexEnvironmentSection
from chameleon.codecs.codex.governance import CodexGovernanceSection
from chameleon.codecs.codex.identity import CodexIdentitySection
from chameleon.codecs.codex.interface import CodexInterfaceSection
from chameleon.codecs.codex.lifecycle import CodexLifecycleSection
from chameleon.io.toml import dump_toml, load_toml
from chameleon.schema._constants import BUILTIN_CODEX, Domains


class CodexAssembler:
    target: ClassVar[TargetId] = BUILTIN_CODEX

    CONFIG_TOML: ClassVar[str] = "settings/config.toml"
    REQUIREMENTS_TOML: ClassVar[str] = "settings/requirements.toml"

    files: ClassVar[tuple[FileSpec, ...]] = (
        FileSpec(
            live_path="~/.codex/config.toml",
            repo_path=CONFIG_TOML,
            ownership=FileOwnership.FULL,
            format=FileFormat.TOML,
        ),
        FileSpec(
            live_path="~/.codex/requirements.toml",
            repo_path=REQUIREMENTS_TOML,
            ownership=FileOwnership.FULL,
            format=FileFormat.TOML,
        ),
    )

    full_model: ClassVar[type[BaseModel]] = CodexConfig

    @staticmethod
    def assemble(  # noqa: PLR0912, PLR0915 — fans out across 8 domains by design
        per_domain: Mapping[Domains, BaseModel],
        passthrough: Mapping[str, object],
        *,
        existing: Mapping[str, bytes] | None = None,
    ) -> dict[str, bytes]:
        doc = tomlkit.document()

        identity = per_domain.get(Domains.IDENTITY)
        if isinstance(identity, CodexIdentitySection):
            for k, v in identity.model_dump(exclude_none=True).items():
                doc[k] = v

        directives = per_domain.get(Domains.DIRECTIVES)
        if isinstance(directives, CodexDirectivesSection):
            for k, v in directives.model_dump(exclude_none=True).items():
                doc[k] = v

        capabilities = per_domain.get(Domains.CAPABILITIES)
        if isinstance(capabilities, CodexCapabilitiesSection):
            mcp_table = tomlkit.table()
            for name, server in capabilities.mcp_servers.items():
                server_table = tomlkit.table()
                for k, v in server.model_dump(exclude_none=True).items():
                    server_table[k] = v
                mcp_table[name] = server_table
            doc["mcp_servers"] = mcp_table

        environment = per_domain.get(Domains.ENVIRONMENT)
        if isinstance(environment, CodexEnvironmentSection):
            sep = tomlkit.table()
            sep["set"] = environment.shell_environment_policy.set
            doc["shell_environment_policy"] = sep

        authorization = per_domain.get(Domains.AUTHORIZATION)
        if isinstance(authorization, CodexAuthorizationSection):
            if authorization.sandbox_mode is not None:
                doc["sandbox_mode"] = authorization.sandbox_mode
            if authorization.sandbox_workspace_write.writable_roots:
                ws = tomlkit.table()
                ws["writable_roots"] = list(authorization.sandbox_workspace_write.writable_roots)
                doc["sandbox_workspace_write"] = ws

        lifecycle = per_domain.get(Domains.LIFECYCLE)
        if isinstance(lifecycle, CodexLifecycleSection):
            history_dump = lifecycle.history.model_dump(exclude_none=True)
            if history_dump:
                history_table = tomlkit.table()
                for k, v in history_dump.items():
                    history_table[k] = v
                doc["history"] = history_table

        interface = per_domain.get(Domains.INTERFACE)
        if isinstance(interface, CodexInterfaceSection):
            tui_dump = interface.tui.model_dump(exclude_none=True)
            if tui_dump:
                tui_table = tomlkit.table()
                for k, v in tui_dump.items():
                    tui_table[k] = v
                doc["tui"] = tui_table
            if interface.file_opener is not None:
                doc["file_opener"] = interface.file_opener

        governance = per_domain.get(Domains.GOVERNANCE)
        if isinstance(governance, CodexGovernanceSection):
            if governance.features:
                features_table = tomlkit.table()
                for k, v in governance.features.items():
                    features_table[k] = v
                doc["features"] = features_table
            if governance.projects:
                projects_table = tomlkit.table()
                for path, project in governance.projects.items():
                    project_table = tomlkit.table()
                    if project.trust_level is not None:
                        project_table["trust_level"] = project.trust_level
                    projects_table[path] = project_table
                doc["projects"] = projects_table

        # Pass-through: top-level Codex keys we don't claim (e.g. personality).
        for k, v in passthrough.items():
            if k not in doc:
                doc[k] = v

        return {CodexAssembler.CONFIG_TOML: dump_toml(doc).encode("utf-8")}

    @staticmethod
    def disassemble(
        files: Mapping[str, bytes],
    ) -> tuple[dict[Domains, BaseModel], dict[str, object]]:
        per_domain: dict[Domains, BaseModel] = {}
        passthrough: dict[str, object] = {}

        raw = files.get(CodexAssembler.CONFIG_TOML, b"")
        doc = load_toml(raw.decode("utf-8")) if raw else {}
        as_dict = dict(doc)

        identity_keys = {"model", "model_reasoning_effort"}
        directives_keys = {"model_instructions_file", "commit_attribution"}
        capabilities_keys = {"mcp_servers"}
        environment_keys = {"shell_environment_policy"}
        authorization_keys = {"sandbox_mode", "sandbox_workspace_write"}
        lifecycle_keys = {"history"}
        interface_keys = {"tui", "file_opener"}
        governance_keys = {"features", "projects"}

        identity_obj = {k: v for k, v in as_dict.items() if k in identity_keys}
        if identity_obj:
            per_domain[Domains.IDENTITY] = CodexIdentitySection.model_validate(identity_obj)
        directives_obj = {k: v for k, v in as_dict.items() if k in directives_keys}
        if directives_obj:
            per_domain[Domains.DIRECTIVES] = CodexDirectivesSection.model_validate(directives_obj)
        capabilities_obj = {k: v for k, v in as_dict.items() if k in capabilities_keys}
        if capabilities_obj:
            per_domain[Domains.CAPABILITIES] = CodexCapabilitiesSection.model_validate(
                capabilities_obj
            )
        environment_obj = {k: v for k, v in as_dict.items() if k in environment_keys}
        if environment_obj:
            per_domain[Domains.ENVIRONMENT] = CodexEnvironmentSection.model_validate(
                environment_obj
            )
        authorization_obj = {k: v for k, v in as_dict.items() if k in authorization_keys}
        if authorization_obj:
            per_domain[Domains.AUTHORIZATION] = CodexAuthorizationSection.model_validate(
                authorization_obj
            )
        lifecycle_obj = {k: v for k, v in as_dict.items() if k in lifecycle_keys}
        if lifecycle_obj:
            per_domain[Domains.LIFECYCLE] = CodexLifecycleSection.model_validate(lifecycle_obj)
        interface_obj = {k: v for k, v in as_dict.items() if k in interface_keys}
        if interface_obj:
            per_domain[Domains.INTERFACE] = CodexInterfaceSection.model_validate(interface_obj)
        governance_obj = {k: v for k, v in as_dict.items() if k in governance_keys}
        if governance_obj:
            per_domain[Domains.GOVERNANCE] = CodexGovernanceSection.model_validate(governance_obj)

        claimed = (
            identity_keys
            | directives_keys
            | capabilities_keys
            | environment_keys
            | authorization_keys
            | lifecycle_keys
            | interface_keys
            | governance_keys
        )
        for k, v in as_dict.items():
            if k not in claimed:
                passthrough[k] = v

        return per_domain, passthrough


__all__ = ["CodexAssembler"]
