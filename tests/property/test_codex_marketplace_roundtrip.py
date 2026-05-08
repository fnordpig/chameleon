"""Codex marketplace round-trip pinning tests.

 Agent B's FUZZ-3 cross-target fuzz suite surfaced three bugs in
the Codex marketplace codec. Each one corrupted operator-authored
configuration silently when transpiling through the Codex lane:

* **F-MP-G** — ``PluginMarketplaceSource(kind='github', repo='owner/name')``
  was rewritten to ``kind='git', url='https://github.com/owner/name.git'``
  on round-trip (``github`` and ``git`` are distinct neutral discriminator
  values; collapsing them is data loss).
* **F-MP-U** — ``PluginMarketplaceSource(kind='url')`` collapsed to
  ``kind='git'`` on round-trip and was non-idempotent (a second
  ``to_target`` would emit ``source_type='git'`` where the first emitted
  ``source_type=None``).
* **F-AU** — ``PluginMarketplace.auto_update`` was silently dropped on
  every Codex round-trip regardless of source kind.

The  F-MP fix preserves the neutral discriminator and
``auto_update`` flag through the Codex lane:

* ``auto_update`` is plumbed as a plain key on the marketplace entry —
  upstream Codex's ``MarketplaceConfig`` is ``extra='allow'`` and
  ignores it, but Chameleon recovers it on disassemble.
* ``kind='github'`` and ``kind='url'`` are tagged with
  ``chameleon_kind`` (and ``chameleon_repo`` for ``github``) so the
  decoder reconstructs the exact neutral shape rather than collapsing
  to ``kind='git'``.

These tests pin every (kind, auto_update) combination and lock the
``[marketplaces.<name>]`` TOML wire format for the F-MP-affected kinds.
"""

from __future__ import annotations

import pytest

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex.capabilities import (
    CodexCapabilitiesCodec,
    _codex_marketplace_from_neutral,
    _codex_marketplace_to_neutral,
    _CodexMarketplaceEntry,
)
from chameleon.schema.capabilities import (
    Capabilities,
    PluginMarketplace,
    PluginMarketplaceSource,
)

# ---------------------------------------------------------------------------
# F-MP-G — kind='github' round-trip
# ---------------------------------------------------------------------------


def test_github_kind_round_trips_through_codex_lane() -> None:
    """The Codex codec must NOT silently rewrite ``kind='github'`` → ``'git'``.

    Pre-fix: ``from_target(to_target(x))`` returned ``kind='git'``,
    ``url='https://github.com/owner/name.git'`` for any
    ``kind='github'`` input — a silent neutral-discriminator change.
    """

    orig = Capabilities(
        plugin_marketplaces={
            "example": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="github",
                    repo="example-org/example-marketplace",
                    ref="main",
                ),
            ),
        }
    )
    ctx = TranspileCtx()
    restored = CodexCapabilitiesCodec.from_target(CodexCapabilitiesCodec.to_target(orig, ctx), ctx)
    assert restored.plugin_marketplaces == orig.plugin_marketplaces


def test_github_kind_without_ref_round_trips() -> None:
    """``ref`` is optional on ``kind='github'`` — round-trip with ref=None."""

    orig = Capabilities(
        plugin_marketplaces={
            "example": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="github",
                    repo="example-org/example-marketplace",
                ),
            ),
        }
    )
    ctx = TranspileCtx()
    restored = CodexCapabilitiesCodec.from_target(CodexCapabilitiesCodec.to_target(orig, ctx), ctx)
    assert restored.plugin_marketplaces == orig.plugin_marketplaces


# ---------------------------------------------------------------------------
# F-MP-U — kind='url' round-trip and idempotence
# ---------------------------------------------------------------------------


def test_url_kind_round_trips_through_codex_lane() -> None:
    """``kind='url'`` must NOT collapse to ``kind='git'`` on round-trip."""

    orig = Capabilities(
        plugin_marketplaces={
            "raw-url": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="url",
                    url="https://example.com/marketplace.json",
                ),
            ),
        }
    )
    ctx = TranspileCtx()
    restored = CodexCapabilitiesCodec.from_target(CodexCapabilitiesCodec.to_target(orig, ctx), ctx)
    assert restored.plugin_marketplaces == orig.plugin_marketplaces


def test_url_kind_codex_encoder_is_idempotent() -> None:
    """Pre-fix, two consecutive ``to_target`` calls produced different
    ``source_type`` values for ``kind='url'`` (``None`` then ``'git'``).
    Post-fix the section must be byte-stable across repeated encodes.
    """

    orig = Capabilities(
        plugin_marketplaces={
            "raw-url": PluginMarketplace(
                source=PluginMarketplaceSource(
                    kind="url",
                    url="https://example.com/marketplace.json",
                ),
            ),
        }
    )
    ctx = TranspileCtx()
    section1 = CodexCapabilitiesCodec.to_target(orig, ctx)
    decoded = CodexCapabilitiesCodec.from_target(section1, ctx)
    section2 = CodexCapabilitiesCodec.to_target(decoded, ctx)
    assert section1.marketplaces == section2.marketplaces


