"""P1-D — Claude directives codec must understand the in-the-wild commit
attribution aliases the operator's settings.json actually uses.

Aliases observed in the exemplar fixture (`tests/fixtures/exemplar/home/_claude/settings.json`):

* ``includeCoAuthoredBy`` — the only bool form in the official schemastore
  schema (marked DEPRECATED there, but still present and recognized).
* ``coauthoredBy`` — legacy/community shorter form, NOT in schemastore.
* ``gitAttribution`` — legacy/community form, NOT in schemastore.
* ``attribution.commit`` — modern structured form (string template;
  empty string means "hide commit attribution").

The neutral schema field ``directives.commit_attribution`` is a
``str | None``. Boolean aliases are mapped onto that string axis as:

* ``false`` -> ``""`` (empty string == "hide commit attribution",
  matching the documented semantics of ``attribution.commit``).
* ``true``  -> ``None`` (default; no override).

Disambiguation precedence when several aliases disagree:

1. ``attribution.commit`` (modern, structured) wins — it is the most
   expressive form and the only one that can carry a non-default
   template string.
2. Among the bool aliases, precedence is
   ``includeCoAuthoredBy`` > ``coauthoredBy`` > ``gitAttribution``.
   That ordering follows the schemastore schema: only the first is
   recognized upstream; the other two are documented community
   variants. A disagreement among bool aliases emits a ``LossWarning``
   and the first-in-precedence wins.

In ``to_target`` we write **only** ``attribution.commit`` — the modern
canonical form. We never re-emit the deprecated/community aliases;
re-emitting them would amplify the existing alias sprawl.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.claude.directives import (
    ClaudeAttribution,
    ClaudeDirectivesCodec,
    ClaudeDirectivesSection,
)
from chameleon.schema.directives import Directives


def _ctx() -> TranspileCtx:
    return TranspileCtx()


# -- single-alias reads ------------------------------------------------------


def test_include_co_authored_by_false_means_hide() -> None:
    section = ClaudeDirectivesSection.model_validate({"includeCoAuthoredBy": False})
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    assert neutral.commit_attribution == ""
    assert ctx.warnings == []


def test_coauthored_by_false_means_hide() -> None:
    section = ClaudeDirectivesSection.model_validate({"coauthoredBy": False})
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    assert neutral.commit_attribution == ""
    assert ctx.warnings == []


def test_git_attribution_false_means_hide() -> None:
    section = ClaudeDirectivesSection.model_validate({"gitAttribution": False})
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    assert neutral.commit_attribution == ""
    assert ctx.warnings == []


def test_attribution_commit_string_passes_through() -> None:
    section = ClaudeDirectivesSection.model_validate(
        {"attribution": {"commit": "Reviewed-by: ops"}},
    )
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    assert neutral.commit_attribution == "Reviewed-by: ops"
    assert ctx.warnings == []


def test_include_co_authored_by_true_is_default_none() -> None:
    """`true` means "include the byline" — that is the upstream default,
    so neutral records None (no override) rather than synthesizing a
    sentinel string."""
    section = ClaudeDirectivesSection.model_validate({"includeCoAuthoredBy": True})
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    assert neutral.commit_attribution is None
    assert ctx.warnings == []


# -- multi-alias agreement (no warning) --------------------------------------


def test_all_three_bool_aliases_agree_no_warning() -> None:
    section = ClaudeDirectivesSection.model_validate(
        {
            "includeCoAuthoredBy": False,
            "coauthoredBy": False,
            "gitAttribution": False,
        },
    )
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    assert neutral.commit_attribution == ""
    assert ctx.warnings == []


def test_attribution_commit_with_agreeing_bool_alias_no_warning() -> None:
    """Empty-string ``attribution.commit`` agrees with ``False`` bool aliases."""
    section = ClaudeDirectivesSection.model_validate(
        {
            "attribution": {"commit": ""},
            "includeCoAuthoredBy": False,
        },
    )
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    assert neutral.commit_attribution == ""
    assert ctx.warnings == []


# -- multi-alias disagreement (LossWarning, precedence wins) -----------------


def test_disagreeing_bool_aliases_emit_losswarning_first_wins() -> None:
    section = ClaudeDirectivesSection.model_validate(
        {
            "includeCoAuthoredBy": False,
            "coauthoredBy": True,
        },
    )
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    # includeCoAuthoredBy wins -> False -> ""
    assert neutral.commit_attribution == ""
    assert len(ctx.warnings) == 1
    assert "disagree" in ctx.warnings[0].message.lower()


def test_attribution_commit_overrides_disagreeing_bool_alias() -> None:
    """An explicit non-empty ``attribution.commit`` template wins over a
    contradicting bool alias and emits a warning."""
    section = ClaudeDirectivesSection.model_validate(
        {
            "attribution": {"commit": "Reviewed-by: ops"},
            "includeCoAuthoredBy": False,
        },
    )
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    # attribution.commit wins, non-empty.
    assert neutral.commit_attribution == "Reviewed-by: ops"
    assert len(ctx.warnings) == 1


def test_three_way_disagreement_uses_full_precedence_ladder() -> None:
    """``coauthoredBy`` > ``gitAttribution`` once ``includeCoAuthoredBy`` is absent."""
    section = ClaudeDirectivesSection.model_validate(
        {
            "coauthoredBy": False,
            "gitAttribution": True,
        },
    )
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    # coauthoredBy wins -> False -> ""
    assert neutral.commit_attribution == ""
    assert len(ctx.warnings) == 1


# -- to_target writes only the canonical alias -------------------------------


def test_to_target_writes_attribution_commit_only_for_hide() -> None:
    neutral = Directives(commit_attribution="")
    section = ClaudeDirectivesCodec.to_target(neutral, _ctx())
    dumped = section.model_dump(exclude_none=True, exclude_defaults=True, by_alias=True)
    # Canonical: write attribution.commit. Do NOT also write the
    # deprecated / community boolean aliases.
    assert "attribution" in dumped
    assert dumped["attribution"]["commit"] == ""
    assert "includeCoAuthoredBy" not in dumped
    assert "coauthoredBy" not in dumped
    assert "gitAttribution" not in dumped


def test_to_target_writes_attribution_commit_for_string_template() -> None:
    neutral = Directives(commit_attribution="Reviewed-by: ops")
    section = ClaudeDirectivesCodec.to_target(neutral, _ctx())
    dumped = section.model_dump(exclude_none=True, exclude_defaults=True, by_alias=True)
    assert dumped["attribution"]["commit"] == "Reviewed-by: ops"
    assert "includeCoAuthoredBy" not in dumped


def test_to_target_emits_nothing_when_neutral_is_none() -> None:
    neutral = Directives(commit_attribution=None)
    section = ClaudeDirectivesCodec.to_target(neutral, _ctx())
    dumped = section.model_dump(exclude_none=True, exclude_defaults=True, by_alias=True)
    assert "attribution" not in dumped
    assert "includeCoAuthoredBy" not in dumped


# -- backwards compatibility with the existing attribution.commit path ------


def test_attribution_object_still_round_trips() -> None:
    section_in = ClaudeDirectivesSection(attribution=ClaudeAttribution(commit="hello"))
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section_in, ctx)
    assert neutral.commit_attribution == "hello"
    section_out = ClaudeDirectivesCodec.to_target(neutral, ctx)
    assert section_out.attribution.commit == "hello"


def test_outputstyle_path_unchanged() -> None:
    section = ClaudeDirectivesSection.model_validate({"outputStyle": "concise"})
    neutral = ClaudeDirectivesCodec.from_target(section, _ctx())
    assert neutral.system_prompt_file == "concise"


# -- the exemplar's exact shape lands cleanly --------------------------------


def test_exemplar_settings_shape_resolves_to_hide() -> None:
    """The fixture has all three bool aliases set to False (agreeing).
    No warning, neutral records ``""``.
    """
    section = ClaudeDirectivesSection.model_validate(
        {
            "includeCoAuthoredBy": False,
            "coauthoredBy": False,
            "gitAttribution": False,
        },
    )
    ctx = _ctx()
    neutral = ClaudeDirectivesCodec.from_target(section, ctx)
    assert neutral.commit_attribution == ""
    assert ctx.warnings == []


# -- unknown keys still rejected (extra="forbid" preserved) ------------------


def test_unknown_key_still_rejected() -> None:
    """Modeling the known aliases should not loosen ``extra="forbid"``;
    truly unknown keys must still surface clearly so they can be added or
    routed to passthrough rather than silently swallowed.
    """
    with pytest.raises(ValidationError):
        ClaudeDirectivesSection.model_validate({"totallyMadeUpKey": True})
