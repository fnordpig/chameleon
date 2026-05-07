"""Hypothesis-driven fuzz suite scaffolding (Wave-F1).

Wave-F1 lands the strategy library, conftest, marker registration, and
the CI workflow; Wave-F2 lands the cross-target differential, schema-
extras, and unicode-torture fuzz tests that consume them.

The fuzz suite runs under two profiles:

* ``default`` — fast (50 examples, 200ms deadline), used inside the
  normal pytest invocation when an operator runs ``uv run pytest -m
  fuzz`` against the local checkout.
* ``fuzz`` — long-running (500 examples, 10s deadline) with a
  persistent ``DirectoryBasedExampleDatabase`` at ``.hypothesis/fuzz/``.
  Selected by setting ``HYPOTHESIS_PROFILE=fuzz``; this is what the
  nightly GHA workflow uses.

See ``tests/fuzz/README.md`` for operator instructions and
``tests/fuzz/corpus/README.md`` for the curated-seed discipline.
"""

from __future__ import annotations
