"""Codex target — wires assembler + codecs.

Phase-of-work placeholder: this declares the entry point that pyproject.toml
references. The real assembler and codec wiring lands in the
codex-assembler task.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from chameleon._types import TargetId
from chameleon.codecs._protocol import Codec
from chameleon.schema._constants import BUILTIN_CODEX


class _PlaceholderAssembler(BaseModel):
    """Placeholder until codex-assembler lands."""


class CodexTarget:
    target_id: ClassVar[TargetId] = BUILTIN_CODEX
    assembler: ClassVar[type] = _PlaceholderAssembler
    codecs: ClassVar[tuple[type[Codec], ...]] = ()


__all__ = ["CodexTarget"]
