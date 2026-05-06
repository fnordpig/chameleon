"""Codex codec for the governance domain.

V0 thin slice:
  features                    ↔ [features]
  trust.trusted_paths         ↔ [projects."<path>"].trust_level = "trusted"
  trust.untrusted_paths       ↔ [projects."<path>"].trust_level = "untrusted"
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.governance import Governance, Trust


class _CodexProject(BaseModel):
    model_config = ConfigDict(extra="allow")
    trust_level: str | None = None  # "trusted" | "untrusted"


class CodexGovernanceSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    features: dict[str, bool] = Field(default_factory=dict)
    projects: dict[str, _CodexProject] = Field(default_factory=dict)


class CodexGovernanceCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.GOVERNANCE
    target_section: ClassVar[type[BaseModel]] = CodexGovernanceSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("features",)),
            FieldPath(segments=("projects",)),
        }
    )

    @staticmethod
    def to_target(model: Governance, ctx: TranspileCtx) -> CodexGovernanceSection:
        section = CodexGovernanceSection()
        if model.features:
            section.features = dict(model.features)
        for path in model.trust.trusted_paths:
            section.projects[path] = _CodexProject(trust_level="trusted")
        for path in model.trust.untrusted_paths:
            section.projects[path] = _CodexProject(trust_level="untrusted")
        if model.updates.channel is not None or model.updates.minimum_version is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.GOVERNANCE,
                    target=BUILTIN_CODEX,
                    message=(
                        "governance.updates has no clean Codex mapping "
                        "(Codex uses a single `check_for_update_on_startup` bool)"
                    ),
                )
            )
        return section

    @staticmethod
    def from_target(section: CodexGovernanceSection, ctx: TranspileCtx) -> Governance:
        trust = Trust()
        for path, project in section.projects.items():
            if project.trust_level == "trusted":
                trust.trusted_paths.append(path)
            elif project.trust_level == "untrusted":
                trust.untrusted_paths.append(path)
            else:
                ctx.warn(
                    LossWarning(
                        domain=Domains.GOVERNANCE,
                        target=BUILTIN_CODEX,
                        message=(
                            f"unknown projects.{path}.trust_level {project.trust_level!r}; dropping"
                        ),
                    )
                )
        return Governance(features=dict(section.features), trust=trust)


__all__ = ["CodexGovernanceCodec", "CodexGovernanceSection"]
