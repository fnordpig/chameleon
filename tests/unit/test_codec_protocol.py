from __future__ import annotations

import pytest
from pydantic import BaseModel

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import (
    Codec,
    LossWarning,
    TranspileCtx,
    validate_claimed_paths,
)
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains


class _NeutralFragment(BaseModel):
    foo: str | None = None


class _TargetSection(BaseModel):
    bar: str | None = None


class _GoodCodec:
    target: TargetId = BUILTIN_CLAUDE
    domain: Domains = Domains.IDENTITY
    claimed_paths: frozenset[FieldPath] = frozenset({FieldPath(segments=("bar",))})
    target_section: type[BaseModel] = _TargetSection

    @staticmethod
    def to_target(model: _NeutralFragment, ctx: TranspileCtx) -> _TargetSection:
        return _TargetSection(bar=model.foo)

    @staticmethod
    def from_target(section: _TargetSection, ctx: TranspileCtx) -> _NeutralFragment:
        return _NeutralFragment(foo=section.bar)


def test_codec_runtime_check_passes() -> None:
    # Codec is a Protocol; runtime_checkable check should accept _GoodCodec.
    assert isinstance(_GoodCodec, Codec)


def test_validate_claimed_paths_accepts_existing_field() -> None:
    class FullModel(BaseModel):
        bar: str | None = None
        baz: int = 0

    validate_claimed_paths(_GoodCodec, FullModel)


def test_validate_claimed_paths_rejects_missing_field() -> None:
    class FullModel(BaseModel):
        unrelated: str | None = None

    with pytest.raises(ValueError, match="bar"):
        validate_claimed_paths(_GoodCodec, FullModel)


def test_loss_warning_typed() -> None:
    ctx = TranspileCtx()
    ctx.warn(LossWarning(domain=Domains.IDENTITY, target=BUILTIN_CLAUDE, message="dropped X"))
    assert len(ctx.warnings) == 1
    assert ctx.warnings[0].message == "dropped X"
