"""Claude target — wires assembler + codecs.

Phase-of-work placeholder: this declares the entry point that pyproject.toml
references. The real assembler and codec wiring lands in the
claude-assembler task; until then `assembler` and `codecs` are placeholders
that the registry will replace.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from chameleon._types import TargetId
from chameleon.codecs._protocol import Codec
from chameleon.schema._constants import BUILTIN_CLAUDE


class _PlaceholderAssembler(BaseModel):
    """Placeholder until claude-assembler lands."""


class ClaudeTarget:
    target_id: ClassVar[TargetId] = BUILTIN_CLAUDE
    assembler: ClassVar[type] = _PlaceholderAssembler
    codecs: ClassVar[tuple[type[Codec], ...]] = ()


__all__ = ["ClaudeTarget"]
