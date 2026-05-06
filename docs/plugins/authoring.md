# Authoring a Chameleon target plugin

A target plugin is a Python package that ships a `Target` class via the
`chameleon.targets` entry point. Once installed alongside chameleon, the
plugin's target is auto-discovered, its `TargetId` is registered, and
codecs participate in `chameleon merge` runs.

## Required surface

Each plugin must provide:

1. **`Target` class** with three class-level attributes:

   ```python
   from typing import ClassVar
   from chameleon._types import TargetId
   from chameleon.codecs._protocol import Codec

   class MyTarget:
       target_id: ClassVar[TargetId] = TargetId(value="myname")
       assembler: ClassVar[type] = MyAssembler
       codecs: ClassVar[tuple[type[Codec], ...]] = (
           MyIdentityCodec,
           MyDirectivesCodec,
           # ... eight in total, one per Domains member;
           # use stubs that raise NotImplementedError for unimplemented domains
       )
   ```

2. **`Assembler` class** declaring the file table and providing
   `assemble`/`disassemble` static methods:

   ```python
   class MyAssembler:
       target: ClassVar[TargetId] = MyTarget.target_id
       full_model: ClassVar[type[BaseModel]] = ...  # generated upstream model
       files: ClassVar[tuple[FileSpec, ...]] = (
           FileSpec(
               live_path="~/.myagent/config.toml",
               repo_path="settings/config.toml",
               ownership=FileOwnership.FULL,
               format=FileFormat.TOML,
           ),
       )

       @staticmethod
       def assemble(per_domain, passthrough, *, existing=None): ...

       @staticmethod
       def disassemble(files): ...
   ```

3. **Eight codec classes** (one per `Domains` member) implementing
   `Codec`. See `chameleon.codecs._protocol.Codec` for the contract:

   ```python
   class MyIdentityCodec:
       target: ClassVar[TargetId] = MyTarget.target_id
       domain: ClassVar[Domains] = Domains.IDENTITY
       claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset({...})
       target_section: ClassVar[type[BaseModel]] = MyIdentitySection

       @staticmethod
       def to_target(model: Identity, ctx: TranspileCtx) -> MyIdentitySection: ...

       @staticmethod
       def from_target(section: MyIdentitySection, ctx: TranspileCtx) -> Identity: ...
   ```

4. **Vendored `_generated.py`** produced from the agent's canonical
   schema authority via the same pattern as
   `tools/sync-schemas/`. Document refresh in your plugin's README.

5. **Entry-point declaration** in your `pyproject.toml`:

   ```toml
   [project.entry-points."chameleon.targets"]
   myname = "my_chameleon_plugin:MyTarget"
   ```

## Registration flow

On `chameleon` import:

1. `TargetRegistry.discover()` walks `chameleon.targets` entry points.
2. For each, `register_target_id(name)` is called, adding the name to
   `TargetId`'s validator set.
3. The plugin's `TargetId` is now constructable; the target is
   discoverable via `chameleon targets list`.

## Schema discipline

- Codec `claimed_paths` must resolve in `MyAssembler.full_model` (the
  schema-drift test enforces this; exemptions require explicit
  allowlisting).
- Use Pydantic field aliases when your assembler reads JSON/TOML keys
  with snake-case-to-original-name remapping (datamodel-codegen
  produces this naturally with `--snake-case-field`).
- Stub codecs (deferred domains) raise `NotImplementedError`; the merge
  engine catches and skips them.

## Testing

- Add property-based round-trip tests under `tests/property/test_<my>_codec.py`.
- Add integration tests for the assembler's per-file routing.
- Run `uv run pytest -m schema_drift` to check codec/upstream-model
  alignment.
