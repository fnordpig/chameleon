# Curated fuzz corpus

This directory holds **committed** seed inputs that the fuzz suite
should exercise on every run, in addition to whatever Hypothesis
generates fresh.

## Why two stores?

* `.hypothesis/fuzz/` (gitignored) — the per-checkout
  `DirectoryBasedExampleDatabase` Hypothesis writes to under the
  `fuzz` profile. Captures recent failures and drives shrinking; not
  shared between operators or CI runs.
* `tests/fuzz/corpus/` (this directory, committed) — curated, named
  seeds that pin past bugs forever. Every file here is the minimised
  failing example from a real fuzz catch, plus a comment explaining
  what bug it pins.

The split keeps the per-checkout DB ephemeral (so it can be wiped
without losing the regression coverage) while the committed corpus
acts as the project-level memory of what fuzzing has caught.

## Adding a corpus seed

When a fuzz failure surfaces a real bug:

1. Land the production fix.
2. Read the minimised failing example out of Hypothesis's output (it
   prints a Python repr; the JSON/YAML form is `model.model_dump()`).
3. Save it here as a `.json` (or `.yaml` if it represents an
   operator-authored neutral file) named for the bug and the model:
   `directives_extra_key_rejected.json`,
   `identity_unicode_zwj_round_trip.yaml`.
4. Open the file with a leading comment / commented JSON-line / YAML
   comment block explaining what the seed pins. The Wave-F2
   `corpus_seeds()` fixture will iterate every file and parameterise
   the relevant fuzz tests over them.

## What does NOT belong here

* Random crashes that turned out to be flakes — verify, then drop.
* Examples Hypothesis can rediscover on its own in seconds — those
  belong in the persistent DB, not this committed set.
* Anything that requires runtime context to evaluate (file paths to
  fixtures outside `tests/fixtures/`, env-dependent values). Keep the
  seeds self-contained.
