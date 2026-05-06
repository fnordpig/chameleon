"""Merge engine — the round-trip orchestrator (§4.3 pipeline).

V0 implements a simplified version of the full §4.3 pipeline:
  - Sampling, disassemble, drift detect, classify, resolve, compose,
    re-derive, write live, commit state-repos, update neutral.
  - Interactive resolution is deferred; V0 accepts only Strategy
    (non-interactive).
  - Change classification operates at domain granularity rather than
    per-FieldPath; per-leaf classification lands when the authorization
    codec ships.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge.changeset import (
    ChangeOutcome,
    ChangeRecord,
    classify_change,
)
from chameleon.merge.conflict import Conflict
from chameleon.merge.resolve import NonInteractiveResolver, Strategy
from chameleon.schema._constants import Domains
from chameleon.schema.neutral import Neutral
from chameleon.state.git import GitRepo
from chameleon.state.paths import StatePaths
from chameleon.state.transaction import (
    TransactionStore,
    transaction_id,
)
from chameleon.targets._protocol import Target
from chameleon.targets._registry import TargetRegistry


class MergeRequest(BaseModel):
    """Inputs to a merge run beyond what the engine already knows."""

    model_config = ConfigDict(frozen=True)

    profile_name: str | None = None
    dry_run: bool = False


class MergeResult(BaseModel):
    """Outcome of a merge run."""

    model_config = ConfigDict(frozen=True)

    exit_code: int
    summary: str
    merge_id: str | None = None


class MergeEngine:
    def __init__(
        self,
        targets: TargetRegistry,
        paths: StatePaths,
        strategy: Strategy,
    ) -> None:
        self.targets = targets
        self.paths = paths
        self.strategy = strategy
        self.tx_store = TransactionStore(paths.tx_dir)

    def _read_live_files(self, target_cls: type[Target]) -> dict[str, bytes]:
        """Read a target's live config files into a dict keyed by repo_path."""
        out: dict[str, bytes] = {}
        for spec in target_cls.assembler.files:
            live = Path(os.path.expanduser(spec.live_path))
            out[spec.repo_path] = live.read_bytes() if live.exists() else b""
        return out

    def _ensure_state_repo(self, target_id: TargetId) -> GitRepo:
        repo_path = self.paths.target_repo(target_id)
        if (repo_path / ".git").exists():
            return GitRepo(repo_path)
        return GitRepo.init(repo_path)

    def merge(self, request: MergeRequest) -> MergeResult:  # noqa: PLR0912, PLR0915
        # 1. Load N1 and N0
        if self.paths.neutral.exists():
            n1 = Neutral.model_validate(load_yaml(self.paths.neutral))
        else:
            n1 = Neutral(schema_version=1)

        if self.paths.lkg.exists():
            n0 = Neutral.model_validate(load_yaml(self.paths.lkg))
        else:
            n0 = Neutral(schema_version=1)

        # 2. Sample + disassemble + reverse-codec per target
        ctx = TranspileCtx(profile_name=request.profile_name)
        per_target_neutral: dict[TargetId, Neutral] = {}

        for tid in self.targets.target_ids():
            target_cls = self.targets.get(tid)
            if target_cls is None:
                continue
            live = self._read_live_files(target_cls)
            domains, _passthrough = target_cls.assembler.disassemble(live)

            target_neutral = Neutral(schema_version=1)
            for codec_cls in target_cls.codecs:
                if codec_cls.domain not in domains:
                    continue
                try:
                    fragment = codec_cls.from_target(domains[codec_cls.domain], ctx)
                except NotImplementedError:
                    continue
                setattr(target_neutral, codec_cls.domain.value, fragment)

            per_target_neutral[tid] = target_neutral

        # 3-5. Classify each domain and gather conflicts
        merge_id = transaction_id()
        conflicts: list[Conflict] = []
        composed = n1.model_copy(deep=True)

        for domain in Domains:
            n0_val = getattr(n0, domain.value)
            n1_val = getattr(n1, domain.value)
            per_target_vals: dict[TargetId, Any] = {
                tid: getattr(neutral, domain.value) for tid, neutral in per_target_neutral.items()
            }

            record = ChangeRecord(
                domain=domain,
                path=FieldPath(segments=(domain.value,)),
                n0=n0_val.model_dump(mode="json") if hasattr(n0_val, "model_dump") else n0_val,
                n1=n1_val.model_dump(mode="json") if hasattr(n1_val, "model_dump") else n1_val,
                per_target={
                    tid: v.model_dump(mode="json") if hasattr(v, "model_dump") else v
                    for tid, v in per_target_vals.items()
                },
            )
            cl = classify_change(record)
            if cl.outcome is ChangeOutcome.UNCHANGED:
                continue
            if cl.outcome is ChangeOutcome.CONFLICT:
                conflicts.append(Conflict(record=record))
                continue
            if cl.winning_target is not None:
                src_neutral = per_target_neutral[cl.winning_target]
                setattr(composed, domain.value, getattr(src_neutral, domain.value))

        # 6. Resolve conflicts non-interactively
        resolver = NonInteractiveResolver(self.strategy)
        for c in conflicts:
            resolved = resolver.resolve(c)
            if resolved is None:
                continue
            domain_cls = type(getattr(composed, c.record.domain.value))
            setattr(composed, c.record.domain.value, domain_cls.model_validate(resolved))

        # 7. Re-derive each target from `composed`
        ctx2 = TranspileCtx(profile_name=request.profile_name)
        target_outputs: dict[TargetId, dict[str, bytes]] = {}
        for tid in self.targets.target_ids():
            target_cls = self.targets.get(tid)
            if target_cls is None:
                continue
            per_domain_sections: dict[Domains, BaseModel] = {}
            for codec_cls in target_cls.codecs:
                neutral_field = getattr(composed, codec_cls.domain.value)
                try:
                    section = codec_cls.to_target(neutral_field, ctx2)
                except NotImplementedError:
                    continue
                per_domain_sections[codec_cls.domain] = section

            existing = self._read_live_files(target_cls) if request.dry_run is False else None
            files = target_cls.assembler.assemble(
                per_domain=per_domain_sections,
                passthrough={},
                existing=existing,
            )
            target_outputs[tid] = dict(files)

        # 8. Write live + commit state-repos (skipped on dry_run)
        if request.dry_run:
            return MergeResult(exit_code=0, summary="dry run — no files written", merge_id=merge_id)

        any_changed = False
        for tid, files in target_outputs.items():
            target_cls = self.targets.get(tid)
            if target_cls is None:
                continue
            repo = self._ensure_state_repo(tid)

            for spec in target_cls.assembler.files:
                live_path = Path(os.path.expanduser(spec.live_path))
                live_path.parent.mkdir(parents=True, exist_ok=True)
                content = files.get(spec.repo_path, b"")
                repo_file = repo.path / spec.repo_path
                repo_file.parent.mkdir(parents=True, exist_ok=True)
                if not live_path.exists() or live_path.read_bytes() != content:
                    any_changed = True
                    live_path.write_bytes(content)
                repo_file.write_bytes(content)

            repo.add_all()
            if not repo.is_clean() or repo.head_commit() is None:
                repo.commit(
                    f"merge: {len(conflicts)} conflict(s), {len(target_outputs)} target(s)",
                    trailer={"Merge-Id": merge_id},
                )
                any_changed = True

        # 9. Update LKG and neutral file
        composed_dict = composed.model_dump(mode="json", exclude_none=False)
        composed_yaml = dump_yaml(composed_dict)
        if not self.paths.lkg.exists() or self.paths.lkg.read_text() != composed_yaml:
            any_changed = True
            self.paths.lkg.parent.mkdir(parents=True, exist_ok=True)
            self.paths.lkg.write_text(composed_yaml, encoding="utf-8")
        if not self.paths.neutral.exists() or self.paths.neutral.read_text() != composed_yaml:
            any_changed = True
            self.paths.neutral.parent.mkdir(parents=True, exist_ok=True)
            self.paths.neutral.write_text(composed_yaml, encoding="utf-8")

        if not any_changed:
            return MergeResult(exit_code=0, summary="merge: nothing to do", merge_id=merge_id)

        return MergeResult(
            exit_code=0,
            summary=f"merge: applied across {len(target_outputs)} target(s)",
            merge_id=merge_id,
        )


__all__ = ["MergeEngine", "MergeRequest", "MergeResult"]
