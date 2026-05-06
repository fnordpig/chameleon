"""Target protocol: pairs an Assembler with a set of codecs and a TargetId."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol, runtime_checkable

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


__all__ = ["Assembler", "Target", "safe_validate_section"]
