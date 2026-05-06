"""Allow `python -m chameleon` invocation."""

from __future__ import annotations

import sys

from chameleon.cli import main

if __name__ == "__main__":
    sys.exit(main())
