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


def _normalize_codex_features(
    input_features: dict[str, bool], ctx: TranspileCtx
) -> dict[str, bool]:
    """Normalize deprecated Codex feature aliases.

    ``codex_hooks`` is deprecated in recent Codex versions; map it to the
    canonical ``hooks`` key to keep emitted config warning-free.
    """
    if "codex_hooks" not in input_features:
        return dict(input_features)

    # Copy the live map first so we can apply canonical key migration.
    features = dict(input_features)
    canonical_value = features.pop("codex_hooks")
    if "hooks" in features and features["hooks"] != canonical_value:
        ctx.warn(
            LossWarning(
                domain=Domains.GOVERNANCE,
                target=BUILTIN_CODEX,
                message=(
                    "governance.features contains both 'hooks' and "
                    "'codex_hooks' with conflicting values; using 'hooks'"
                ),
                field_path=FieldPath(segments=("features",)),
            )
        )
    else:
        features["hooks"] = canonical_value
    return features


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
            section.features = _normalize_codex_features(model.features, ctx)

        # Detect duplicate paths within either list. The wire shape
        # (``projects.<path>.trust_level`` keyed by path string) cannot
        # represent duplicates — a second occurrence collapses onto the
        # first dict slot. Surface the loss explicitly.
        trusted_dupes = sorted(
            {p for p in model.trust.trusted_paths if model.trust.trusted_paths.count(p) > 1}
        )
        untrusted_dupes = sorted(
            {p for p in model.trust.untrusted_paths if model.trust.untrusted_paths.count(p) > 1}
        )
        if trusted_dupes or untrusted_dupes:
            ctx.warn(
                LossWarning(
                    domain=Domains.GOVERNANCE,
                    target=BUILTIN_CODEX,
                    message=(
                        "Trust.duplicate_paths — Codex `projects.<path>` "
                        "is keyed by path string, so duplicate entries within a "
                        "trust list collapse to a single wire key; "
                        f"trusted_paths duplicates={trusted_dupes!r}, "
                        f"untrusted_paths duplicates={untrusted_dupes!r}"
                    ),
                )
            )

        # Detect paths that appear in BOTH trusted and untrusted. The
        # wire shape can hold only one trust_level per path, so the
        # second-written list clobbers the first (last-write-wins:
        # untrusted_paths overrides trusted_paths because it is
        # serialised second below). Per A-TRUST, surface the loss.
        trusted_set = set(model.trust.trusted_paths)
        untrusted_set = set(model.trust.untrusted_paths)
        both = sorted(trusted_set & untrusted_set)
        if both:
            ctx.warn(
                LossWarning(
                    domain=Domains.GOVERNANCE,
                    target=BUILTIN_CODEX,
                    message=(
                        "Trust.both_trusted_and_untrusted — paths appear "
                        "in both trusted_paths and untrusted_paths; Codex stores "
                        "a single trust_level per path, so untrusted_paths wins "
                        f"(serialised last); paths={both!r}"
                    ),
                )
            )

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
        features = dict(section.features)
        if "codex_hooks" in section.features and "hooks" not in section.features:
            features["hooks"] = section.features["codex_hooks"]
        elif "codex_hooks" in section.features and section.features.get(
            "codex_hooks"
        ) != section.features.get("hooks"):
            ctx.warn(
                LossWarning(
                    domain=Domains.GOVERNANCE,
                    target=BUILTIN_CODEX,
                    message=(
                        "disagreement between Codex features 'hooks' and "
                        "deprecated 'codex_hooks'; canonicalizing as 'hooks'"
                    ),
                    field_path=FieldPath(segments=("features",)),
                )
            )
        features.pop("codex_hooks", None)
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
        return Governance(features=features, trust=trust)


__all__ = ["CodexGovernanceCodec", "CodexGovernanceSection"]
