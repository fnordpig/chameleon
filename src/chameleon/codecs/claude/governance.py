"""Claude codec for the governance domain (updates channel + minimum version)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.governance import Governance, Updates, UpdatesChannel


class ClaudeGovernanceSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    autoUpdatesChannel: str | None = None  # noqa: N815
    minimumVersion: str | None = None  # noqa: N815


class ClaudeGovernanceCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.GOVERNANCE
    target_section: ClassVar[type[BaseModel]] = ClaudeGovernanceSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("autoUpdatesChannel",)),
            FieldPath(segments=("minimumVersion",)),
        }
    )

    @staticmethod
    def to_target(model: Governance, ctx: TranspileCtx) -> ClaudeGovernanceSection:
        section = ClaudeGovernanceSection()
        if model.updates.channel is not None:
            section.autoUpdatesChannel = model.updates.channel.value
        if model.updates.minimum_version is not None:
            section.minimumVersion = model.updates.minimum_version
        if model.features:
            ctx.warn(
                LossWarning(
                    domain=Domains.GOVERNANCE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "governance.features is a Codex-only construct; "
                        "Claude exposes feature toggles via env vars"
                    ),
                )
            )
        if model.trust.trusted_paths or model.trust.untrusted_paths:
            ctx.warn(
                LossWarning(
                    domain=Domains.GOVERNANCE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "governance.trust managed via ~/.claude.json's per-project "
                        "trust state, not settings.json (§15.4)"
                    ),
                )
            )
        return section

    @staticmethod
    def from_target(section: ClaudeGovernanceSection, ctx: TranspileCtx) -> Governance:
        updates = Updates()
        if section.autoUpdatesChannel is not None:
            try:
                updates.channel = UpdatesChannel(section.autoUpdatesChannel)
            except ValueError:
                ctx.warn(
                    LossWarning(
                        domain=Domains.GOVERNANCE,
                        target=BUILTIN_CLAUDE,
                        message=(
                            f"unknown autoUpdatesChannel {section.autoUpdatesChannel!r}; dropping"
                        ),
                    )
                )
        if section.minimumVersion is not None:
            updates.minimum_version = section.minimumVersion
        return Governance(updates=updates)


__all__ = ["ClaudeGovernanceCodec", "ClaudeGovernanceSection"]
