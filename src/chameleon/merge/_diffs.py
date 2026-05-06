"""Typed FileDiff model emitted by `MergeEngine` on dry-run.

A ``FileDiff`` captures the bytes a non-dry-run merge *would* have written
to a single live target file, paired with the bytes already on disk. The
CLI uses these to render a unified diff against live without ever
touching the filesystem.

Kept in its own module so the merge engine and the CLI can both import
it without dragging in either's heavier dependencies.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from chameleon._types import TargetId


class FileDiff(BaseModel):
    """Pre/post bytes for one live target file under a hypothetical merge.

    ``live_path`` is the absolute (already-expanded) path to the live
    target file — the same path the non-dry-run write step would touch.
    ``repo_path`` is the assembler-relative path used for diff headers so
    the rendered diff matches what ``chameleon diff`` shows for the same
    target/file pair.

    ``before`` is the bytes currently on disk (empty when the file
    doesn't exist yet — a fresh ``init`` after deleting live). ``after``
    is the bytes the engine composed; identity (``before == after``)
    means "no change for this file" and the CLI suppresses the entry.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    target: TargetId
    live_path: Path
    repo_path: str
    before: bytes
    after: bytes

    @property
    def changed(self) -> bool:
        return self.before != self.after


__all__ = ["FileDiff"]
