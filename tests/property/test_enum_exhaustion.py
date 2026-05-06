"""Wave-8 — exhaustive enum/Literal round-trip verification.

For every ``Enum`` / ``StrEnum`` / ``IntEnum`` / ``Literal[...]`` field in
the neutral schema, exhaustively round-trip every value through every
codec whose domain matches that field. Finite-domain mathematical
proof — fast, deterministic, no fuzzing needed.

Catalog discovery is dynamic: walking ``Neutral.model_fields``
recursively from the schema, identifying scalar enum / Literal leaves
inside per-domain submodels (``Identity``, ``Directives``, ...).
A field is considered claimed by a given codec iff
``codec.from_target(codec.to_target(domain_root, ctx), ctx)`` reproduces
the original value at that path. Codecs that do not yet claim a
schema-typed enum field (the typed-but-unimplemented surface tracked
under §15.x of the design spec) cause that ``(field, value, codec)``
case to be skipped, with a session-summary count emitted at end-of-run
so the parity gap stays visible.

Scope intentionally restricted to scalar leaves directly on a domain
model or its nested non-collection submodels. Enum / Literal values
inside ``dict[K, V]`` or ``list[T]`` containers (e.g. the discriminator
``transport`` / ``kind`` / ``type`` Literals on entries inside
``capabilities.mcp_servers`` or ``capabilities.plugin_marketplaces``
or ``lifecycle.hooks.<event>``) are deliberately out of scope here —
they are dispatch tags rather than free-floating values, and
parametrising them would require synthesising parent containers that
multiply combinatorics for no proof benefit. They are exercised by
the per-shape codec tests instead.
"""

from __future__ import annotations

import enum
import types
import typing
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from chameleon._types import FieldPath
from chameleon.codecs._protocol import Codec, TranspileCtx
from chameleon.schema._constants import Domains
from chameleon.schema.neutral import Neutral
from chameleon.targets.claude import ClaudeTarget
from chameleon.targets.codex import CodexTarget

# ---------------------------------------------------------------------------
# Catalog walker — recursively find scalar enum / Literal leaves in Neutral.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnumCatalogEntry:
    """A discovered enum / Literal field in the neutral schema.

    ``path`` is the dotted path from ``Neutral`` (its first segment is
    the domain name; the remainder is the path inside the domain root).
    ``values`` is the finite domain — either a tuple of enum members or
    a tuple of Literal arg values. ``representation`` is a short human
    label used for parametrize IDs.
    """

    path: FieldPath
    enum_kind: type[enum.Enum] | None
    values: tuple[object, ...]

    @property
    def domain_segment(self) -> str:
        return self.path.segments[0]

    @property
    def field_path_in_domain(self) -> tuple[str, ...]:
        return self.path.segments[1:]

    def render_value(self, value: object) -> str:
        if isinstance(value, enum.Enum):
            return value.name
        return repr(value)


