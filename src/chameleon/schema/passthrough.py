"""Pass-through namespace for target-unique features (§7.2).

A `PassThroughBag` is parametric over the target's generated model.
At the schema layer, the bag's items are typed as `JsonValue` (recursive),
since the neutral form is YAML and we don't constrain shapes the schema
doesn't model. At the codec layer, the disassembler upgrades these to
target-native types when reading the live target file (preserving e.g.
TOML datetimes that Codex round-trips).

For V0 the bag is a single Pydantic model with ``items: dict[str, JsonValue]``;
the per-target typing is enforced at the codec/assembler boundary rather
than via Python generics on the bag itself, which keeps the YAML schema
simple and avoids forcing operators to namespace pass-through values by
their target's model class name.
"""

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import JsonValue


class PassThroughBag(BaseModel):
    """Untyped-at-schema, target-validated-at-codec pass-through container."""

    model_config = ConfigDict(extra="forbid")

    items: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = ["PassThroughBag"]
