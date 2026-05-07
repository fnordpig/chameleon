from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.identity import (
    ClaudeIdentityCodec,
    ClaudeIdentitySection,
)
from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.schema.identity import Identity, ReasoningEffort

# Generate Identity instances with the Claude-specific keys exercised.
# Codex-specific keys (e.g. model[BUILTIN_CODEX]) are not in scope here:
# the Claude codec ignores them on forward and re-emits them only via
# pass-through on reverse.

_reasoning = st.sampled_from(list(ReasoningEffort))
_thinking = st.booleans()
_models = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-.", min_size=3, max_size=40)


@st.composite
def _claude_focused_identities(draw: st.DrawFn) -> Identity:
    return Identity(
        reasoning_effort=draw(st.one_of(st.none(), _reasoning)),
        thinking=draw(st.one_of(st.none(), _thinking)),
        model={BUILTIN_CLAUDE: draw(_models)} if draw(st.booleans()) else None,
    )


@given(_claude_focused_identities())
def test_round_trip_for_claude_identity(orig: Identity) -> None:
    ctx = TranspileCtx()
    section = ClaudeIdentityCodec.to_target(orig, ctx)
    restored = ClaudeIdentityCodec.from_target(section, ctx)
    assert restored.reasoning_effort == orig.reasoning_effort
    assert restored.thinking == orig.thinking
    if orig.model is not None and BUILTIN_CLAUDE in orig.model:
        assert restored.model is not None
        assert restored.model[BUILTIN_CLAUDE] == orig.model[BUILTIN_CLAUDE]


def test_claude_identity_section_is_typed_subset() -> None:
    for path in ClaudeIdentityCodec.claimed_paths:
        cur: type = ClaudeIdentitySection
        for seg in path.segments:
            # Section fields may use the wire key as a Pydantic alias when
            # the Python attribute name is snake_case (Wave-10 §15.x's
            # ``forceLoginMethod`` / ``apiKeyHelper`` paths). Resolve by
            # alias too — same rule the schema-drift gate enforces.
            field = cur.model_fields.get(seg)
            if field is None:
                for f in cur.model_fields.values():
                    if getattr(f, "alias", None) == seg:
                        field = f
                        break
            assert field is not None, f"{path.render()} not in section"
            ann = field.annotation
            if isinstance(ann, type):
                cur = ann
