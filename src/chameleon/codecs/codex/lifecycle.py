"""Codex codec for the lifecycle domain.

V0 thin slice:
  history.persistence  ↔ [history].persistence
  history.max_bytes    ↔ [history].max_bytes
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.lifecycle import History, HistoryPersistence, Lifecycle


class _CodexHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persistence: str | None = None
    max_bytes: int | None = None


class CodexLifecycleSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history: _CodexHistory = Field(default_factory=_CodexHistory)


class CodexLifecycleCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.LIFECYCLE
    target_section: ClassVar[type[BaseModel]] = CodexLifecycleSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("history", "persistence")),
            FieldPath(segments=("history", "max_bytes")),
        }
    )

    @staticmethod
    def to_target(model: Lifecycle, ctx: TranspileCtx) -> CodexLifecycleSection:
        section = CodexLifecycleSection()
        if model.history.persistence is not None:
            section.history.persistence = model.history.persistence.value
        if model.history.max_bytes is not None:
            section.history.max_bytes = model.history.max_bytes
        if model.cleanup_period_days is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CODEX,
                    message="lifecycle.cleanup_period_days has no Codex equivalent (§15.2)",
                )
            )
        if model.hooks:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CODEX,
                    message="lifecycle.hooks not propagated to Codex in V0 (§15.2)",
                )
            )
        return section

    @staticmethod
    def from_target(section: CodexLifecycleSection, ctx: TranspileCtx) -> Lifecycle:
        history = History()
        if section.history.persistence is not None:
            try:
                history.persistence = HistoryPersistence(section.history.persistence)
            except ValueError:
                ctx.warn(
                    LossWarning(
                        domain=Domains.LIFECYCLE,
                        target=BUILTIN_CODEX,
                        message=(
                            f"unknown history.persistence {section.history.persistence!r}; dropping"
                        ),
                    )
                )
        if section.history.max_bytes is not None:
            history.max_bytes = section.history.max_bytes
        return Lifecycle(history=history)


__all__ = ["CodexLifecycleCodec", "CodexLifecycleSection"]
