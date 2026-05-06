"""Codec, Assembler, and TranspileCtx protocol/types.

Per §8.1 of the design spec: codecs are pure function pairs that
consume typed Pydantic models and produce typed Pydantic models. The
disassembler routes input by typed field-path traversal; codecs never
touch raw dicts.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.schema._constants import Domains


class LossWarning(BaseModel):
    """A typed warning emitted by a codec when encoding is documented-lossy."""

    model_config = ConfigDict(frozen=True)

    domain: Domains
    target: TargetId
    message: str
    field_path: FieldPath | None = None


class TranspileCtx:
    """Per-merge mutable context passed to every codec invocation.

    Carries the warning collector, resolved profile name (if any), and
    the registered targets registry. Codecs MUST NOT mutate the registry
    or perform I/O.
    """

    def __init__(self, profile_name: str | None = None) -> None:
        self.profile_name = profile_name
        self.warnings: list[LossWarning] = []

    def warn(self, w: LossWarning) -> None:
        self.warnings.append(w)


@runtime_checkable
class Codec(Protocol):
    """Codec protocol — pure (target, domain) translator.

    Implementations are simple classes (or modules) with class-level
    attributes. The `target_section` is a typed Pydantic submodel that
    mirrors the SHAPE of the target's FullTargetModel restricted to
    `claimed_paths`. The disassembler uses these paths to extract
    section values during reverse-codec routing.
    """

    target: ClassVar[TargetId]
    domain: ClassVar[Domains]
    claimed_paths: ClassVar[frozenset[FieldPath]]
    target_section: ClassVar[type[BaseModel]]

    @staticmethod
    def to_target(model: BaseModel, ctx: TranspileCtx) -> BaseModel: ...

    @staticmethod
    def from_target(section: BaseModel, ctx: TranspileCtx) -> BaseModel: ...


def validate_claimed_paths(codec: Codec, full_model: type[BaseModel]) -> None:
    """Walk each codec's claimed_paths through `full_model` to verify each
    path resolves to an existing field. Raises ValueError on the first
    missing path. The schema-drift check — when upstream regeneration
    removes a field, the registry refuses to load the stale codec.
    """
    for path in codec.claimed_paths:
        current: type[BaseModel] | None = full_model
        for seg in path.segments:
            if current is None:
                msg = (
                    f"codec {codec.target}/{codec.domain.value} claims path "
                    f"{path.render()!r} but {seg!r} is reached through a non-model "
                    f"type; perhaps the upstream schema changed?"
                )
                raise ValueError(msg)
            fields = getattr(current, "model_fields", None)
            if fields is None or seg not in fields:
                msg = (
                    f"codec {codec.target}/{codec.domain.value} claims path "
                    f"{path.render()!r} but field {seg!r} does not exist in "
                    f"{current.__name__}; the upstream schema regeneration may "
                    f"have removed it."
                )
                raise ValueError(msg)
            field = fields[seg]
            ann = field.annotation
            current = ann if isinstance(ann, type) and issubclass(ann, BaseModel) else None


__all__ = [
    "Codec",
    "LossWarning",
    "TranspileCtx",
    "validate_claimed_paths",
]
