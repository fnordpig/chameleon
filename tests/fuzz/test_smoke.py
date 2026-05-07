"""Single smoke fuzz test proving the Wave-F1 scaffolding works.

This is the ONLY fuzz test in this PR. Wave-F2 owns the remaining
fuzz coverage — schema-extras rejection, cross-target differential,
unicode-torture round-trip, and the corpus-replay fixture. This test's
job is to verify that the strategy registrations in
:mod:`tests.fuzz.strategies` produce valid :class:`Identity` instances
that round-trip through :class:`ClaudeIdentityCodec` under both the
``default`` and ``fuzz`` Hypothesis profiles.
"""

from __future__ import annotations

import pytest
from hypothesis import given

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.identity import ClaudeIdentityCodec
from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.schema.identity import Identity

# Import the strategies module so its `register_type_strategy` calls
# run before `@given(identity=...)` collects. The strategy library
# also runs at conftest-import time, but this explicit re-import lets
# the test file stand on its own when read.
from tests.fuzz import strategies as _strategies  # noqa: F401

pytestmark = pytest.mark.fuzz


@given(identity=...)  # uses the registered Identity strategy
def test_claude_identity_round_trip_smoke(identity: Identity) -> None:
    """End-to-end smoke: register-and-derive-identity through the
    Claude identity codec round-trip.

    The Claude codec is intentionally lossy along the Codex-only
    identity tuning knobs (``context_window``, ``compact_threshold``,
    ``model_catalog_path``) and along the per-target ``model`` mapping
    (only the Claude entry is preserved). The assertions therefore
    pin the *non-lossy* axes and accept the documented losses on the
    rest. When Wave-F2 adds the cross-target differential test it will
    reuse this strategy to drive both targets in parallel.
    """
    ctx = TranspileCtx()
    section = ClaudeIdentityCodec.to_target(identity, ctx)
    recovered = ClaudeIdentityCodec.from_target(section, ctx)

    # Non-lossy axes — these MUST round-trip.
    assert recovered.reasoning_effort == identity.reasoning_effort
    assert recovered.thinking == identity.thinking

    # Per-target model: only the Claude entry survives. If the input
    # had no Claude entry, the recovered model is None.
    if identity.model is not None and BUILTIN_CLAUDE in identity.model:
        assert recovered.model is not None
        assert recovered.model[BUILTIN_CLAUDE] == identity.model[BUILTIN_CLAUDE]
    else:
        assert recovered.model is None
