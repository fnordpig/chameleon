"""Codex target — wires assembler + V0 codecs."""

from __future__ import annotations

from typing import ClassVar

from chameleon._types import TargetId
from chameleon.codecs._protocol import Codec
from chameleon.codecs.codex.authorization import CodexAuthorizationCodec
from chameleon.codecs.codex.capabilities import CodexCapabilitiesCodec
from chameleon.codecs.codex.directives import CodexDirectivesCodec
from chameleon.codecs.codex.environment import CodexEnvironmentCodec
from chameleon.codecs.codex.governance import CodexGovernanceCodec
from chameleon.codecs.codex.identity import CodexIdentityCodec
from chameleon.codecs.codex.interface import CodexInterfaceCodec
from chameleon.codecs.codex.lifecycle import CodexLifecycleCodec
from chameleon.schema._constants import BUILTIN_CODEX
from chameleon.targets.codex.assembler import CodexAssembler


class CodexTarget:
    target_id: ClassVar[TargetId] = BUILTIN_CODEX
    assembler: ClassVar[type[CodexAssembler]] = CodexAssembler
    codecs: ClassVar[tuple[type[Codec], ...]] = (
        CodexIdentityCodec,
        CodexDirectivesCodec,
        CodexCapabilitiesCodec,
        CodexEnvironmentCodec,
        CodexAuthorizationCodec,
        CodexLifecycleCodec,
        CodexInterfaceCodec,
        CodexGovernanceCodec,
    )


__all__ = ["CodexTarget"]
