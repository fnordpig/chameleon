"""Codex codec for the lifecycle domain.

V0 thin slice:
  history.persistence  ↔ [history].persistence
  history.max_bytes    ↔ [history].max_bytes

telemetry.exporter ↔ [otel].exporter:
  Codex's ``OtelConfigToml.exporter`` is an ``OtelExporterKind`` RootModel
  union with three arms — a plain enum (``none``/``statsig``), an
  ``otlp-http`` table, or an ``otlp-grpc`` table. Neutral
  ``TelemetryExporter`` exposes ``none``/``otlp-http``/``otlp-grpc``.
  We map:
    NONE        ↔ OtelExporterKind1.none
    OTLP_HTTP   ↔ {otlp-http: {endpoint, protocol: 'json'}}
    OTLP_GRPC   ↔ {otlp-grpc: {endpoint}}
  ``protocol`` and the OTLP endpoint are required by upstream's schema;
  if the operator selected OTLP_* without an endpoint, we emit a
  ``LossWarning`` and leave the field unset rather than fabricate a
  bogus default. Codex-only ``statsig`` reverse-maps to a LossWarning
  (no neutral analogue).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.codecs.codex._generated import (
    OtelExporterKind,
    OtelExporterKind1,
    OtelExporterKind2,
    OtelExporterKind3,
    OtelHttpProtocol,
    OtelHttpProtocol2,
    OtlpGrpc,
    OtlpHttp,
)
from chameleon.schema._constants import BUILTIN_CODEX, Domains
from chameleon.schema.lifecycle import (
    History,
    HistoryPersistence,
    Hooks,
    Lifecycle,
    Telemetry,
    TelemetryExporter,
)


def _hooks_has_any_event(hooks: Hooks) -> bool:
    """True iff the operator set any hook event in neutral.

    A bare ``Hooks()`` instance is the V0 default and means "no hooks
    configured" — emitting a LossWarning for it would be noise. Any
    explicitly-set event (or any extras carried via ``extra="allow"``)
    is real operator data we cannot propagate to Codex today.
    """
    for field_name in Hooks.model_fields:
        if getattr(hooks, field_name) is not None:
            return True
    extras = getattr(hooks, "model_extra", None) or {}
    return bool(extras)


class _CodexHistory(BaseModel):
    # ``extra="allow"`` — unclaimed sub-keys round-trip through
    # ``__pydantic_extra__`` and are re-emitted by the assembler.
    model_config = ConfigDict(extra="allow")
    persistence: str | None = None
    max_bytes: int | None = None


class _CodexOtel(BaseModel):
    """slice of ``[otel]`` claimed by this codec.

    Only the ``exporter`` arm is claimed today; the rest of
    ``OtelConfigToml`` (``environment`` / ``log_user_prompt`` /
    ``metrics_exporter`` / ``trace_exporter``) is unmodelled here, and
    ``extra="allow"`` keeps any such keys round-tripping through
    ``__pydantic_extra__``.
    """

    model_config = ConfigDict(extra="allow")
    exporter: OtelExporterKind | None = None


class CodexLifecycleSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    history: _CodexHistory = Field(default_factory=_CodexHistory)
    # telemetry.exporter (and endpoint) live under [otel].
    otel: _CodexOtel | None = None


class CodexLifecycleCodec:
    target: ClassVar[TargetId] = BUILTIN_CODEX
    domain: ClassVar[Domains] = Domains.LIFECYCLE
    target_section: ClassVar[type[BaseModel]] = CodexLifecycleSection
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("history", "persistence")),
            FieldPath(segments=("history", "max_bytes")),
            # telemetry.exporter:
            FieldPath(segments=("otel", "exporter")),
        }
    )

    @staticmethod
    def to_target(model: Lifecycle, ctx: TranspileCtx) -> CodexLifecycleSection:
        section = CodexLifecycleSection()
        if model.history.persistence is not None:
            section.history.persistence = model.history.persistence.value
        if model.history.max_bytes is not None:
            section.history.max_bytes = model.history.max_bytes
        if model.cleanup_period_days is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CODEX,
                    message="lifecycle.cleanup_period_days has no Codex equivalent",
                )
            )
        if _hooks_has_any_event(model.hooks):
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CODEX,
                    message=(
                        "lifecycle.hooks not propagated to Codex: Codex "
                        "does not currently expose a hooks ABI; a real Codex "
                        "hooks codec lands once upstream publishes a schema"
                    ),
                )
            )
        # telemetry.exporter ↔ [otel].exporter.
        exporter = _telemetry_exporter_to_codex(model.telemetry, ctx)
        if exporter is not None:
            section.otel = _CodexOtel(exporter=exporter)
        return section

    @staticmethod
    def from_target(section: CodexLifecycleSection, ctx: TranspileCtx) -> Lifecycle:
        history = History()
        if section.history.persistence is not None:
            try:
                history.persistence = HistoryPersistence(section.history.persistence)
            except ValueError:
                ctx.warn(
                    LossWarning(
                        domain=Domains.LIFECYCLE,
                        target=BUILTIN_CODEX,
                        message=(
                            f"unknown history.persistence {section.history.persistence!r}; dropping"
                        ),
                    )
                )
        if section.history.max_bytes is not None:
            history.max_bytes = section.history.max_bytes
        # reverse mapping for [otel].exporter.
        telemetry = (
            _telemetry_exporter_from_codex(section.otel.exporter, ctx)
            if section.otel is not None and section.otel.exporter is not None
            else Telemetry()
        )
        return Lifecycle(history=history, telemetry=telemetry)


def _telemetry_exporter_to_codex(  # noqa: PLR0911 — branches map 1:1 to the four telemetry exporter cases plus the endpoint-without-exporter sentinel
    telemetry: Telemetry, ctx: TranspileCtx
) -> OtelExporterKind | None:
    """Render the neutral ``Telemetry`` triple into Codex's ``OtelExporterKind``.

    Returns ``None`` when the operator hasn't set ``exporter`` (so the
    assembler can omit the ``[otel]`` table entirely). Returns ``None``
    after a ``LossWarning`` when an OTLP variant is selected without a
    paired ``endpoint`` — fabricating a default endpoint would be a
    silent mis-correction rather than honest loss.
    """
    if telemetry.exporter is None:
        if telemetry.endpoint is not None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CODEX,
                    message=(
                        "lifecycle.telemetry.endpoint set without an exporter; "
                        "Codex's [otel] table requires a configured exporter, "
                        "leaving unset"
                    ),
                    field_path=FieldPath(segments=("telemetry", "endpoint")),
                )
            )
        return None
    if telemetry.exporter is TelemetryExporter.NONE:
        return OtelExporterKind(root=OtelExporterKind1.none)
    if telemetry.exporter is TelemetryExporter.OTLP_HTTP:
        if telemetry.endpoint is None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CODEX,
                    message=(
                        "lifecycle.telemetry.exporter='otlp-http' requires an "
                        "endpoint to render Codex's [otel.exporter.otlp-http] "
                        "table; leaving exporter unset"
                    ),
                    field_path=FieldPath(segments=("telemetry", "endpoint")),
                )
            )
            return None
        return OtelExporterKind(
            root=OtelExporterKind2(
                **{
                    "otlp-http": OtlpHttp(
                        endpoint=telemetry.endpoint,
                        protocol=OtelHttpProtocol(root=OtelHttpProtocol2.json),
                    )
                }
            )
        )
    if telemetry.exporter is TelemetryExporter.OTLP_GRPC:
        if telemetry.endpoint is None:
            ctx.warn(
                LossWarning(
                    domain=Domains.LIFECYCLE,
                    target=BUILTIN_CODEX,
                    message=(
                        "lifecycle.telemetry.exporter='otlp-grpc' requires an "
                        "endpoint to render Codex's [otel.exporter.otlp-grpc] "
                        "table; leaving exporter unset"
                    ),
                    field_path=FieldPath(segments=("telemetry", "endpoint")),
                )
            )
            return None
        return OtelExporterKind(
            root=OtelExporterKind3(**{"otlp-grpc": OtlpGrpc(endpoint=telemetry.endpoint)})
        )
    # pragma: no cover — exhaustion sentinel.
    return None


def _telemetry_exporter_from_codex(exporter: OtelExporterKind, ctx: TranspileCtx) -> Telemetry:
    """Reverse mapping for ``[otel].exporter``.

    The codex-only ``statsig`` value maps to a typed LossWarning — there
    is no neutral analogue. Unknown union arms (a future upstream
    addition) likewise emit a LossWarning rather than crash.
    """
    root = exporter.root
    if isinstance(root, OtelExporterKind1):
        # Plain enum arm — ``none`` round-trips, ``statsig`` is Codex-only.
        if root is OtelExporterKind1.none:
            return Telemetry(exporter=TelemetryExporter.NONE)
        ctx.warn(
            LossWarning(
                domain=Domains.LIFECYCLE,
                target=BUILTIN_CODEX,
                message=(
                    f"otel.exporter={root.value!r} is Codex-only and has no "
                    "neutral TelemetryExporter equivalent; dropping"
                ),
                field_path=FieldPath(segments=("otel", "exporter")),
            )
        )
        return Telemetry()
    if isinstance(root, OtelExporterKind2):
        otlp = root.otlp_http
        return Telemetry(exporter=TelemetryExporter.OTLP_HTTP, endpoint=otlp.endpoint)
    if isinstance(root, OtelExporterKind3):
        return Telemetry(exporter=TelemetryExporter.OTLP_GRPC, endpoint=root.otlp_grpc.endpoint)
    # pragma: no cover — exhaustion sentinel for future union extensions.
    ctx.warn(
        LossWarning(
            domain=Domains.LIFECYCLE,
            target=BUILTIN_CODEX,
            message=(
                "unknown otel.exporter union arm; Codex schema may have grown a "
                "new variant — extend the lifecycle codec"
            ),
            field_path=FieldPath(segments=("otel", "exporter")),
        )
    )
    return Telemetry()


__all__ = ["CodexLifecycleCodec", "CodexLifecycleSection"]
