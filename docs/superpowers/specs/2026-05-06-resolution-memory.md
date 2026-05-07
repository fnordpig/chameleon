# Resolution memory + target-specific resolution

**Date:** 2026-05-06
**Status:** Design spec for Wave-15 implementation.

Today the merge engine re-classifies every conflict from scratch on
each `chameleon merge` invocation and re-prompts the operator. There
is also no way to express "this field is intentionally different per
target" — the only choices are pick-one-side, take-neutral, take-N₀,
or skip. Operators with legitimately target-specific values (e.g.
`identity.reasoning_effort=xhigh` on Codex but `high` on Claude
because they explicitly want different costs) have to either re-prompt
every time or pick one and lose the other.

This spec adds two complementary mechanisms.

## 1. Resolution memory

Persist operator decisions in the neutral file under a new
`resolutions` block:

```yaml
resolutions:
  "identity.reasoning_effort":
    decided_at: "2026-05-06T18:42:00Z"
    decision: take_neutral
    decision_hash: "sha256:abc123…"   # hash of (N₀, N₁, per_target) at decision time

  "capabilities.plugin_marketplaces[archivium-marketplace]":
    decided_at: "2026-05-06T18:43:12Z"
    decision: take_target
    decision_target: claude
    decision_hash: "sha256:def456…"
```

### Schema (typed; per the project's strict-typing rule)

```python
class ResolutionDecisionKind(StrEnum):
    TAKE_NEUTRAL    = "take_neutral"     # neutral wins
    TAKE_LKG        = "take_lkg"         # last-known-good wins
    TAKE_TARGET     = "take_target"      # specific target wins (decision_target set)
    TARGET_SPECIFIC = "target_specific"  # preserve each target's value separately
    SKIP            = "skip"             # leave unresolved (rare; not auto-replayed)

class Resolution(BaseModel):
    decided_at:      datetime
    decision:        ResolutionDecisionKind
    decision_target: TargetId | None = None      # only when decision=TAKE_TARGET
    decision_hash:   str                         # invalidation key

class Resolutions(BaseModel):
    items: dict[str, Resolution] = Field(default_factory=dict)
    # key is FieldPath.render() — a stable string with target-keyed and
    # dict-keyed indexers visible (e.g. `identity.model[claude]` or
    # `capabilities.plugin_marketplaces[archivium-marketplace]`)
```

### Invalidation hash

When a decision is recorded, the engine computes:

```python
sha256(json.dumps({
    "n0": _serialize(record.n0),
    "n1": _serialize(record.n1),
    "per_target": {tid.value: _serialize(v) for tid, v in record.per_target.items()},
}, sort_keys=True).encode("utf-8"))
```

On next merge, recompute the hash from the *current* `ChangeRecord` and
look up in `resolutions`. Three outcomes:

- **No entry** → prompt as today.
- **Entry exists, hash matches** → apply silently. The disagreement is
  the same disagreement the operator already decided.
- **Entry exists, hash differs** → prompt, displaying the prior
  decision as default and noting "values have changed since you last
  decided this on `<decided_at>`."

This is the right invalidation rule because it captures "is the
operator's decision still responsive to the data they decided over."
A stale decision shouldn't silently apply to a new disagreement.

### GC

After a successful merge, walk `resolutions.items` and remove entries
where the unified neutral field's value now equals every per-target
value (i.e., the disagreement has been resolved through a different
path — operator edited neutral or both targets converged). This
prevents `resolutions` from accumulating entries forever as the
operator's setup evolves.

GC runs on **successful** merges only. A failed merge leaves
resolutions intact in case the operator wants to retry.

## 2. Target-specific resolution

The `InteractiveResolver` gains a new choice:

```
choose: [n] neutral / [a] claude / [b] codex / [k] revert / [t] target-specific / [s] skip
```

Choosing `[t]` does:

1. Removes the conflicting field from the unified neutral path.
2. Writes each target's current value to
   `targets.<target_id>.<field_path>` in neutral (using the existing
   `targets.<target>.items` pass-through namespace, but with structural
   reach into typed paths — see §2.1).
3. Records the resolution as `decision: TARGET_SPECIFIC`.

On future merges:

- The unified path is unset → no conflict to detect.
- Each target's encoder reads from `targets.<self>.<path>` if the
  unified path is unset and the target-specific resolution exists.
- The engine emits a `LossWarning` per `target_specific` resolution at
  merge time:
  `"identity.reasoning_effort: target-specific by operator decision; not propagating cross-target"`.

The operator can re-unify by editing neutral.yaml directly: move the
value from `targets.<target>.identity.reasoning_effort` to the unified
`identity.reasoning_effort` and delete the `resolutions` entry.

### 2.1 How target-specific values plumb through

Today's pass-through namespace stores raw `JsonValue` blobs at
`targets.<target>.items[<top_level_key>]`. That's wire-shaped and
target-namespaced — useful for fields chameleon doesn't model in
neutral, but wrong for fields that ARE typed in neutral and where we
just want to disable cross-target propagation.

The cleanest plumb: extend `MergeEngine.merge()` so that *before*
calling `codec_cls.to_target(neutral_field, ctx2)`, it checks
`resolutions` for any `target_specific` entry under the codec's
domain. For each such entry, it patches the per-target neutral
submodel with the target-specific value.

