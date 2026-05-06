from __future__ import annotations

import pytest
from pydantic import BaseModel

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs._registry import CodecRegistry, DuplicateClaimError
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains


class _Section(BaseModel):
    bar: str | None = None


class _Frag(BaseModel):
    foo: str | None = None


class _CodecA:
    target: TargetId = BUILTIN_CLAUDE
    domain: Domains = Domains.IDENTITY
    claimed_paths: frozenset[FieldPath] = frozenset({FieldPath(segments=("bar",))})
    target_section: type[BaseModel] = _Section

    @staticmethod
    def to_target(m: _Frag, c: TranspileCtx) -> _Section:
        return _Section()

    @staticmethod
    def from_target(s: _Section, c: TranspileCtx) -> _Frag:
        return _Frag()


class _CodecB:
    target: TargetId = BUILTIN_CLAUDE
    domain: Domains = Domains.DIRECTIVES
    # SAME path as A — should trigger duplicate claim
    claimed_paths: frozenset[FieldPath] = frozenset({FieldPath(segments=("bar",))})
    target_section: type[BaseModel] = _Section

    @staticmethod
    def to_target(m: _Frag, c: TranspileCtx) -> _Section:
        return _Section()

    @staticmethod
    def from_target(s: _Section, c: TranspileCtx) -> _Frag:
        return _Frag()


def test_registry_accepts_distinct_claims() -> None:
    r = CodecRegistry()
    r.register(_CodecA)

    class _CodecC:
        target: TargetId = BUILTIN_CLAUDE
        domain: Domains = Domains.DIRECTIVES
        claimed_paths: frozenset[FieldPath] = frozenset({FieldPath(segments=("baz",))})
        target_section: type[BaseModel] = _Section

        @staticmethod
        def to_target(m: _Frag, c: TranspileCtx) -> _Section:
            return _Section()

        @staticmethod
        def from_target(s: _Section, c: TranspileCtx) -> _Frag:
            return _Frag()

    r.register(_CodecC)


def test_registry_rejects_duplicate_terminal_claim() -> None:
    r = CodecRegistry()
    r.register(_CodecA)
    with pytest.raises(DuplicateClaimError):
        r.register(_CodecB)


def test_registry_lookup_by_target_and_domain() -> None:
    r = CodecRegistry()
    r.register(_CodecA)
    found = r.get(BUILTIN_CLAUDE, Domains.IDENTITY)
    assert found is _CodecA
