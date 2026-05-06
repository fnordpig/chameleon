"""Chameleon neutral schema — eight typed Pydantic domain models plus
profiles overlay, pass-through namespace, and the composing `Neutral`
model. Domain modules land in the schema-domain tasks; this `__init__`
re-exports them once they exist.
"""

from __future__ import annotations
