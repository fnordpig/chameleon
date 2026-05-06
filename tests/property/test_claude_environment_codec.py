from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.environment import ClaudeEnvironmentCodec
from chameleon.schema.environment import Environment


@given(
    env_vars=st.dictionaries(st.text(min_size=1, max_size=20), st.text(max_size=200), max_size=10)
)
def test_round_trip(env_vars: dict[str, str]) -> None:
    orig = Environment(variables=env_vars)
    ctx = TranspileCtx()
    section = ClaudeEnvironmentCodec.to_target(orig, ctx)
    restored = ClaudeEnvironmentCodec.from_target(section, ctx)
    assert restored.variables == orig.variables
