"""Chameleon CLI entry point.

scaffold-package skeleton: parses --help/--version and exits 0. Subcommands land
in cli-skeleton.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from chameleon import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chameleon",
        description=(
            "Transpile a neutral agent configuration into Claude Code, "
            "Codex CLI, and other agent-specific formats — and back again."
        ),
    )
    parser.add_argument("--version", action="version", version=f"chameleon {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code.

    scaffold-package: prints help and exits 0. Subcommands are added in cli-skeleton.
    """
    args = list(argv) if argv is not None else sys.argv[1:]
    parser = _build_parser()
    if "--help" in args or "-h" in args:
        parser.print_help()
        return 0
    parser.parse_args(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
