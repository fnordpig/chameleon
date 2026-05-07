# Chameleon fuzz suite

Hypothesis-driven fuzz tests for the neutral schema and codecs. Wave-F1
ships the scaffolding (strategies, profiles, marker, GHA workflow) plus
one smoke test. Wave-F2 will add the cross-target differential,
schema-extras rejection, and unicode-torture round-trip tests.

## Running

The default `pytest` invocation skips fuzz tests entirely — the
`-m 'not fuzz'` selector lives in `pyproject.toml`'s `addopts`. To run
fuzz tests explicitly:

| Command | Profile | Examples | Deadline | Persistent DB? |
|---|---|---|---|---|
| `uv run pytest -m fuzz` | `default` | 50 | 200 ms | no |
| `HYPOTHESIS_PROFILE=fuzz uv run pytest -m fuzz` | `fuzz` | 500 | 10 s | yes (`.hypothesis/fuzz/`) |

The `fuzz` profile is what `.github/workflows/fuzz.yml` runs nightly.
Locally, the `default` profile is enough for a smoke pass before
pushing — it returns in roughly a second per fuzz test on a
modern laptop.

## Selecting one test

Standard pytest selection works:

```sh
uv run pytest -m fuzz tests/fuzz/test_smoke.py::test_claude_identity_round_trip_smoke
```

## Where things live

* `strategies.py` — every neutral submodel registered as a Hypothesis
  strategy. Add a `register_type_strategy(Model, ...)` call here when
  you add a new Pydantic model to `src/chameleon/schema/`.
* `conftest.py` — registers the two profiles and loads whichever one
  `HYPOTHESIS_PROFILE` selects (default `default`).
* `corpus/` — curated seed inputs. See `corpus/README.md` for the
  discipline. Failing examples Hypothesis discovers should be pruned
  here as commit-once regression seeds.
* `.hypothesis/fuzz/` (gitignored) — the persistent example database
  the `fuzz` profile writes to. Never commit it.

## Adding a fuzz test

1. Mark with `pytestmark = pytest.mark.fuzz` at module scope (or use
   `@pytest.mark.fuzz` per-test).
2. Use `@given(model=...)` with a typed parameter; the registered
   strategy resolves automatically. For composite scenarios use the
   helpers in `strategies.py` (e.g. `extra_keys_at_random_depth`,
   `partial_neutral_with_holes`, `unicode_torture`).
3. Keep per-test overrides for adversarial slices, not for routine
   shaping — bound shaping belongs in `strategies.py` so every test
   benefits.
