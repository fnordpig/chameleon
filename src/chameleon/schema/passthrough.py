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

In addition to the wire-shaped ``items`` bag, each per-target slot also
carries a ``target_specific`` mapping (§2.2 of the resolution-memory
spec) keyed by ``FieldPath.render()`` strings. ``items`` is for
pass-through of fields the schema doesn't model at all; ``target_specific``
is for fields the schema DOES model where the operator has explicitly
chosen to disable cross-target propagation by recording a TARGET_SPECIFIC
resolution. Keeping the two distinct lets the fuzzer pin "every key in
``target_specific`` is a valid neutral path" without confusing it with
raw wire keys.
"""

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import JsonValue


class PassThroughBag(BaseModel):
    """Untyped-at-schema, target-validated-at-codec pass-through container."""

    model_config = ConfigDict(extra="forbid")

    items: dict[str, JsonValue] = Field(default_factory=dict)
    target_specific: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = ["PassThroughBag"]
