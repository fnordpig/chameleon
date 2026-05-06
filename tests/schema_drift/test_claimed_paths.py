"""Schema-drift tests: every registered codec's claimed_paths must resolve
into the corresponding _generated.py FullTargetModel.

Run via `uv run pytest -m schema_drift -v`.

V0 limitation: claude's capabilities codec claims `mcpServers` which lives
in `~/.claude.json`, not the primary `settings.json` modelled by
ClaudeCodeSettings. A full implementation would aggregate the multi-file
view into the assembler's `full_model`; for now we exempt that codec.
The exemption list shrinks as the design's full multi-file aggregate
materializes.
"""

from __future__ import annotations

import pytest

from chameleon._types import TargetId
from chameleon.codecs._protocol import validate_claimed_paths
from chameleon.codecs._registry import CodecRegistry
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.targets._registry import TargetRegistry

pytestmark = pytest.mark.schema_drift


_EXEMPT_CODECS: frozenset[tuple[TargetId, Domains]] = frozenset(
    {
        # capabilities.mcp_servers maps to ~/.claude.json's `mcpServers`,
        # not settings.json (the file ClaudeCodeSettings models).
        (BUILTIN_CLAUDE, Domains.CAPABILITIES),
    }
)


def test_every_registered_codec_resolves_against_full_model() -> None:
    target_reg = TargetRegistry.discover()
    codec_reg = CodecRegistry()
    for tid in target_reg.target_ids():
        target_cls = target_reg.get(tid)
        assert target_cls is not None
        for codec in target_cls.codecs:
            codec_reg.register(codec)

    for tid in target_reg.target_ids():
        target_cls = target_reg.get(tid)
        assert target_cls is not None
        full = target_cls.assembler.full_model
        for codec in codec_reg.for_target(tid):
            if (codec.target, codec.domain) in _EXEMPT_CODECS:
                continue
            validate_claimed_paths(codec, full)