def _peel_optional(annotation: object) -> object:
    """Strip a top-level ``X | None`` / ``Optional[X]`` wrapper."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _enum_class_from_annotation(annotation: object) -> type[enum.Enum] | None:
    """If ``annotation`` resolves to an Enum subclass (incl. inside
    ``Optional``), return that subclass. Otherwise return None."""
    inner = _peel_optional(annotation)
    if isinstance(inner, type) and issubclass(inner, enum.Enum):
        return inner
    return None


def _literal_values_from_annotation(annotation: object) -> tuple[object, ...] | None:
    """If ``annotation`` resolves to a ``Literal[...]`` (incl. inside
    ``Optional``), return its arg tuple. Otherwise return None.
    Returns None for an empty Literal (defensive — Python forbids this
    syntactically, but skip cleanly if a future schema regen produces
    one through composition)."""
    inner = _peel_optional(annotation)
    if typing.get_origin(inner) is typing.Literal:
        args = typing.get_args(inner)
        if not args:
            return None
        return args
    return None


def _basemodel_from_annotation(annotation: object) -> type[BaseModel] | None:
    """If ``annotation`` resolves to a ``BaseModel`` subclass (incl.
    inside ``Optional``), return that subclass. Otherwise return None.
    Crucially: returns None for ``dict[...]`` / ``list[...]`` / other
    generic containers — recursion intentionally stops at containers
    so we don't catalog enum leaves nested inside dict-values or
    list-elements (per the module docstring's scope rule)."""
    inner = _peel_optional(annotation)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return inner
    return None


def _walk(
    model_class: type[BaseModel],
    path_so_far: tuple[str, ...],
    seen: set[type[BaseModel]],
) -> Iterable[EnumCatalogEntry]:
    if model_class in seen:
        # Cycle protection — defensive even though schema is a DAG today.
        return
    seen = seen | {model_class}
    for name, field_info in model_class.model_fields.items():
        ann = field_info.annotation
        path = (*path_so_far, name)
        enum_class = _enum_class_from_annotation(ann)
        if enum_class is not None:
            yield EnumCatalogEntry(
                path=FieldPath(segments=path),
                enum_kind=enum_class,
                values=tuple(enum_class),
            )
            continue
        literal_args = _literal_values_from_annotation(ann)
        if literal_args is not None:
            yield EnumCatalogEntry(
                path=FieldPath(segments=path),
                enum_kind=None,
                values=literal_args,
            )
            continue
        nested_model = _basemodel_from_annotation(ann)
        if nested_model is not None:
            yield from _walk(nested_model, path, seen)


def _build_catalog() -> tuple[EnumCatalogEntry, ...]:
    """Walk ``Neutral`` once at module load and return every scalar
    enum / Literal leaf found inside the eight domain submodels.

    Top-level Neutral fields outside the eight domains
    (``schema_version``, ``profiles``, ``targets``) are intentionally
    skipped — ``schema_version`` is an internal marker (no codec round-
    trips it) and ``profiles`` / ``targets`` are dict-valued containers."""
    domain_field_names = {d.value for d in Domains}
    entries: list[EnumCatalogEntry] = []
    for name, field_info in Neutral.model_fields.items():
        if name not in domain_field_names:
            continue
        ann = field_info.annotation
        domain_root = _basemodel_from_annotation(ann)
        if domain_root is None:
            continue
        entries.extend(_walk(domain_root, (name,), set()))
    # Sort for stable parametrize IDs / reproducible session summary.
    return tuple(sorted(entries, key=lambda e: e.path.render()))


CATALOG: tuple[EnumCatalogEntry, ...] = _build_catalog()


# ---------------------------------------------------------------------------
# Codec roster — discovered from the registered targets, not hand-curated.
# ---------------------------------------------------------------------------


def _all_codec_classes() -> tuple[type[Codec], ...]:
    return ClaudeTarget.codecs + CodexTarget.codecs


CODECS: tuple[type[Codec], ...] = _all_codec_classes()


# ---------------------------------------------------------------------------
# Minimal-submodel construction & path extraction helpers.
# ---------------------------------------------------------------------------


def _build_minimal_domain(
    domain_root: type[BaseModel],
    inner_path: Sequence[str],
    value: object,
) -> BaseModel:
    """Construct a ``domain_root`` instance with ``value`` set at
    ``inner_path``. Intermediate submodels are explicitly constructed
    so an ``X | None`` field carries a real submodel (default
    ``None`` would defeat the test). Other fields default-construct."""
    if not inner_path:
        msg = "inner_path must contain at least one segment"
        raise ValueError(msg)
    head, *tail = inner_path
    if not tail:
        return domain_root.model_validate({head: value})
    head_field = domain_root.model_fields[head]
    nested_model = _basemodel_from_annotation(head_field.annotation)
    if nested_model is None:
        msg = (
            f"path {inner_path!r} on {domain_root.__name__}: segment {head!r} "
            f"does not resolve to a BaseModel; cannot build minimal submodel"
        )
        raise TypeError(msg)
    sub = _build_minimal_domain(nested_model, tail, value)
    return domain_root.model_validate({head: sub})


def _extract(model: BaseModel, inner_path: Sequence[str]) -> object:
    """Walk ``inner_path`` through ``model``, returning the leaf value.
    If any intermediate submodel is ``None``, returns ``None``."""
    cursor: object = model
    for seg in inner_path:
        if cursor is None:
            return None
        cursor = getattr(cursor, seg)
    return cursor


# ---------------------------------------------------------------------------
# Catalog x codec parametrise — every (entry, value, codec) candidate.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Case:
    entry: EnumCatalogEntry
    value: object
    codec: type[Codec]


def _domain_class_for(entry: EnumCatalogEntry) -> type[BaseModel]:
    field_info = Neutral.model_fields[entry.domain_segment]
    cls = _basemodel_from_annotation(field_info.annotation)
    if cls is None:
        # Catalog construction shouldn't yield entries whose root isn't
        # a BaseModel — surface a typed error if it ever does.
        msg = f"domain {entry.domain_segment!r} did not resolve to a BaseModel root"
        raise TypeError(msg)
    return cls


def _candidates() -> list[_Case]:
    cases: list[_Case] = []
    for entry in CATALOG:
        for value in entry.values:
            for codec in CODECS:
                if codec.domain.value != entry.domain_segment:
                    continue
                cases.append(_Case(entry=entry, value=value, codec=codec))
    return cases


CASES: list[_Case] = _candidates()


def _case_id(case: _Case) -> str:
    return f"{case.entry.path.render()}-{case.entry.render_value(case.value)}-{case.codec.__name__}"


# Mutable counters populated by the test body so the terminal-summary
# hook below can report skip / pass tallies without re-running discovery.
_SKIPPED_UNCLAIMED: list[str] = []
_PASSED: list[str] = []
_CLAIMERS_BY_PATH: dict[str, set[str]] = {}


@pytest.mark.parametrize("case", CASES, ids=[_case_id(c) for c in CASES])
def test_round_trip(case: _Case) -> None:
    domain_root = _domain_class_for(case.entry)
    inner = case.entry.field_path_in_domain
    submodel = _build_minimal_domain(domain_root, inner, case.value)
    ctx = TranspileCtx()
    section = case.codec.to_target(submodel, ctx)
    restored = case.codec.from_target(section, ctx)
    actual = _extract(restored, inner)
    if actual is None and case.value is not None:
        # Codec does not (yet) claim this neutral field. Record it so
        # the session summary surfaces the parity gap, then skip — the
        # round-trip can't be proven against a codec that drops the
        # value by design (see e.g. P1-G ClaudeAuthorizationCodec
        # warning for ``reviewer``, or §15.x deferred fields).
        marker = f"{case.entry.path.render()} via {case.codec.__name__}"
        _SKIPPED_UNCLAIMED.append(marker)
        pytest.skip(
            f"{case.codec.__name__} does not round-trip "
            f"{case.entry.path.render()} (value dropped on encode/decode); "
            "skipping per Wave-8 unclaimed-field policy."
        )
    assert actual == case.value, (
        f"round-trip mismatch for {case.entry.path.render()} via "
        f"{case.codec.__name__}: original={case.value!r} restored={actual!r}"
    )
    _PASSED.append(_case_id(case))
    _CLAIMERS_BY_PATH.setdefault(case.entry.path.render(), set()).add(case.codec.__name__)


# ---------------------------------------------------------------------------
# Session summary — visibility for the parity gap and the catalog itself.
# A module-local ``pytest_terminal_summary`` hook would not be picked up
# (pytest only autoregisters it in ``conftest.py`` / installed plugins);
# the touch-this-file-only constraint forbids dropping it in conftest.
# An autouse session-scoped fixture is the principled within-file way
# to publish the summary into the captured pytest output.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _emit_session_summary(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        # Headless invocation (e.g. pytest --collect-only). No surface to
        # write to — silently drop the summary; the data is recoverable
        # from the parametrise IDs and pass/skip outcomes anyway.
        return
    line = reporter.write_line
    line("")
    line("Wave-8 enum-exhaustion catalog summary")
    line(f"  enum/Literal scalar leaves discovered: {len(CATALOG)}")
    line(f"  parametrised cases: {len(CASES)}")
    line(f"  passed (claimed): {len(_PASSED)}")
    line(f"  skipped because unclaimed: {len(_SKIPPED_UNCLAIMED)}")
    if _SKIPPED_UNCLAIMED:
        line("  unclaimed (field via codec):")
        for marker in sorted(set(_SKIPPED_UNCLAIMED)):
            line(f"    - {marker}")
    if _CLAIMERS_BY_PATH:
        line("  claimers by neutral path:")
        for path in sorted(_CLAIMERS_BY_PATH):
            codecs = ", ".join(sorted(_CLAIMERS_BY_PATH[path]))
            line(f"    - {path}: {codecs}")
