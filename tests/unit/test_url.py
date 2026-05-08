"""Unit tests for ``chameleon.codecs._url.parse_github_url``."""

from __future__ import annotations

import pytest

from chameleon.codecs._url import parse_github_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/name", ("owner", "name")),
        ("https://github.com/owner/name.git", ("owner", "name")),
        ("https://github.com/owner/name/", ("owner", "name")),
        ("https://github.com/owner/name.git/", ("owner", "name")),
        ("http://github.com/owner/name", ("owner", "name")),
        ("http://github.com/owner/name.git", ("owner", "name")),
        # SSH "scp-like" form
        ("git@github.com:owner/name", ("owner", "name")),
        ("git@github.com:owner/name.git", ("owner", "name")),
        # Hyphenated owner / name are common.
        (
            "https://github.com/example-org/example-marketplace.git",
            ("example-org", "example-marketplace"),
        ),
    ],
)
def test_parse_github_url_canonical_forms(url: str, expected: tuple[str, str]) -> None:
    assert parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # Custom SSH alias — chameleon can't safely rewrite these.
        "git@github-org:owner/name.git",
        "git@github-archivium:Archivium-Properties/archivium-marketplace.git",
        # Gist host is not the same as github.com.
        "https://gist.github.com/owner/abc123",
        # Sub-path beyond owner/name.
        "https://github.com/owner/name/tree/main",
        "https://github.com/owner/name/blob/main/README.md",
        # Wrong host.
        "https://gitlab.com/owner/name.git",
        "https://example.com/repo.git",
        # Missing owner or name.
        "https://github.com/",
        "https://github.com/owner",
        "https://github.com/owner/",
        # Empty.
        "",
        # SSH form with extra path.
        "git@github.com:owner/name/sub",
    ],
)
def test_parse_github_url_rejects_non_canonical(url: str) -> None:
    assert parse_github_url(url) is None
