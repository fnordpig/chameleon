"""Target protocol: pairs an Assembler with a set of codecs and a TargetId."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import ClassVar, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ValidationError

from chameleon._types import FileSpec, TargetId
from chameleon.codecs._protocol import Codec, LossWarning, TranspileCtx
from chameleon.schema._constants import Domains


def safe_validate_section(
    section_cls: type[BaseModel],
    section_obj: Mapping[str, object],
    domain: Domains,
    target: TargetId,
    *,
    ctx: TranspileCtx | None,
    per_domain: dict[Domains, BaseModel],
    passthrough: dict[str, object],
) -> None:
    """Validate one per-domain section; on failure, route keys to pass-through.

    P0-2: a single malformed section must not abort the entire disassemble.
    Catch ``ValidationError``, emit a typed ``LossWarning`` (via ``ctx``
    when one was supplied), and route the section's keys verbatim into the
    pass-through bag so the operator can see and hand-fix them.

    The message format is uniform across both assemblers so downstream
    tooling (the CLI, future structured logs) can pattern-match on it::

        "could not disassemble {domain}: {error}; routing to pass-through"

    Both assemblers (Claude, Codex) call into this helper so the catch
    behaviour is provably identical across targets.
    """
    try:
        per_domain[domain] = section_cls.model_validate(section_obj)
    except ValidationError as exc:
        if ctx is not None:
            ctx.warn(
                LossWarning(
                    domain=domain,
                    target=target,
                    message=(
                        f"could not disassemble {domain.value}: {exc}; routing to pass-through"
                    ),
                )
            )
        for k, v in section_obj.items():
            passthrough[k] = v


def harvest_section_extras(section: BaseModel) -> dict[str, object]:
    """Recursively collect ``__pydantic_extra__`` from a section model tree.

    B1 — when a section model uses ``ConfigDict(extra="allow")``, Pydantic
    parks unmodelled keys in ``__pydantic_extra__``. The assembler uses
    those extras to re-emit unclaimed sub-keys on round-trip (e.g.
    ``[tui].status_line`` and ``[tui.model_availability_nux]`` for Codex
    interface) so partially-claimed nested tables don't lose their
    unclaimed inner keys.

    The returned dict has the same nesting shape as the section model:
    ``{"tui": {"status_line": [...], "model_availability_nux": {...}}}``.
    Keys present in the section's *modeled* fields are excluded — only
    unmodelled extras appear. Empty dicts are pruned so callers can use
    a non-empty test to skip merge work.
    """
    out: dict[str, object] = {}
    extras = getattr(section, "__pydantic_extra__", None) or {}
    for k, v in extras.items():
        out[k] = v
    for field_name in type(section).model_fields:
        value = getattr(section, field_name, None)
        sub_extras = _walk_field_extras(value)
        if sub_extras:
            out[field_name] = sub_extras
    return out


def _walk_field_extras(value: object) -> object:
    """Walk into a field value, collecting nested extras recursively.

    Returns an extras-shaped overlay (dict / list / scalar). Returns an
    empty dict when no extras live anywhere under ``value``.
    """
    if isinstance(value, BaseModel):
        return harvest_section_extras(value)
    if isinstance(value, dict):
        # ``dict[str, BaseModel]`` (e.g. ``mcp_servers: dict[str, _CodexMcpServerStdio]``):
        # produce per-key extras only when at least one entry has extras.
        nested: dict[str, object] = {}
        for k, v in value.items():
            sub = _walk_field_extras(v)
            if sub:
                nested[str(k)] = sub
        return nested
    if isinstance(value, list):
        # Lists of BaseModel (e.g. hook matchers) — collect extras
        # positionally so the caller can splice them by index.
        nested_list: list[object] = []
        any_extras = False
        for v in value:
            sub = _walk_field_extras(v)
            nested_list.append(sub)
            if sub:
                any_extras = True
        return nested_list if any_extras else {}
    return {}


def merge_extras_into_dict(
    target: MutableMapping[str, object],
    extras: Mapping[str, object],
) -> None:
    """Merge ``extras`` into ``target`` recursively, in place.

    The ``target`` is the freshly-built per-domain output dict (or a
    sub-table thereof); ``extras`` is the matching slice of the
    extras-overlay produced by ``harvest_section_extras``. Keys already
    present in ``target`` are left alone — codec output is canonical for
    modelled fields; extras only fill in unclaimed sub-keys.
    """
    for k, v in extras.items():
        if isinstance(v, dict) and v:
            existing = target.get(k)
            if isinstance(existing, MutableMapping):
                # Recurse into the nested map. Pydantic-validated
                # nested values land here (tomlkit Tables, plain dicts,
                # etc.); the cast carries us through ``MutableMapping``'s
                # invariant generics — runtime guarantees the keys are
                # ``str`` since both halves come from JSON/TOML decoders.
                merge_extras_into_dict(
                    cast("MutableMapping[str, object]", existing),
                    cast("Mapping[str, object]", v),
                )
            elif k not in target:
                # Unclaimed sub-table the codec didn't emit; copy through.
                target[k] = dict(v)
            # If ``target[k]`` exists but isn't a dict, the codec wrote a
            # scalar where the live had a sub-table — leave the codec's
            # value alone; modelled wins.
        elif k not in target:
            # Scalar (or list-of-scalars) extra — copy through verbatim.
            # List-of-BaseModel cases never reach here; ``_walk_field_extras``
            # only emits a non-empty list when at least one element has
            # extras, and the dict-keyed branch above handles that shape.
            target[k] = v


@runtime_checkable
class Assembler(Protocol):
    target: ClassVar[TargetId]
    full_model: ClassVar[type[BaseModel]]
    files: ClassVar[tuple[FileSpec, ...]]

    @staticmethod
    def assemble(
        per_domain: Mapping[Domains, BaseModel],
        passthrough: Mapping[str, object],
        *,
        existing: Mapping[str, bytes] | None = None,
    ) -> Mapping[str, bytes]: ...

    @staticmethod
    def disassemble(
        files: Mapping[str, bytes],
        *,
        ctx: TranspileCtx | None = None,
    ) -> tuple[Mapping[Domains, BaseModel], dict[str, object]]: ...


@runtime_checkable
class Target(Protocol):
    target_id: ClassVar[TargetId]
    assembler: ClassVar[type[Assembler]]
    codecs: ClassVar[tuple[type[Codec], ...]]


__all__ = [
    "Assembler",
    "Target",
    "harvest_section_extras",
    "merge_extras_into_dict",
    "safe_validate_section",
]
