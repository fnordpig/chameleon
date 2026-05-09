"""Codex assembler — composes/decomposes config.toml (and requirements.toml).

V0 owns:
  - ~/.codex/config.toml         (FULL ownership)
  - ~/.codex/requirements.toml   (FULL — managed enforcement; not exercised by V0
                                   codecs but the file is declared in `files` for
                                   future use).
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from collections.abc import Mapping as ABCCMapping
from typing import ClassVar, cast

import tomlkit
from pydantic import BaseModel

from chameleon._types import FileFormat, FileOwnership, FileSpec, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
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
from chameleon.targets._protocol import (
    harvest_section_extras,
    merge_extras_into_dict,
    safe_validate_section,
)


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
    def _disassemble_config_toml(
        raw: bytes,
        *,
        ctx: TranspileCtx | None,
    ) -> dict[str, object]:
        """Parse Codex config TOML with graceful failure to warnings.

        The only failure mode is invalid TOML. In that case we emit a
        typed LossWarning and continue with an empty document so merge
        can keep moving (prefering a noisy migration note over a hard
        crash).
        """
        source = raw.decode("utf-8") if raw else ""
        if not source.strip():
            return {}
        try:
            doc = load_toml(source)
            return dict(doc)
        except Exception as exc:  # pragma: no cover - defensive parse-failure path
            if ctx is not None:
                ctx.warn(
                    LossWarning(
                        domain=Domains.GOVERNANCE,
                        target=BUILTIN_CODEX,
                        message=(
                            "could not disassemble config.toml; parse failure — fallback "
                            "to default decode path. Please migrate this file to valid TOML. "
                            f"cause={exc}"
                        ),
                    )
                )
            return {}

    @staticmethod
    def _sanitize_features(features: object) -> dict[str, object] | None:
        """Return a canonicalized features map for emission.

        ``codex_hooks`` is deprecated in current Codex versions and must not
        be written to ``config.toml``. If both legacy and canonical keys are
        present, canonical ``hooks`` wins.
        """
        if not isinstance(features, ABCCMapping):
            return None
        # tomlkit allows dotted-key style and mixed-type maps; normalize only
        # straightforward string-keyed string/bool tables.
        if not all(isinstance(k, str) for k in features):
            return None

        normalized: dict[str, object] = {}
        for key, value in features.items():
            normalized_key = cast("str", key)
            normalized[normalized_key] = value

        if "codex_hooks" in normalized:
            canonical = normalized["codex_hooks"]
            if "hooks" not in normalized:
                normalized["hooks"] = canonical
            normalized.pop("codex_hooks")

        return normalized

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
            if capabilities.mcp_servers:
                mcp_table = tomlkit.table()
                for name, server in capabilities.mcp_servers.items():
                    server_table = tomlkit.table()
                    for k, v in server.model_dump(exclude_none=True).items():
                        server_table[k] = v
                    mcp_table[name] = server_table
                doc["mcp_servers"] = mcp_table
            if capabilities.plugins:
                plugins_table = tomlkit.table()
                for plugin_key, entry in capabilities.plugins.items():
                    p_table = tomlkit.table()
                    for k, v in entry.model_dump(exclude_none=True).items():
                        p_table[k] = v
                    plugins_table[plugin_key] = p_table
                doc["plugins"] = plugins_table
            if capabilities.marketplaces:
                mp_table = tomlkit.table()
                for mp_name, mp_entry in capabilities.marketplaces.items():
                    inner = tomlkit.table()
                    for k, v in mp_entry.model_dump(exclude_none=True).items():
                        inner[k] = v
                    mp_table[mp_name] = inner
                doc["marketplaces"] = mp_table
            # top-level ``web_search`` mode (StrEnum value).
            if capabilities.web_search is not None:
                doc["web_search"] = capabilities.web_search.value

        environment = per_domain.get(Domains.ENVIRONMENT)
        if isinstance(environment, CodexEnvironmentSection):
            sep = tomlkit.table()
            sep["set"] = environment.shell_environment_policy.set
            # emit ``inherit`` when the codec set it.
            if environment.shell_environment_policy.inherit is not None:
                sep["inherit"] = environment.shell_environment_policy.inherit
            doc["shell_environment_policy"] = sep

        authorization = per_domain.get(Domains.AUTHORIZATION)
        if isinstance(authorization, CodexAuthorizationSection):
            if authorization.sandbox_mode is not None:
                doc["sandbox_mode"] = authorization.sandbox_mode
            if authorization.sandbox_workspace_write.writable_roots:
                ws = tomlkit.table()
                ws["writable_roots"] = list(authorization.sandbox_workspace_write.writable_roots)
                doc["sandbox_workspace_write"] = ws
            if authorization.approval_policy is not None:
                #  S3 — ``approval_policy`` is either a plain wire
                # string (the 4 LCD enum arms the codec round-trips) or a
                # dict (the granular ``AskForApproval4`` arm preserved
                # raw via section field typing). Both serialise cleanly
                # via tomlkit's native dict/str handling.
                doc["approval_policy"] = authorization.approval_policy
            if authorization.approvals_reviewer is not None:
                doc["approvals_reviewer"] = authorization.approvals_reviewer

        lifecycle = per_domain.get(Domains.LIFECYCLE)
        if isinstance(lifecycle, CodexLifecycleSection):
            history_dump = lifecycle.history.model_dump(exclude_none=True)
            if history_dump:
                history_table = tomlkit.table()
                for k, v in history_dump.items():
                    history_table[k] = v
                doc["history"] = history_table
            # emit the ``[otel]`` block when the codec
            # populated an exporter. ``model_dump`` flattens the
            # discriminated-union arm into a TOML-compatible dict (e.g.
            # ``{"exporter": "none"}`` for the plain-enum arm or
            # ``{"exporter": {"otlp-http": {...}}}`` for the structured
            # arm). The pydantic ``by_alias=True`` keeps the
            # ``otlp-http`` / ``otlp-grpc`` aliased keys instead of the
            # snake_case Python attribute names.
            if lifecycle.otel is not None:
                otel_dump = lifecycle.otel.model_dump(exclude_none=True, by_alias=True)
                if otel_dump:
                    otel_table = tomlkit.table()
                    for k, v in otel_dump.items():
                        otel_table[k] = v
                    doc["otel"] = otel_table

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
                normalized_features = CodexAssembler._sanitize_features(governance.features) or {}
                for k, v in normalized_features.items():
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
            if k == "features":
                sanitized = CodexAssembler._sanitize_features(v)
                if sanitized is not None:
                    doc[k] = sanitized
                    continue
            if k not in doc:
                doc[k] = v

        # B1 — sub-table preservation. For each domain section a codec
        # produced, look up the matching section in the (raw) existing
        # disassemble and recover any unclaimed inner keys parked in
        # ``__pydantic_extra__`` (the section models all carry
        # ``ConfigDict(extra="allow")``). Merge those extras back onto
        # the doc at the corresponding top-level key so partially-
        # claimed nested tables (e.g. ``[tui]`` with unclaimed
        # ``status_line`` and ``[tui.model_availability_nux]``) round-trip
        # losslessly.
        if existing is not None and existing.get(CodexAssembler.CONFIG_TOML):
            existing_domains, _ = CodexAssembler.disassemble(existing)
            CodexAssembler._merge_existing_extras(doc, existing_domains)

        return {CodexAssembler.CONFIG_TOML: dump_toml(doc).encode("utf-8")}

    @staticmethod
    def _merge_existing_extras(
        doc: object,
        existing_domains: Mapping[Domains, BaseModel],
    ) -> None:
        """Merge unclaimed sub-keys harvested from ``existing_domains``
        onto ``doc`` (a tomlkit document, which is dict-shaped).

        Each Codex codec section's field names align 1:1 with the
        top-level TOML keys the assembler emits — there is no domain-
        specific renaming on the way in or out (e.g.
        ``CodexInterfaceSection.tui`` ↔ ``[tui]``;
        ``CodexInterfaceSection.file_opener`` ↔ ``file_opener``;
        ``CodexAuthorizationSection.sandbox_workspace_write`` ↔
        ``[sandbox_workspace_write]``). The merge therefore is a single
        recursive splice of harvested extras into the doc — no per-
        domain routing required.

        ``merge_extras_into_dict`` honours the "modelled wins" rule:
        the codec's freshly-built TOML keys are kept verbatim; only
        unclaimed inner keys (and any unclaimed top-level table the
        codec didn't emit at all) are filled in from extras.
        """
        # tomlkit Document is dict-shaped; cast through MutableMapping's
        # invariant generics — at runtime the keys are str by virtue of
        # the TOML grammar.
        if not isinstance(doc, MutableMapping):  # pragma: no cover
            return
        target = cast("MutableMapping[str, object]", doc)

        for _domain, section in existing_domains.items():
            extras = harvest_section_extras(section)
            if extras:
                merge_extras_into_dict(target, extras)

    @staticmethod
    def disassemble(
        files: Mapping[str, bytes],
        *,
        ctx: TranspileCtx | None = None,
    ) -> tuple[dict[Domains, BaseModel], dict[str, object]]:
        """Disassemble Codex live files into per-domain sections + bag.

        ``ctx`` is optional. When supplied, per-domain ``ValidationError``s
        are caught and surfaced as typed ``LossWarning``s; the offending
        keys land in pass-through. Mirrors the Claude assembler's contract
        — see ``ClaudeAssembler.disassemble`` and the shared
        ``safe_validate_section`` helper for the exact shape.
        """
        per_domain: dict[Domains, BaseModel] = {}
        passthrough: dict[str, object] = {}

        raw = files.get(CodexAssembler.CONFIG_TOML, b"")
        as_dict = CodexAssembler._disassemble_config_toml(raw, ctx=ctx)

        identity_keys = {
            "model",
            "model_reasoning_effort",
            # Codex-only identity tuning knobs.
            "model_context_window",
            "model_auto_compact_token_limit",
            "model_catalog_json",
            # auth.method.
            "forced_login_method",
        }
        directives_keys = {
            "model_instructions_file",
            "commit_attribution",
            "personality",  # P1-E
            # directives.verbosity.
            "model_verbosity",
        }
        # capabilities.web_search.
        capabilities_keys = {"mcp_servers", "plugins", "marketplaces", "web_search"}
        environment_keys = {"shell_environment_policy"}
        authorization_keys = {
            "sandbox_mode",
            "sandbox_workspace_write",
            "approval_policy",
            "approvals_reviewer",
        }
        # lifecycle.telemetry.exporter ↔ otel.exporter.
        lifecycle_keys = {"history", "otel"}
        interface_keys = {"tui", "file_opener"}
        governance_keys = {"features", "projects"}

        def _validate(
            section_cls: type[BaseModel],
            section_obj: Mapping[str, object],
            domain: Domains,
        ) -> None:
            safe_validate_section(
                section_cls,
                section_obj,
                domain,
                CodexAssembler.target,
                ctx=ctx,
                per_domain=per_domain,
                passthrough=passthrough,
            )

        identity_obj = {k: v for k, v in as_dict.items() if k in identity_keys}
        if identity_obj:
            _validate(CodexIdentitySection, identity_obj, Domains.IDENTITY)
        directives_obj = {k: v for k, v in as_dict.items() if k in directives_keys}
        if directives_obj:
            _validate(CodexDirectivesSection, directives_obj, Domains.DIRECTIVES)
        capabilities_obj = {k: v for k, v in as_dict.items() if k in capabilities_keys}
        if capabilities_obj:
            _validate(CodexCapabilitiesSection, capabilities_obj, Domains.CAPABILITIES)
        environment_obj = {k: v for k, v in as_dict.items() if k in environment_keys}
        if environment_obj:
            _validate(CodexEnvironmentSection, environment_obj, Domains.ENVIRONMENT)
        authorization_obj = {k: v for k, v in as_dict.items() if k in authorization_keys}
        if authorization_obj:
            _validate(CodexAuthorizationSection, authorization_obj, Domains.AUTHORIZATION)
        lifecycle_obj = {k: v for k, v in as_dict.items() if k in lifecycle_keys}
        if lifecycle_obj:
            _validate(CodexLifecycleSection, lifecycle_obj, Domains.LIFECYCLE)
        interface_obj = {k: v for k, v in as_dict.items() if k in interface_keys}
        if interface_obj:
            _validate(CodexInterfaceSection, interface_obj, Domains.INTERFACE)
        governance_obj = {k: v for k, v in as_dict.items() if k in governance_keys}
        if governance_obj:
            _validate(CodexGovernanceSection, governance_obj, Domains.GOVERNANCE)

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
            if k not in claimed and k not in passthrough:
                passthrough[k] = v

        return per_domain, passthrough


__all__ = ["CodexAssembler"]
