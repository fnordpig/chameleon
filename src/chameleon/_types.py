"""Foundational typed primitives shared across Chameleon.

Per §5.4 of the design spec: everything is typed — no stringly-typed
identifiers float free in the codebase. TargetId is a registry-validated
newtype; FieldPath is a tuple-based path through a Pydantic model;
FileFormat / FileOwnership are closed enums; FileSpec is the typed
record that assemblers use to declare which on-disk files they own.
"""

import re
from enum import Enum
from typing import NamedTuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Sentinel used by TargetId._coerce_from_str_or_targetid to recognize when
# Pydantic re-passes a TargetId instance back into model_validate (e.g. via
# dict-key coercion); we accept the bare value rather than wrapping again.

# ------------------------------------------------------------------
# JsonValue — recursive scalar/list/dict type for unstructured payloads
# ------------------------------------------------------------------

# PEP 695 type alias (Python 3.12+) gives Pydantic a named hook it can
# resolve for the recursive list/dict branches without hitting infinite
# recursion during schema construction.
type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


# ------------------------------------------------------------------
# TargetId — registry-validated identifier for a Chameleon target
# ------------------------------------------------------------------

# Mutable registry populated at startup by entry-point discovery (the
# registries-and-target-protocol task) plus tests via `register_target_id`.
# Kept module-private; external code adds via the helper, never by mutating
# directly.
_TARGET_REGISTRY: set[str] = set()

# Regex for valid target names: start and end with alphanumeric,
# interior characters may be alphanumeric, hyphen, or underscore.
# Also allows single-character names.
_VALID_TARGET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*[A-Za-z0-9]$|^[A-Za-z0-9]$")


def register_target_id(name: str) -> None:
    """Register a target name as valid for `TargetId` construction.

    Called at startup by the targets registry (registries-and-target-protocol
    task) and by tests. Idempotent. Names must start AND end with an
    alphanumeric character; interior characters may be alphanumeric, hyphen,
    or underscore.
    """
    if not _VALID_TARGET_NAME.match(name):
        msg = f"target name must be alphanumeric (interior `-` or `_` allowed); got {name!r}"
        raise ValueError(msg)
    _TARGET_REGISTRY.add(name)


def registered_target_names() -> frozenset[str]:
    """Return the current set of registered target names (for diagnostics)."""
    return frozenset(_TARGET_REGISTRY)


class TargetId(BaseModel):
    """Registry-validated target identifier.

    Construction fails if `value` is not in `_TARGET_REGISTRY`. Tests
    register names via `register_target_id`; production code registers
    via the targets-registry entry-point discovery path (the
    registries-and-target-protocol task).
    """

    model_config = ConfigDict(frozen=True)

    value: str

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_str_or_targetid(cls, data: object) -> object:
        # Allow bare-string construction (TargetId("claude")) and YAML/JSON
        # mapping-key resolution (where Pydantic hands us str keys for
        # dict[TargetId, V] fields).
        if isinstance(data, str):
            return {"value": data}
        if isinstance(data, TargetId):
            return {"value": data.value}
        return data

    @field_validator("value")
    @classmethod
    def _must_be_registered(cls, v: str) -> str:
        if v not in _TARGET_REGISTRY:
            registered = sorted(_TARGET_REGISTRY)
            msg = (
                f"unknown target {v!r}; registered targets: {registered}. "
                f"Plugins register via Python entry points; tests register via "
                f"chameleon._types.register_target_id."
            )
            raise ValueError(msg)
        return v

    def __str__(self) -> str:
        return self.value


# ------------------------------------------------------------------
# FieldPath — typed path through a Pydantic model's field hierarchy
# ------------------------------------------------------------------


class FieldPath(NamedTuple):
    """A path through a Pydantic model's nested fields.

    Each segment is the literal field name in the parent model. Codec
    `claimed_paths` is a frozenset of these. Validated against the
    target's `FullTargetModel` at registry-load time (the
    registries-and-target-protocol task).
    """

    segments: tuple[str, ...]

    def render(self) -> str:
        """Dotted human-readable rendering, e.g. ``permissions.allow``."""
        return ".".join(self.segments)

    def is_prefix_of(self, other: "FieldPath") -> bool:
        """True iff `self` is an ancestor of `other` (or equal)."""
        if len(self.segments) > len(other.segments):
            return False
        return other.segments[: len(self.segments)] == self.segments


# ------------------------------------------------------------------
# FileFormat, FileOwnership — closed enums
# ------------------------------------------------------------------


class FileFormat(Enum):
    """Wire format for a target file."""

    JSON = "json"
    TOML = "toml"
    YAML = "yaml"


class FileOwnership(Enum):
    """Ownership semantics for a file Chameleon writes.

    FULL: Chameleon owns every byte; safe to overwrite atomically.
    PARTIAL: Chameleon owns specific keys only (see FileSpec.owned_keys);
        concurrency discipline in §10.5 applies on every write.
    """

    FULL = "full"
    PARTIAL = "partial"


# ------------------------------------------------------------------
# FileSpec — typed declaration of a target-owned file
# ------------------------------------------------------------------


class FileSpec(BaseModel):
    """Declaration of one file an assembler reads/writes.

    `live_path` is the operator-visible filesystem path (may contain
    `~` for home dir; resolved at I/O time). `repo_path` is the
    state-repo-relative path under `targets/<target>/settings/`.
    """

    model_config = ConfigDict(frozen=True)

    live_path: str
    repo_path: str
    ownership: FileOwnership
    format: FileFormat
    owned_keys: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def _partial_must_declare_owned_keys(self) -> "FileSpec":
        if self.ownership is FileOwnership.PARTIAL and not self.owned_keys:
            msg = (
                f"FileSpec for {self.live_path!r} declares ownership=PARTIAL "
                f"but provides no owned_keys; partial-ownership writes must "
                f"name the top-level keys Chameleon owns (§10.5)."
            )
            raise ValueError(msg)
        return self


__all__: list[str] = [
    "FieldPath",
    "FileFormat",
    "FileOwnership",
    "FileSpec",
    "JsonValue",
    "TargetId",
    "register_target_id",
    "registered_target_names",
]