# ---------------------------------------------------------------------------
# auto_update round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("auto_update_value", [True, False, None])
@pytest.mark.parametrize(
    "source",
    [
        PluginMarketplaceSource(kind="github", repo="example-org/example-marketplace", ref="main"),
        PluginMarketplaceSource(kind="git", url="https://example.com/repo.git", ref=None),
        PluginMarketplaceSource(kind="url", url="https://example.com/marketplace.json"),
        PluginMarketplaceSource(kind="local", path="/srv/vendored-marketplace"),
    ],
    ids=["github", "git", "url", "local"],
)
def test_auto_update_round_trips_for_every_source_kind(
    source: PluginMarketplaceSource, auto_update_value: bool | None
) -> None:
    """Pre-fix, ``auto_update`` was silently dropped to ``None`` on every
    Codex round-trip regardless of source kind. The fix plumbs the bit
    through every kind."""

    orig = Capabilities(
        plugin_marketplaces={
            "example": PluginMarketplace(source=source, auto_update=auto_update_value),
        }
    )
    ctx = TranspileCtx()
    restored = CodexCapabilitiesCodec.from_target(CodexCapabilitiesCodec.to_target(orig, ctx), ctx)
    assert restored.plugin_marketplaces["example"].auto_update == auto_update_value, (
        f"auto_update={auto_update_value!r} dropped for kind={source.kind!r}"
    )
    assert restored.plugin_marketplaces == orig.plugin_marketplaces


# ---------------------------------------------------------------------------
# Wire-format pinning — lock the [marketplaces.<name>] TOML shape so a
# future refactor doesn't regress the round-trip.
# ---------------------------------------------------------------------------


def test_github_kind_emits_chameleon_kind_and_repo_hints() -> None:
    """Pin the wire format: ``kind='github'`` writes ``chameleon_kind='github'``
    and ``chameleon_repo='owner/name'`` alongside the synthesized URL."""

    ctx = TranspileCtx()
    entry = _codex_marketplace_from_neutral(
        "example",
        PluginMarketplace(
            source=PluginMarketplaceSource(
                kind="github", repo="example-org/example-marketplace", ref="main"
            ),
        ),
        ctx,
    )
    assert entry.source == "https://github.com/example-org/example-marketplace.git"
    assert entry.source_type == "git"
    assert entry.ref == "main"
    assert entry.chameleon_kind == "github"
    assert entry.chameleon_repo == "example-org/example-marketplace"


def test_url_kind_emits_chameleon_kind_hint_and_no_source_type() -> None:
    """Pin: ``kind='url'`` writes ``source_type=None`` and
    ``chameleon_kind='url'``. The latter is the F-MP-U recovery key."""

    ctx = TranspileCtx()
    entry = _codex_marketplace_from_neutral(
        "raw-url",
        PluginMarketplace(
            source=PluginMarketplaceSource(kind="url", url="https://example.com/marketplace.json"),
        ),
        ctx,
    )
    assert entry.source == "https://example.com/marketplace.json"
    assert entry.source_type is None
    assert entry.chameleon_kind == "url"
    assert entry.chameleon_repo is None


def test_git_kind_emits_no_chameleon_hints() -> None:
    """Pin: a plain ``kind='git'`` round-trips through ``source_type='git'``
    alone with NO chameleon-namespaced hints. Operators authoring
    ``[marketplaces.x] source_type='git'`` by hand should still
    decode to ``kind='git'``."""

    ctx = TranspileCtx()
    entry = _codex_marketplace_from_neutral(
        "example",
        PluginMarketplace(
            source=PluginMarketplaceSource(
                kind="git", url="https://example.com/repo.git", ref="main"
            ),
        ),
        ctx,
    )
    assert entry.source == "https://example.com/repo.git"
    assert entry.source_type == "git"
    assert entry.ref == "main"
    assert entry.chameleon_kind is None
    assert entry.chameleon_repo is None


def test_local_kind_emits_no_chameleon_hints() -> None:
    """Pin: ``kind='local'`` round-trips through ``source_type='local'``
    alone — this kind has always been Codex-native and never needed a
    hint."""

    ctx = TranspileCtx()
    entry = _codex_marketplace_from_neutral(
        "vendored",
        PluginMarketplace(
            source=PluginMarketplaceSource(kind="local", path="/srv/vendored-marketplace"),
        ),
        ctx,
    )
    assert entry.source == "/srv/vendored-marketplace"
    assert entry.source_type == "local"
    assert entry.chameleon_kind is None


