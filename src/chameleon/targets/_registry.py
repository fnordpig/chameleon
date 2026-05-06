"""Target registry — entry-point discovery + lookup."""

from __future__ import annotations

import importlib.metadata as md

from chameleon._types import TargetId, register_target_id
from chameleon.targets._protocol import Target


class TargetRegistry:
    """Discovers `chameleon.targets` entry points and binds plugin TargetIds."""

    def __init__(self, targets: dict[TargetId, type[Target]]) -> None:
        self._by_id = targets

    @classmethod
    def discover(cls) -> TargetRegistry:
        targets: dict[TargetId, type[Target]] = {}
        for ep in md.entry_points(group="chameleon.targets"):
            register_target_id(ep.name)
            cls_obj = ep.load()
            targets[TargetId(value=ep.name)] = cls_obj
        return cls(targets)

    def target_ids(self) -> list[TargetId]:
        return list(self._by_id.keys())

    def get(self, target_id: TargetId) -> type[Target] | None:
        return self._by_id.get(target_id)


__all__ = ["TargetRegistry"]
