from __future__ import annotations

import pytest
from pydantic import BaseModel

from chameleon.codecs._protocol import Codec, TranspileCtx
from chameleon.codecs.claude.authorization import ClaudeAuthorizationCodec
from chameleon.codecs.claude.governance import ClaudeGovernanceCodec
from chameleon.codecs.claude.interface import ClaudeInterfaceCodec
from chameleon.codecs.claude.lifecycle import ClaudeLifecycleCodec
from chameleon.schema.authorization import Authorization
from chameleon.schema.governance import Governance
from chameleon.schema.interface import Interface
from chameleon.schema.lifecycle import Lifecycle


@pytest.mark.parametrize(
    ("codec", "fragment"),
    [
        (ClaudeAuthorizationCodec, Authorization()),
        (ClaudeLifecycleCodec, Lifecycle()),
        (ClaudeInterfaceCodec, Interface()),
        (ClaudeGovernanceCodec, Governance()),
    ],
    ids=["authorization", "lifecycle", "interface", "governance"],
)
def test_stub_codec_raises_not_implemented(codec: Codec, fragment: BaseModel) -> None:
    ctx = TranspileCtx()
    with pytest.raises(NotImplementedError):
        codec.to_target(fragment, ctx)
    with pytest.raises(NotImplementedError):
        codec.from_target(codec.target_section(), ctx)
