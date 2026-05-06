from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex.directives import CodexDirectivesCodec
from chameleon.schema.directives import Directives


@given(
    spf=st.one_of(st.none(), st.text(min_size=1, max_size=200)),
    attr=st.one_of(st.none(), st.text(min_size=1, max_size=200)),
)
def test_round_trip(spf: str | None, attr: str | None) -> None:
    orig = Directives(system_prompt_file=spf, commit_attribution=attr)
    ctx = TranspileCtx()
    section = CodexDirectivesCodec.to_target(orig, ctx)
    restored = CodexDirectivesCodec.from_target(section, ctx)
    assert restored.system_prompt_file == orig.system_prompt_file
    assert restored.commit_attribution == orig.commit_attribution
