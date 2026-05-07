"""Claude codec for environment.variables — maps directly to settings.json `env`.

Wave-10 §15.x — ``environment.inherit`` (``InheritPolicy`` enum: ``all``,
``core``, ``none``) has no Claude analogue. Claude inherits the parent
shell environment unconditionally; selectively inheriting a subset is
not configurable through ``settings.json``. The codec emits a typed
``LossWarning`` when neutral sets ``environment.inherit`` rather than
silently approximating one of the three values.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.environment import Environment


class ClaudeEnvironmentSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    env: dict[str, str] = Field(default_factory=dict)


class ClaudeEnvironmentCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.ENVIRONMENT
    target_section: ClassVar[type[BaseModel]] = ClaudeEnvironmentSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset({FieldPath(segments=("env",))})

    @staticmethod
    def to_target(model: Environment, ctx: TranspileCtx) -> ClaudeEnvironmentSection:
        if model.inherit is not None:
            # Wave-10 §15.x — Claude has no inherit-policy setting.
            ctx.warn(
                LossWarning(
                    domain=Domains.ENVIRONMENT,
                    target=BUILTIN_CLAUDE,
                    message=(
                        f"environment.inherit ({model.inherit.value!r}) has no "
                        "Claude analogue (Claude inherits the parent shell "
                        "environment unconditionally); dropping during to_target."
                    ),
                    field_path=FieldPath(segments=("inherit",)),
                )
            )
        return ClaudeEnvironmentSection(env=dict(model.variables))

    @staticmethod
    def from_target(section: ClaudeEnvironmentSection, ctx: TranspileCtx) -> Environment:
        return Environment(variables=dict(section.env))


__all__ = ["ClaudeEnvironmentCodec", "ClaudeEnvironmentSection"]
