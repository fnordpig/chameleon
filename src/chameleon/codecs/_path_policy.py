"""Shared helpers for home-relative path normalization at the neutral layer."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import TypeVar

T = TypeVar("T", bound=str)


def collapse_user_home(path: str) -> str:
    """Convert absolute home paths to ``~`` form.

    Non-home absolute paths, relative paths, and empty strings are passed
    through unchanged so we only mutate clearly transport-safe values.
    """
    if not path:
        return path
    normalized = os.path.normpath(path)
    home = os.path.normpath(os.path.expanduser("~"))
    if normalized == home:
        return "~"
    home_prefix = f"{home}{os.sep}"
    if normalized.startswith(home_prefix):
        return f"~/{normalized.removeprefix(home_prefix)}"
    return path


def expand_user_home(path: str) -> str:
    """Expand ``~`` values to the local home directory.

    Leaves values unchanged unless they start with ``~``.
    """
    if not path:
        return path
    if path.startswith("~"):
        return os.path.expanduser(path)
    return path


def map_dict_paths(
    values: Mapping[str, str],
    transform_key: Callable[[str], str],
) -> dict[str, str]:
    """Remap only dict keys that are paths."""
    return {transform_key(key): value for key, value in values.items()}


__all__ = ["collapse_user_home", "expand_user_home", "map_dict_paths"]
