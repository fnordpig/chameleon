"""governance domain — rules about rules (managed config, trust, updates).

V0: typed schema only; codecs deferred (§15.4).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class UpdatesChannel(Enum):
    STABLE = "stable"
    LATEST = "latest"


class Trust(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trusted_paths: list[str] = Field(default_factory=list)
    untrusted_paths: list[str] = Field(default_factory=list)


class Updates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: UpdatesChannel | None = None
    minimum_version: str | None = None


class Governance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    managed: dict[str, str] = Field(default_factory=dict)
    trust: Trust = Field(default_factory=Trust)
    updates: Updates = Field(default_factory=Updates)
    features: dict[str, bool] = Field(default_factory=dict)


__all__ = ["Governance", "Trust", "Updates", "UpdatesChannel"]
