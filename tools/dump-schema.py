"""Dump the JSON Schema produced by Pydantic for the Neutral model.

Used as part of the docs build:
    uv run python tools/dump-schema.py > docs/schema/neutral.schema.json
"""

from __future__ import annotations

import json
import sys

from chameleon.schema.neutral import Neutral

if __name__ == "__main__":
    schema = Neutral.model_json_schema()
    json.dump(schema, sys.stdout, indent=2)
    sys.stdout.write("\n")
