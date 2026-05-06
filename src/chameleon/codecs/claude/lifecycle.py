"""Claude codec for the lifecycle domain.

V0 thin slice:
  cleanup_period_days  ↔ cleanupPeriodDays

The remaining lifecycle surface (hooks, telemetry, history) sits behind
LossWarning paths until the dedicated lifecycle spec lands. Operators
who put `lifecycle.hooks` etc. in their neutral file get a typed
warning that the values are accepted but not propagated to Claude.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.lifecycle import Lifecycle


class ClaudeLifecycleSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cleanupPeriodDays: int | None = None  # noqa: N815


class ClaudeLifecycleCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.LIFECYCLE
    target_section: ClassVar[type[BaseModel]] = ClaudeLifecycleSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {FieldPath(segments=("cleanupPeriodDays",))}
    )

    @staticmethod
    def to_target(model: Lifecycle, ctx: TranspileCtx) -> ClaudeLifecycleSection:
        section = ClaudeLifecycleSection()
        if model.cleanup_period_days is not None:
            section.cleanupPeriodDays = model.cleanup_period_days
        if model.hooks:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "lifecycle.hooks not propagated to Claude in V0; "
                        "see §15.2 for the dedicated lifecycle spec"
                    ),
                )
            )
        if model.telemetry.exporter is not None or model.telemetry.endpoint is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CLAUDE,
                    message="lifecycle.telemetry not propagated to Claude in V0 (§15.2)",
                )
            )
        return section

    @staticmethod
    def from_target(section: ClaudeLifecycleSection, ctx: TranspileCtx) -> Lifecycle:
        return Lifecycle(cleanup_period_days=section.cleanupPeriodDays)


__all__ = ["ClaudeLifecycleCodec", "ClaudeLifecycleSection"]