```python
target_neutral = composed.model_copy(deep=True)
for path_str, resolution in neutral.resolutions.items.items():
    if resolution.decision is ResolutionDecisionKind.TARGET_SPECIFIC:
        path = FieldPath.parse(path_str)
        target_value = neutral.targets.get(target_id, PassThroughBag()).get_at(path)
        if target_value is not None:
            _set_leaf(target_neutral, path, target_value)
```

The codec sees a per-target neutral that has the target-specific value
in the unified slot. No codec changes needed. Reverse path on
disassemble: when the engine sees that a `target_specific` resolution
exists for `(domain, path)`, it harvests the value from the per-target
neutral into `targets.<target>.<path>` rather than the unified
composed neutral.

### 2.2 Storage shape for target-specific values

The current `PassThroughBag` is `dict[str, JsonValue]` — flat top-level
keys. To support `targets.claude.identity.reasoning_effort` we need
nested storage. Two options:

- **Option α**: Extend `PassThroughBag.items` to allow nested dicts, and
  walk the path to read/write.
- **Option β**: Add a separate `target_specific: dict[FieldPath, JsonValue]`
  field on `Targets[TargetId]` distinct from `items`.

α keeps the storage uniform but blurs the distinction between "wire
key the codec doesn't model" and "neutral path the operator chose to
target-namespace." β separates the two concerns and is more typed.

**Recommendation: β.** A `Targets[TargetId]` instance gains a
`target_specific: dict[str, JsonValue]` (keyed by `FieldPath.render()`).
The fuzzer can pin "every key in `target_specific` is a valid neutral
path", which `items` (raw wire pass-through) can't satisfy.

## 3. Non-interactive strategies and resolution memory

When the operator passes `--on-conflict=fail` / `=keep` /
`=prefer-neutral` / `=prefer=<target>`, should those non-interactive
choices persist as resolutions?

**No.** Non-interactive strategies are stateless by design — they
encode "this is my batch policy for THIS run." Persisting would
silently extend a one-shot decision into a permanent one. Operators
running login-time `chameleon merge --on-conflict=keep` don't want
KEEP on every field forever.

**Only interactive resolutions persist.** This is documented in the
help text and in the resolver implementation.

## 4. Resolution UI

Two CLI surfaces:

- **`chameleon resolutions list`** — shows all stored resolutions with
  age, decision, and current applicability (does the hash still match
  the current per-target reality?).
- **`chameleon resolutions clear [<path>]`** — removes one or all
  resolutions, forcing re-prompts.

Both are operator escape hatches. Most operators won't need them.
They're the principled answer to "I changed my mind."

## 5. Wave-15 dispatch plan

Two parallel agents, file-disjoint:

- **W15-A (schema + engine)**: `Resolution`/`Resolutions` typed schema,
  field-path hash computation, lookup-before-prompt in engine,
  TARGET_SPECIFIC plumb, GC pass, `chameleon resolutions` CLI
  subcommand. Touches `schema/neutral.py`, `merge/engine.py`,
  `merge/changeset.py` (hash helper), `cli.py` (new subcommand). This
  is the bigger half.

- **W15-B (interactive UI)**: extend `InteractiveResolver` with `[t]`
  choice; render the prior decision as a default when the hash matches;
  surface "values changed since prior decision" message when the hash
  has rolled. Touches `merge/resolve.py` and adds tests under
  `tests/conflicts/`.

The interaction surface between A and B is the new `Resolution` typed
model (A creates it; B consumes it). They can develop in parallel as
long as A's typed shape is locked first; B's brief includes A's
schema as an interface contract to read against, not write.

## 6. Acceptance criteria

A merged Wave-15 satisfies:

1. **Same disagreement, same decision: no re-prompt.** Operator picks
   `[a]` for `identity.reasoning_effort`; rerun merge; no prompt;
   resolution applied silently.
2. **Different disagreement, same path: re-prompt with prior shown.**
   Operator picks `[a]` (claude=`high`); changes claude to `xhigh`;
   rerun merge; prompts again with "your prior decision was claude:high"
   shown.
3. **`[t]` removes cross-target propagation.** Operator picks `[t]` for
   `identity.reasoning_effort`; live values stay distinct on each
   target; rerun merge; no prompt; LossWarning surfaces the
   target-specific status.
4. **GC removes stale entries.** Operator's three resolutions resolve
   themselves through later edits (values converge); next clean merge
   prunes the entries.
5. **Non-interactive strategies don't persist.** `merge --on-conflict=keep`
   produces zero new `resolutions` entries.
6. **`chameleon resolutions list/clear` works.** Both subcommands
   present, typed, tested.

## 7. Out of scope for Wave-15

- **Cross-FieldPath resolution composition.** A resolution on
  `capabilities.plugins[a@m]` is independent of one on
  `capabilities.plugins[b@m]`. No "all plugin marketplaces" wildcard.
- **Resolution import/export.** Operators with two machines could
  benefit from sharing decisions; defer.
- **Resolution-driven default for non-interactive strategies.** Could
  imagine `--on-conflict=remember`, but that's a v0.6+ ergonomic
  addition once the basic mechanism is shipped.
- **Conflict-detection on `targets.<target>.<path>` itself.** If two
  merges happen and the per-target value changes underneath a
  target-specific resolution, that's not a conflict — it's the operator
  authoring per-target values, which is the explicit semantic.

---

*End of design.*
