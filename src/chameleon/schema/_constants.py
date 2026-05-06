"""Closed enums and built-in TargetId constants.

The Domains enum is closed by design (§7 note): adding a domain is a
core change. The OnConflict enum mirrors the §5.2 strategies. Built-in
TargetIds are registered at import time so they can be used as
sentinels in tests, CLI parsing, and codec class declarations.
"""

from __future__ import annotations

from enum import Enum

from chameleon._types import TargetId, register_target_id


class Domains(Enum):
    """The eight orthogonal slices of the schema ontology (§7).

    Members' .value is the lowercase YAML key in the neutral form
    (e.g. `Domains.IDENTITY.value == "identity"`).
    """

    IDENTITY = "identity"
    DIRECTIVES = "directives"
    CAPABILITIES = "capabilities"
    AUTHORIZATION = "authorization"
    ENVIRONMENT = "environment"
    LIFECYCLE = "lifecycle"
    INTERFACE = "interface"
    GOVERNANCE = "governance"


class OnConflict(Enum):
    """Non-interactive conflict resolution strategies (§5.2)."""

    FAIL = "fail"
    KEEP = "keep"
    PREFER_TARGET = "prefer-target"
    PREFER_NEUTRAL = "prefer-neutral"
    PREFER_LKG = "prefer-lkg"


# Register built-in target names so TargetId construction succeeds for them
# starting from import time. Plugin targets register themselves later via
# entry-point discovery (registries-and-target-protocol task); both flows
# funnel through register_target_id.
register_target_id("claude")
register_target_id("codex")

BUILTIN_CLAUDE: TargetId = TargetId(value="claude")
BUILTIN_CODEX: TargetId = TargetId(value="codex")


__all__ = [
    "BUILTIN_CLAUDE",
    "BUILTIN_CODEX",
    "Domains",
    "OnConflict",
]
