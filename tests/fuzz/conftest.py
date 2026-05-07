"""Hypothesis profile registration for the fuzz suite (Wave-F1).

Two profiles, selected by the ``HYPOTHESIS_PROFILE`` env var:

* ``default`` — fast (50 examples, 200ms per-example deadline). Used
  when an operator runs ``uv run pytest -m fuzz`` locally for a smoke
  pass before pushing.
* ``fuzz`` — long-running (500 examples, 10s deadline) with a
  persistent ``DirectoryBasedExampleDatabase`` rooted at
  ``.hypothesis/fuzz/``. The persistent DB lets failing examples replay
  forever and lets every nightly run incrementally widen coverage.
  Used by ``.github/workflows/fuzz.yml``.

The default-active profile is whatever ``HYPOTHESIS_PROFILE`` says at
conftest-import time. If it is unset, ``default`` is loaded.

Importing :mod:`tests.fuzz.strategies` here at conftest time wires the
``register_type_strategy`` calls before any fuzz test collects, so
``@given(model: NeutralSubmodel)`` works without per-test boilerplate.
"""

from __future__ import annotations

import os
from pathlib import Path

from hypothesis import HealthCheck, settings
from hypothesis.database import DirectoryBasedExampleDatabase

# Importing the strategies module is the side-effecting wire-up: every
# `st.register_type_strategy(...)` call inside it runs at import. The
# `noqa` is intentional — this is the entire point of the import.
from tests.fuzz import strategies as _strategies  # noqa: F401

# ----------------------------------------------------------------------
# Profile registration. Both profiles suppress the `data_too_large` and
# `filter_too_much` health checks because Wave-F2's adversarial unicode
# and extra-keys composites legitimately produce inputs near the upper
# end of Hypothesis's defaults. The trade-off: a generator pathology
# would surface as deadline-exceeded warnings rather than health-check
# failures; the `fuzz` profile's wider deadline gives that signal room.
# ----------------------------------------------------------------------

_DEFAULT_PROFILE_NAME = "default"
_FUZZ_PROFILE_NAME = "fuzz"
_FUZZ_DB_PATH = Path(".hypothesis") / "fuzz"

settings.register_profile(
    _DEFAULT_PROFILE_NAME,
    max_examples=50,
    deadline=200,  # milliseconds
    suppress_health_check=[HealthCheck.data_too_large, HealthCheck.filter_too_much],
)

settings.register_profile(
    _FUZZ_PROFILE_NAME,
    max_examples=500,
    deadline=10_000,  # 10s — the spec's long-running budget.
    derandomize=False,
    database=DirectoryBasedExampleDatabase(str(_FUZZ_DB_PATH)),
    suppress_health_check=[HealthCheck.data_too_large, HealthCheck.filter_too_much],
)

_active_profile = os.environ.get("HYPOTHESIS_PROFILE", _DEFAULT_PROFILE_NAME)
settings.load_profile(_active_profile)
