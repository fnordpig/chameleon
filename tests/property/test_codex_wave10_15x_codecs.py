"""Wave-10 §15.x — round-trip + LossWarning tests for the five Codex
codec slots that previously dropped enum-typed neutral fields.

The Wave-8 enum-exhaustion harness already proves the round-trip on the
finite enum domain; this file documents the wire-mapping decisions and
exercises the specific LossWarning paths that exhaustion can't reach
(lossy axes are by definition skipped by the round-trip prover).

Wire mappings (with ``_generated.py`` evidence):

* ``identity.auth.method``     ↔ ``forced_login_method`` (``ForcedLoginMethod`` enum)
  Only ``OAUTH ↔ chatgpt`` and ``API_KEY ↔ api`` are bidirectional;
  ``BEDROCK`` / ``VERTEX`` / ``AZURE`` emit a typed LossWarning.
* ``directives.verbosity``     ↔ ``model_verbosity`` (``Verbosity`` enum, exact match)
* ``capabilities.web_search``  ↔ ``web_search``       (``WebSearchMode`` enum, exact match)
* ``environment.inherit``      ↔ ``shell_environment_policy.inherit`` (3-arm RootModel union)
* ``lifecycle.telemetry.exporter`` ↔ ``otel.exporter`` (``OtelExporterKind`` 3-arm union)
"""

from __future__ import annotations

from typing import Literal, cast

import pytest

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex._generated import OtelExporterKind, OtelExporterKind1
from chameleon.codecs.codex.capabilities import CodexCapabilitiesCodec
from chameleon.codecs.codex.directives import CodexDirectivesCodec
from chameleon.codecs.codex.environment import (
    CodexEnvironmentCodec,
    CodexEnvironmentSection,
    _CodexShellEnvPolicy,
)
from chameleon.codecs.codex.identity import CodexIdentityCodec, CodexIdentitySection
from chameleon.codecs.codex.lifecycle import (
    CodexLifecycleCodec,
    CodexLifecycleSection,
    _CodexOtel,
)
from chameleon.schema.capabilities import Capabilities
from chameleon.schema.directives import Directives, Verbosity
from chameleon.schema.environment import Environment, InheritPolicy
from chameleon.schema.identity import AuthMethod, Identity, IdentityAuth
from chameleon.schema.lifecycle import Lifecycle, Telemetry, TelemetryExporter

# ---- identity.auth.method ↔ forced_login_method ----------------------------


@pytest.mark.parametrize(
    ("neutral", "wire"),
    [
        (AuthMethod.OAUTH, "chatgpt"),
        (AuthMethod.API_KEY, "api"),
    ],
)
def test_codex_identity_auth_method_round_trip(neutral: AuthMethod, wire: str) -> None:
    orig = Identity(auth=IdentityAuth(method=neutral))
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(orig, ctx)
    assert section.forced_login_method == wire
    restored = CodexIdentityCodec.from_target(section, ctx)
    assert restored.auth.method is neutral
    assert ctx.warnings == []


@pytest.mark.parametrize("neutral", [AuthMethod.BEDROCK, AuthMethod.VERTEX, AuthMethod.AZURE])
def test_codex_identity_auth_method_loss_warns_on_unsupported(
    neutral: AuthMethod,
) -> None:
    """BEDROCK / VERTEX / AZURE have no Codex equivalent — encoding emits
    a typed LossWarning and leaves the wire field unset."""
    orig = Identity(auth=IdentityAuth(method=neutral))
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(orig, ctx)
    assert section.forced_login_method is None
    assert any(
        "forced_login_method" in w.message and neutral.value in w.message for w in ctx.warnings
    )


def test_codex_identity_unknown_forced_login_method_drops_with_warning() -> None:
    """Disassembled-but-unknown wire values land in the section, then
    surface as a LossWarning at ``from_target`` rather than crashing."""
    section = CodexIdentitySection(forced_login_method="kerberos")
    ctx = TranspileCtx()
    restored = CodexIdentityCodec.from_target(section, ctx)
    assert restored.auth.method is None
    assert any("kerberos" in w.message for w in ctx.warnings)


# ---- directives.verbosity ↔ model_verbosity ----------------------------------


@pytest.mark.parametrize(
    ("neutral", "wire"),
    [(Verbosity.LOW, "low"), (Verbosity.MEDIUM, "medium"), (Verbosity.HIGH, "high")],
)
def test_codex_directives_verbosity_round_trip(neutral: Verbosity, wire: str) -> None:
    orig = Directives(verbosity=neutral)
    ctx = TranspileCtx()
    section = CodexDirectivesCodec.to_target(orig, ctx)
    # Section field is the upstream ``Verbosity`` StrEnum — value matches wire.
    assert section.model_verbosity is not None
    assert section.model_verbosity.value == wire
    restored = CodexDirectivesCodec.from_target(section, ctx)
    assert restored.verbosity is neutral
    assert ctx.warnings == []


