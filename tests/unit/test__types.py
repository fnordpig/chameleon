"""Tests for chameleon._types — TargetId, JsonValue, FieldPath, etc."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from chameleon._types import (
    FieldPath,
    FileFormat,
    FileOwnership,
    FileSpec,
    JsonValue,
    TargetId,
    register_target_id,
)


class TestTargetId:
    def test_unregistered_target_id_rejected(self) -> None:
        # No targets are registered until the registries-and-target-protocol
        # task wires them up; until then, construction should fail unless the
        # operator pre-registers a name.
        with pytest.raises(ValidationError):
            TargetId(value="never-registered")

    def test_registered_target_id_accepted(self) -> None:
        register_target_id("hypothetical")
        tid = TargetId(value="hypothetical")
        assert tid.value == "hypothetical"

    def test_target_id_is_hashable(self) -> None:
        register_target_id("h2")
        a = TargetId(value="h2")
        b = TargetId(value="h2")
        assert {a, b} == {a}

    def test_target_id_equality(self) -> None:
        register_target_id("eq")
        assert TargetId(value="eq") == TargetId(value="eq")


class TestRegisterTargetId:
    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            register_target_id("")

    def test_rejects_leading_separator(self) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            register_target_id("-claude")

    def test_rejects_trailing_separator(self) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            register_target_id("claude-")

    def test_rejects_invalid_chars(self) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            register_target_id("claude/x")

    def test_accepts_single_char(self) -> None:
        register_target_id("x")  # should not raise

    def test_accepts_interior_separators(self) -> None:
        register_target_id("claude-code-1")  # should not raise

    def test_accepts_underscores(self) -> None:
        register_target_id("my_target")  # should not raise


class TestFieldPath:
    def test_field_path_is_a_tuple(self) -> None:
        p = FieldPath(segments=("permissions", "allow"))
        assert p.segments == ("permissions", "allow")
        assert len(p.segments) == 2

    def test_field_path_is_hashable(self) -> None:
        p1 = FieldPath(segments=("a", "b"))
        p2 = FieldPath(segments=("a", "b"))
        assert {p1, p2} == {p1}

    def test_field_path_render_dotted(self) -> None:
        assert FieldPath(segments=("permissions", "allow")).render() == "permissions.allow"


class TestFileSpec:
    def test_file_spec_round_trips(self) -> None:
        spec = FileSpec(
            live_path="~/.claude/settings.json",
            repo_path="settings/claude/settings.json",
            ownership=FileOwnership.FULL,
            format=FileFormat.JSON,
        )
        assert spec.ownership is FileOwnership.FULL
        assert spec.format is FileFormat.JSON

    def test_partial_ownership_requires_owned_keys(self) -> None:
        # Partial-ownership FileSpecs must declare which keys we own (§10.5).
        with pytest.raises(ValidationError):
            FileSpec(
                live_path="~/.claude.json",
                repo_path="settings/dotfiles/claude.json",
                ownership=FileOwnership.PARTIAL,
                format=FileFormat.JSON,
                # owned_keys missing
            )


class TestJsonValue:
    def test_json_value_accepts_scalars_and_nested(self) -> None:
        # JsonValue is a recursive Pydantic-compatible type; construction
        # via TypeAdapter validates.
        ta = TypeAdapter(JsonValue)
        assert ta.validate_python(None) is None
        assert ta.validate_python(True) is True
        assert ta.validate_python(42) == 42
        assert ta.validate_python(3.14) == 3.14
        assert ta.validate_python("hi") == "hi"
        assert ta.validate_python([1, "two", None]) == [1, "two", None]
        assert ta.validate_python({"a": [1, {"b": False}]}) == {"a": [1, {"b": False}]}
