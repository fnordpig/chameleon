"""Unit tests for ``_write_leaf``'s schema-aware coercion (B3).

The merge resolver returns the chosen value as a raw object — for an
``identity.reasoning_effort`` field this can be a ``str`` like
``"xhigh"`` rather than a ``ReasoningEffort`` enum member. ``_write_leaf``
must coerce the raw value through the field's annotation before
``setattr``, otherwise downstream codec ``to_target`` calls (which
expect Pydantic-validated types) crash with e.g.
``AttributeError: 'str' object has no attribute 'value'``.

This is the regression net for the engine's leaf writer; the integration
proof lives in ``tests/integration/test_exemplar_smoke.py::
test_prefer_neutral_resolves_real_conflict``.
"""

from __future__ import annotations

from chameleon.merge.engine import _write_leaf
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.identity import Identity, ReasoningEffort
from chameleon.schema.neutral import Neutral


def _fresh_neutral() -> Neutral:
    return Neutral(schema_version=1)


def test_scalar_enum_leaf_coerces_str_to_enum_member() -> None:
    """The scalar case: resolver returns 'xhigh' (str) for a
    ReasoningEffort | None field — _write_leaf must hand back the
    enum member, not the raw string."""
    root = _fresh_neutral()
    _write_leaf(
        root,
        segments=("identity", "reasoning_effort"),
        target_key=None,
        value="xhigh",
    )
    leaf = root.identity.reasoning_effort
    assert isinstance(leaf, ReasoningEffort), (
        f"expected ReasoningEffort enum member, got {type(leaf).__name__}: {leaf!r}"
    )
    assert leaf is ReasoningEffort.XHIGH


def test_scalar_enum_leaf_passes_enum_through_unchanged() -> None:
    """An already-coerced enum value must round-trip cleanly."""
    root = _fresh_neutral()
    _write_leaf(
        root,
        segments=("identity", "reasoning_effort"),
        target_key=None,
        value=ReasoningEffort.HIGH,
    )
    assert root.identity.reasoning_effort is ReasoningEffort.HIGH


def test_scalar_enum_leaf_accepts_none() -> None:
    """The resolver legitimately returns None to clear an Optional
    field; coercion must accept None for ``X | None`` annotations."""
    root = _fresh_neutral()
    root.identity = Identity(reasoning_effort=ReasoningEffort.HIGH)
    _write_leaf(
        root,
        segments=("identity", "reasoning_effort"),
        target_key=None,
        value=None,
    )
    assert root.identity.reasoning_effort is None


def test_target_keyed_dict_leaf_coerces_value_type() -> None:
    """For ``dict[TargetId, str]`` (identity.model), the value type is
    ``str``; coercion must operate on V, not on the whole dict.

    This case is currently a string already, so the assertion is that
    coercion preserves it AND preserves sibling target_key entries.
    """
    root = _fresh_neutral()
    # Pre-seed a sibling claude entry — _write_leaf must not wipe it.
    _write_leaf(
        root,
        segments=("identity", "model"),
        target_key=BUILTIN_CLAUDE,
        value="claude-sonnet-4-7",
    )
    _write_leaf(
        root,
        segments=("identity", "model"),
        target_key=BUILTIN_CODEX,
        value="gpt-5.5",
    )
    assert root.identity.model is not None
    assert root.identity.model[BUILTIN_CLAUDE] == "claude-sonnet-4-7"
    assert root.identity.model[BUILTIN_CODEX] == "gpt-5.5"


def test_str_keyed_dict_leaf_coerces_to_value_model() -> None:
    """For ``dict[str, McpServer]`` (capabilities.mcp_servers), the
    value type is the ``McpServer`` BaseModel; coercion via
    TypeAdapter must accept a plain dict and produce a model instance.
    """
    root = _fresh_neutral()
    _write_leaf(
        root,
        segments=("capabilities", "mcp_servers"),
        target_key=None,
        value={"command": "echo", "args": ["hi"]},
        dict_key="hello-server",
    )
    bag = root.capabilities.mcp_servers
    assert "hello-server" in bag
    server = bag["hello-server"]
    # Pydantic should have coerced the dict into the McpServer model.
    # We don't depend on the exact class symbol — just that it has a
    # ``command`` attribute carrying the string we passed in.
    assert getattr(server, "command", None) == "echo"


def test_dict_leaf_drops_key_on_none_value() -> None:
    """When the resolver returns None for a target-keyed leaf, the
    write must drop the key (existing engine behaviour) — coercion
    must not interfere with that None-pop semantics."""
    root = _fresh_neutral()
    _write_leaf(
        root,
        segments=("identity", "model"),
        target_key=BUILTIN_CLAUDE,
        value="claude-sonnet-4-7",
    )
    _write_leaf(
        root,
        segments=("identity", "model"),
        target_key=BUILTIN_CODEX,
        value="gpt-5.5",
    )
    _write_leaf(
        root,
        segments=("identity", "model"),
        target_key=BUILTIN_CLAUDE,
        value=None,
    )
    assert root.identity.model is not None
    assert BUILTIN_CLAUDE not in root.identity.model
    assert root.identity.model[BUILTIN_CODEX] == "gpt-5.5"
