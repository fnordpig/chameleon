"""Target protocol: pairs an Assembler with a set of codecs and a TargetId."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel

from chameleon._types import FileSpec, TargetId
from chameleon.codecs._protocol import Codec
from chameleon.schema._constants import Domains


@runtime_checkable
class Assembler(Protocol):
    target: ClassVar[TargetId]
    full_model: ClassVar[type[BaseModel]]
    files: ClassVar[tuple[FileSpec, ...]]

    @staticmethod
    def assemble(
        per_domain: Mapping[Domains, BaseModel],
        passthrough: Mapping[str, object],
        *,
        existing: Mapping[str, bytes] | None = None,
    ) -> Mapping[str, bytes]: ...

    @staticmethod
    def disassemble(
        files: Mapping[str, bytes],
    ) -> tuple[Mapping[Domains, BaseModel], dict[str, object]]: ...


@runtime_checkable
class Target(Protocol):
    target_id: ClassVar[TargetId]
    assembler: ClassVar[type[Assembler]]
    codecs: ClassVar[tuple[type[Codec], ...]]


__all__ = ["Assembler", "Target"]
