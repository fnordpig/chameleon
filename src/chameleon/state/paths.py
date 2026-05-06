"""XDG-aware path resolution for state-repos, neutral file, transaction markers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from chameleon._types import TargetId


@dataclass(frozen=True)
class StatePaths:
    """Resolved paths for chameleon's on-disk state.

    All paths are absolute. Construction does NOT create directories;
    callers materialize them lazily as needed.
    """

    state_root: Path
    config_root: Path
    neutral: Path
    notices_dir: Path
    tx_dir: Path
    lkg: Path  # last-known-good neutral snapshot

    @classmethod
    def resolve(cls, *, neutral_override: Path | None = None) -> StatePaths:
        state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        state_root = state_home / "chameleon"
        config_root = config_home / "chameleon"
        return cls(
            state_root=state_root,
            config_root=config_root,
            neutral=neutral_override or (config_root / "neutral.yaml"),
            notices_dir=state_root / "notices",
            tx_dir=state_root / ".tx",
            lkg=state_root / "neutral.lkg.yaml",
        )

    def target_repo(self, target_id: TargetId) -> Path:
        return self.state_root / "targets" / target_id.value


__all__ = ["StatePaths"]