# ---- capabilities.web_search ↔ web_search ------------------------------------


@pytest.mark.parametrize("value", ["disabled", "cached", "live"])
def test_codex_capabilities_web_search_round_trip(value: str) -> None:
    typed_value = cast("Literal['cached', 'live', 'disabled']", value)
    orig = Capabilities(web_search=typed_value)
    ctx = TranspileCtx()
    section = CodexCapabilitiesCodec.to_target(orig, ctx)
    assert section.web_search is not None
    assert section.web_search.value == value
    restored = CodexCapabilitiesCodec.from_target(section, ctx)
    assert restored.web_search == value
    assert ctx.warnings == []


# ---- environment.inherit ↔ shell_environment_policy.inherit ------------------


@pytest.mark.parametrize(
    ("neutral", "wire"),
    [
        (InheritPolicy.ALL, "all"),
        (InheritPolicy.CORE, "core"),
        (InheritPolicy.NONE, "none"),
    ],
)
def test_codex_environment_inherit_round_trip(neutral: InheritPolicy, wire: str) -> None:
    orig = Environment(variables={"X": "1"}, inherit=neutral)
    ctx = TranspileCtx()
    section = CodexEnvironmentCodec.to_target(orig, ctx)
    assert section.shell_environment_policy.inherit == wire
    restored = CodexEnvironmentCodec.from_target(section, ctx)
    assert restored.inherit is neutral
    assert restored.variables == {"X": "1"}
    assert ctx.warnings == []


def test_codex_environment_inherit_unknown_drops_with_warning() -> None:
    section = CodexEnvironmentSection(
        shell_environment_policy=_CodexShellEnvPolicy(inherit="cgroup")
    )
    ctx = TranspileCtx()
    restored = CodexEnvironmentCodec.from_target(section, ctx)
    assert restored.inherit is None
    assert any("cgroup" in w.message for w in ctx.warnings)


# ---- lifecycle.telemetry.exporter ↔ otel.exporter ----------------------------


def test_codex_lifecycle_telemetry_none_round_trip() -> None:
    orig = Lifecycle(telemetry=Telemetry(exporter=TelemetryExporter.NONE))
    ctx = TranspileCtx()
    section = CodexLifecycleCodec.to_target(orig, ctx)
    assert section.otel is not None
    assert section.otel.exporter is not None
    restored = CodexLifecycleCodec.from_target(section, ctx)
    assert restored.telemetry.exporter is TelemetryExporter.NONE
    assert restored.telemetry.endpoint is None
    assert ctx.warnings == []


@pytest.mark.parametrize("exporter", [TelemetryExporter.OTLP_HTTP, TelemetryExporter.OTLP_GRPC])
def test_codex_lifecycle_telemetry_otlp_round_trip(
    exporter: TelemetryExporter,
) -> None:
    orig = Lifecycle(telemetry=Telemetry(exporter=exporter, endpoint="https://otel.example.com"))
    ctx = TranspileCtx()
    section = CodexLifecycleCodec.to_target(orig, ctx)
    assert section.otel is not None
    assert section.otel.exporter is not None
    restored = CodexLifecycleCodec.from_target(section, ctx)
    assert restored.telemetry.exporter is exporter
    assert restored.telemetry.endpoint == "https://otel.example.com"
    assert ctx.warnings == []


@pytest.mark.parametrize("exporter", [TelemetryExporter.OTLP_HTTP, TelemetryExporter.OTLP_GRPC])
def test_codex_lifecycle_telemetry_otlp_without_endpoint_loss_warns(
    exporter: TelemetryExporter,
) -> None:
    """Codex's [otel.exporter.otlp-*] table requires an endpoint; we
    refuse to fabricate one and emit a LossWarning instead."""
    orig = Lifecycle(telemetry=Telemetry(exporter=exporter))
    ctx = TranspileCtx()
    section = CodexLifecycleCodec.to_target(orig, ctx)
    assert section.otel is None
    assert any(exporter.value in w.message for w in ctx.warnings)


def test_codex_lifecycle_telemetry_statsig_drops_with_warning() -> None:
    """``statsig`` is Codex-only — neutral has no analogue, so reverse
    mapping emits a LossWarning."""
    section = CodexLifecycleSection(
        otel=_CodexOtel(exporter=OtelExporterKind(root=OtelExporterKind1.statsig))
    )
    ctx = TranspileCtx()
    restored = CodexLifecycleCodec.from_target(section, ctx)
    assert restored.telemetry.exporter is None
    assert any("statsig" in w.message for w in ctx.warnings)
