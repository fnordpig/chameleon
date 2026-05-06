"""Claude target — wires assembler + V0 codecs."""

from __future__ import annotations

from typing import ClassVar

from chameleon._types import TargetId
from chameleon.codecs._protocol import Codec
from chameleon.codecs.claude.authorization import ClaudeAuthorizationCodec
from chameleon.codecs.claude.capabilities import ClaudeCapabilitiesCodec
from chameleon.codecs.claude.directives import ClaudeDirectivesCodec
from chameleon.codecs.claude.environment import ClaudeEnvironmentCodec
from chameleon.codecs.claude.governance import ClaudeGovernanceCodec
from chameleon.codecs.claude.identity import ClaudeIdentityCodec
from chameleon.codecs.claude.interface import ClaudeInterfaceCodec
from chameleon.codecs.claude.lifecycle import ClaudeLifecycleCodec
from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.targets.claude.assembler import ClaudeAssembler


class ClaudeTarget:
    target_id: ClassVar[TargetId] = BUILTIN_CLAUDE
    assembler: ClassVar[type[ClaudeAssembler]] = ClaudeAssembler
    codecs: ClassVar[tuple[type[Codec], ...]] = (
        ClaudeIdentityCodec,
        ClaudeDirectivesCodec,
        ClaudeCapabilitiesCodec,
        ClaudeEnvironmentCodec,
        ClaudeAuthorizationCodec,
        ClaudeLifecycleCodec,
        ClaudeInterfaceCodec,
        ClaudeGovernanceCodec,
    )


__all__ = ["ClaudeTarget"]
