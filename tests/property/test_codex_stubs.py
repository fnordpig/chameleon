from __future__ import annotations

import pytest
from pydantic import BaseModel

from chameleon.codecs._protocol import Codec, TranspileCtx
from chameleon.codecs.codex.authorization import CodexAuthorizationCodec
from chameleon.codecs.codex.governance import CodexGovernanceCodec
from chameleon.codecs.codex.interface import CodexInterfaceCodec
from chameleon.codecs.codex.lifecycle import CodexLifecycleCodec
from chameleon.schema.authorization import Authorization
from chameleon.schema.governance import Governance
from chameleon.schema.interface import Interface
from chameleon.schema.lifecycle import Lifecycle


@pytest.mark.parametrize(
    ("codec", "fragment"),
    [
        (CodexAuthorizationCodec, Authorization()),
        (CodexLifecycleCodec, Lifecycle()),
        (CodexInterfaceCodec, Interface()),
        (CodexGovernanceCodec, Governance()),
    ],
    ids=["authorization", "lifecycle", "interface", "governance"],
)
def test_stub_codec_raises_not_implemented(codec: Codec, fragment: BaseModel) -> None:
    ctx = TranspileCtx()
    with pytest.raises(NotImplementedError):
        codec.to_target(fragment, ctx)
    with pytest.raises(NotImplementedError):
        codec.from_target(codec.target_section(), ctx)
