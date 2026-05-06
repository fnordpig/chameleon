"""Codex target codecs.

The `_generated` module is produced by `tools/sync-schemas/sync.py codex`
and committed to git. Domain codecs in this package import typed slices
of `_generated.ConfigToml` (re-exported here as `CodexConfig` for a
stable name codecs depend on) and never manipulate raw dicts.
"""

from __future__ import annotations

from chameleon.codecs.codex._generated import ConfigToml as CodexConfig

__all__ = ["CodexConfig"]
