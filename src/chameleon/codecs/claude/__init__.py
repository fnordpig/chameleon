"""Claude target codecs.

The `_generated` module is produced by `tools/sync-schemas/sync.py claude`
and committed to git. Domain codecs in this package import typed slices
of `_generated.ClaudeCodeSettings` (re-exported here as `ClaudeSettings`
for a stable name codecs depend on) and never manipulate raw dicts.
"""

from __future__ import annotations

from chameleon.codecs.claude._generated import ClaudeCodeSettings as ClaudeSettings

__all__ = ["ClaudeSettings"]
