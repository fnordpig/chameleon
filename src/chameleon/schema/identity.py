"""identity domain — what model, where it's called, how it's authenticated.

Per §7 of the design spec, this domain owns model selection, provider/
endpoint configuration, authentication method, reasoning-effort and
thinking flags, service tier, and context-window controls. Some keys
(model, endpoint.base_url) are inherently target-specific; those use
the `Mapping[TargetId, V]` pattern from §7.1. Target-shared keys
(reasoning_effort, thinking) remain scalar.
"""

from __future__ import annotations

from enum import Enum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from chameleon._types import TargetId


class ReasoningEffort(Enum):
    """Reasoning effort vocabulary shared by Claude and Codex."""

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class AuthMethod(Enum):
    """How Chameleon expects the operator to authenticate to the target."""

    OAUTH = "oauth"
    API_KEY = "api-key"
    BEDROCK = "bedrock"
    VERTEX = "vertex"
    AZURE = "azure"


class IdentityEndpoint(BaseModel):
    """Per-target endpoint base URL (target-specific by nature)."""

    model_config = ConfigDict(extra="forbid")

    base_url: dict[TargetId, str] | None = None


class IdentityAuth(BaseModel):
    """Authentication configuration."""

    model_config = ConfigDict(extra="forbid")

    method: AuthMethod | None = None
    api_key_helper: str | None = Field(
        default=None,
        description="Path to an executable that prints an API key on stdout.",
    )


# Per-target model name. The value type is plain str at the neutral
# layer — codecs validate against each target's _generated literals.
IdentityModel = dict[TargetId, str]


class Identity(BaseModel):
    """The identity domain — composed of target-shared and per-target keys.

    All fields are optional so operators can partially configure the
    domain. Codecs decide what to do when a field is unset.
    """

    model_config = ConfigDict(extra="forbid")

    reasoning_effort: ReasoningEffort | None = None
    thinking: bool | None = None
    service_tier: str | None = None
    context_window: int | None = Field(default=None, ge=1)
    model: IdentityModel | None = Field(
        default=None,
        description=(
            "Target-specific model identifier. Must be a mapping "
            "TargetId -> model name (e.g. {claude: 'claude-sonnet-4-7'}); "
            "scalar values are rejected (§7.1)."
        ),
    )
    endpoint: IdentityEndpoint = Field(default_factory=IdentityEndpoint)
    auth: IdentityAuth = Field(default_factory=IdentityAuth)

    @field_serializer("model", when_used="json")
    def serialize_model(self, value: IdentityModel | None) -> dict[str, str] | None:
        """Serialize TargetId keys to their string values for JSON."""
        if value is None:
            return None
        return {key.value: val for key, val in value.items()}

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, value: object) -> IdentityModel | None:
        """Reconstruct TargetId keys from string keys or pass through TargetId dicts."""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("model must be a dict or None")
        if not value:
            # Empty dict
            return {}  # type: ignore[return-value]
        # Check first key to determine type
        first_key = next(iter(value.keys()))
        # If keys are already TargetId, return as-is
        if isinstance(first_key, TargetId):
            return cast(IdentityModel, value)
        # If keys are strings, reconstruct TargetId keys
        if isinstance(first_key, str):
            return {TargetId(value=str(k)): str(v) for k, v in value.items()}
        # Reject other key types
        raise ValueError(f"model keys must be TargetId or str, got {type(first_key)}")


__all__ = [
    "AuthMethod",
    "Identity",
    "IdentityAuth",
    "IdentityEndpoint",
    "IdentityModel",
    "ReasoningEffort",
]