# ---------------------------------------------------------------------------
# Hand-authored TOML compatibility — operators writing the canonical
# Codex shape (no chameleon_* hints) must still decode to a valid neutral.
# ---------------------------------------------------------------------------


def test_hand_authored_git_entry_decodes_to_kind_git() -> None:
    """A ``[marketplaces.x]`` table written by hand (no chameleon hints)
    decodes to ``kind='git'``. This is the documented default — only the
    chameleon-tagged hints unlock ``github`` / ``url``."""

    entry = _CodexMarketplaceEntry(
        source="https://example.com/repo.git", source_type="git", ref="main"
    )
    ctx = TranspileCtx()
    neutral = _codex_marketplace_to_neutral("example", entry, ctx)
    assert neutral is not None
    assert neutral.source.kind == "git"
    assert neutral.source.url == "https://example.com/repo.git"
    assert neutral.source.ref == "main"
    assert neutral.auto_update is None


def test_hand_authored_github_https_url_canonicalizes_to_kind_github() -> None:
    """A hand-authored Codex marketplace whose ``source`` is a canonical
    ``https://github.com/<owner>/<name>`` URL canonicalizes to
    ``kind='github'`` on disassemble — neutral always holds the
    higher-detail form (the user's design instruction: "highest detail
    in neutral").

    Pre-fix: the codec's ``else`` branch defaulted any ``source_type='git'``
    or unset entry to ``kind='git'`` even when the URL was a github repo,
    leaving cross-target merge to choose between Claude's ``kind='github'``
    and Codex's ``kind='git'`` for the same operator intent.
    """

    entry = _CodexMarketplaceEntry(
        source="https://github.com/example-org/example.git",
        source_type="git",
        ref="main",
    )
    ctx = TranspileCtx()
    neutral = _codex_marketplace_to_neutral("example", entry, ctx)
    assert neutral is not None
    assert neutral.source.kind == "github"
    assert neutral.source.repo == "example-org/example"
    assert neutral.source.url is None
    assert neutral.source.ref == "main"


def test_hand_authored_github_ssh_url_canonicalizes_to_kind_github() -> None:
    """SSH ``git@github.com:owner/name.git`` form canonicalizes too —
    same canonical owner/name, different transport."""

    entry = _CodexMarketplaceEntry(
        source="git@github.com:example-org/example.git",
        source_type="git",
    )
    ctx = TranspileCtx()
    neutral = _codex_marketplace_to_neutral("example", entry, ctx)
    assert neutral is not None
    assert neutral.source.kind == "github"
    assert neutral.source.repo == "example-org/example"


def test_hand_authored_custom_ssh_alias_stays_kind_git() -> None:
    """``git@github-org:...`` is a custom SSH alias, not literally
    ``github.com`` — chameleon can't rewrite it without changing auth
    behaviour, so it stays ``kind='git'``."""

    entry = _CodexMarketplaceEntry(
        source="git@github-org:example-org/example.git",
        source_type="git",
    )
    ctx = TranspileCtx()
    neutral = _codex_marketplace_to_neutral("example", entry, ctx)
    assert neutral is not None
    assert neutral.source.kind == "git"
    assert neutral.source.url == "git@github-org:example-org/example.git"


def test_hand_authored_local_entry_decodes_to_kind_local() -> None:
    entry = _CodexMarketplaceEntry(source="/srv/vendored", source_type="local")
    ctx = TranspileCtx()
    neutral = _codex_marketplace_to_neutral("vendored", entry, ctx)
    assert neutral is not None
    assert neutral.source.kind == "local"
    assert neutral.source.path == "/srv/vendored"


def test_chameleon_hints_recover_neutral_kind_github() -> None:
    """The chameleon-namespaced hints are the F-MP-G recovery channel.
    A TOML table carrying ``chameleon_kind='github'`` and
    ``chameleon_repo='o/n'`` decodes to ``kind='github'``."""

    entry = _CodexMarketplaceEntry(
        source="https://github.com/example-org/example.git",
        source_type="git",
        chameleon_kind="github",
        chameleon_repo="example-org/example",
    )
    ctx = TranspileCtx()
    neutral = _codex_marketplace_to_neutral("example", entry, ctx)
    assert neutral is not None
    assert neutral.source.kind == "github"
    assert neutral.source.repo == "example-org/example"


def test_chameleon_hints_recover_neutral_kind_url() -> None:
    """The F-MP-U recovery channel — ``chameleon_kind='url'`` decodes
    to ``kind='url'``."""

    entry = _CodexMarketplaceEntry(
        source="https://example.com/marketplace.json",
        source_type=None,
        chameleon_kind="url",
    )
    ctx = TranspileCtx()
    neutral = _codex_marketplace_to_neutral("raw-url", entry, ctx)
    assert neutral is not None
    assert neutral.source.kind == "url"
    assert neutral.source.url == "https://example.com/marketplace.json"
