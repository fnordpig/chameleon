"""lifecycle domain — events around agent actions (hooks, telemetry, history).

V0: typed schema only; codecs deferred (§15.2).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class HistoryPersistence(Enum):
    SAVE_ALL = "save-all"
    NONE = "none"


class History(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persistence: HistoryPersistence | None = None
    max_bytes: int | None = Field(default=None, ge=0)


class TelemetryExporter(Enum):
    NONE = "none"
    OTLP_HTTP = "otlp-http"
    OTLP_GRPC = "otlp-grpc"


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exporter: TelemetryExporter | None = None
    endpoint: str | None = None


class Lifecycle(BaseModel):
    """V0: typed schema only; codecs deferred."""

    model_config = ConfigDict(extra="forbid")

    hooks: dict[str, list[str]] = Field(default_factory=dict)
    history: History = Field(default_factory=History)
    telemetry: Telemetry = Field(default_factory=Telemetry)
    cleanup_period_days: int | None = Field(default=None, ge=0)


__all__ = ["History", "HistoryPersistence", "Lifecycle", "Telemetry", "TelemetryExporter"]
