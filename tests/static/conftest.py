"""Pytest hooks for the no-silent-upstream-drops scorecard.

The static test under this directory builds a coverage matrix per
target — for each upstream wire field, it records exactly one of the
four dispositions (claimed / pass-through / loss-warned / silent-drop).

We surface that matrix at session end via ``pytest_terminal_summary`` so
the scorecard is visible on every run, not just on failure or with
``-s``. The numbers form the project's running scorecard; they should
move only on intentional codec / passthrough / LossWarning changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.static.test_no_silent_upstream_drops import COVERAGE_REGISTRY

if TYPE_CHECKING:
    from _pytest.config import Config
    from _pytest.terminal import TerminalReporter


def pytest_terminal_summary(
    terminalreporter: TerminalReporter,
    exitstatus: int,
    config: Config,
) -> None:
    """Render the no-silent-drops scorecard at session end.

    The hook is a no-op when ``COVERAGE_REGISTRY`` is empty, which
    happens whenever the static tests weren't selected (e.g., ``pytest
    tests/unit``). When the static tests *did* run, it prints one
    summary line per target.
    """
    del exitstatus, config  # unused; signature is fixed by pytest.
    if not COVERAGE_REGISTRY:
        return
    terminalreporter.write_sep("=", "no-silent-upstream-drops scorecard")
    for cov in COVERAGE_REGISTRY.values():
        terminalreporter.write_line(cov.render())
