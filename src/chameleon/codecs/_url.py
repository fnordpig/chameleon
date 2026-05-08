"""URL helpers shared by codecs.

Centralizes the GitHub-URL parser used by both the Claude and Codex
capabilities codecs to canonicalize hand-authored ``https://github.com/X/Y``
or ``git@github.com:X/Y`` marketplace sources to the higher-detail
neutral form ``PluginMarketplaceSource(kind='github', repo='X/Y')``.

The parser is intentionally strict: it accepts only URLs whose host is
exactly ``github.com`` and whose path is exactly ``/<owner>/<name>`` (with
an optional ``.git`` suffix). Custom SSH aliases (``git@github-org:...``),
GitHub Gist URLs (``gist.github.com``), and sub-paths
(``/X/Y/tree/main``) are NOT promoted — those forms carry operator intent
the canonical ``github`` shape cannot express, so they remain
``kind='git'``.
"""

from __future__ import annotations

import re

# HTTPS / HTTP form: https://github.com/owner/name(.git)?
# Reject sub-paths (anything past name), trailing ``/`` is OK.
# ``[^/]+?`` is non-greedy so ``(?:\.git)?`` can claim the trailing
# ``.git`` instead of folding it into the captured name.
_HTTPS_GITHUB_RE = re.compile(
    r"\Ahttps?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?\Z",
)

# SSH "scp-like" form: git@github.com:owner/name(.git)?
# Accept only the literal ``github.com`` host — custom SSH aliases like
# ``git@github-org:...`` are rejected because chameleon can't tell which
# operator-managed alias they map to and rewriting the URL would change
# auth behaviour.
_SSH_GITHUB_RE = re.compile(
    r"\Agit@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?\Z",
)


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Return ``(owner, name)`` if ``url`` is a canonical GitHub repo URL.

    Returns ``None`` for any URL that doesn't match the canonical
    ``https://github.com/<owner>/<name>`` (or ``git@github.com:<owner>/<name>``)
    shape — including custom SSH aliases, gist URLs, and sub-paths.
    """

    m = _HTTPS_GITHUB_RE.match(url)
    if m is None:
        m = _SSH_GITHUB_RE.match(url)
    if m is None:
        return None
    owner, name = m.group(1), m.group(2)
    # Empty owner/name (e.g. ``https://github.com//``) is not a valid repo.
    if not owner or not name:
        return None
    return owner, name


__all__ = ["parse_github_url"]
