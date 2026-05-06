"""Claude codec for the directives domain.

V0 covered ``commit_attribution`` (via ``attribution.commit``) and
``system_prompt_file`` (via ``outputStyle``) only. P1-D extends the
codec to recognize the in-the-wild boolean aliases for "should the
commit body include a co-authored-by trailer?" that real
``settings.json`` files use:

* ``includeCoAuthoredBy`` — the only bool form present in the official
  schemastore-derived schema (``Attribution`` model marks it
  DEPRECATED in favor of the ``attribution`` object, but it is still
  recognized upstream).
* ``coauthoredBy`` — legacy/community shorter form. Not in the
  schemastore schema.
* ``gitAttribution`` — legacy/community form. Not in the schemastore
  schema either.

The neutral schema's ``directives.commit_attribution`` is a
``str | None``: a template string for the commit attribution line, with
the documented convention (mirroring ``Attribution.commit``) that an
empty string means "hide". We map the boolean aliases onto that
string axis as ``False -> ""`` and ``True -> None`` (default; no
override needed).

Disambiguation precedence when several aliases are present and disagree:

1. ``attribution.commit`` (the modern, structured, expressive form)
   wins outright.
2. Among the boolean aliases, the precedence is
   ``includeCoAuthoredBy`` > ``coauthoredBy`` > ``gitAttribution``.
   That ordering matches the schemastore: only the first is recognized
   upstream; the other two are community variants documented by
   parity-gap analysis.

When two aliases disagree we emit a ``LossWarning`` describing the
disagreement and how it was resolved, rather than silently dropping
information.

When emitting back out (``to_target``), we write a single canonical
form: ``attribution.commit``. We deliberately do NOT re-emit the
deprecated/community boolean aliases — re-emitting all three would
amplify the alias sprawl that motivated this codec.

Note on ``claimed_paths``: only the upstream-recognized aliases
(``attribution.commit``, ``attribution.pr``, ``includeCoAuthoredBy``)
appear in ``claimed_paths``, because ``claimed_paths`` is validated
against the upstream-derived ``ClaudeSettings`` model. The
non-schemastore aliases (``coauthoredBy``, ``gitAttribution``) are
absorbed at the section level for tolerance — they cannot be claimed
through a model that doesn't define them.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chameleon._types import FieldPath, TargetId
from chameleon.codecs._protocol import LossWarning, TranspileCtx
from chameleon.schema._constants import BUILTIN_CLAUDE, Domains
from chameleon.schema.directives import Directives


class ClaudeAttribution(BaseModel):
    model_config = ConfigDict(extra="allow")
    commit: str | None = None
    pr: str | None = None


class ClaudeDirectivesSection(BaseModel):
    """Section view of Claude's directives-domain keys.

    ``extra="forbid"`` is preserved so that genuinely unknown keys
    surface as validation errors and get routed to passthrough rather
    than being silently swallowed. All known commit-attribution
    aliases are modelled explicitly below.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    outputStyle: str | None = None  # noqa: N815
    attribution: ClaudeAttribution = Field(default_factory=ClaudeAttribution)

    # Boolean aliases. All three are mapped onto neutral
    # ``commit_attribution`` (a ``str | None``) where ``False -> ""``
    # and ``True -> None``.
    include_co_authored_by: bool | None = Field(default=None, alias="includeCoAuthoredBy")
    coauthored_by: bool | None = Field(default=None, alias="coauthoredBy")
    git_attribution: bool | None = Field(default=None, alias="gitAttribution")


def _resolve_commit_attribution(
    section: ClaudeDirectivesSection,
    ctx: TranspileCtx,
) -> str | None:
    """Apply the precedence ladder described in the module docstring.

    Returns the resolved neutral value (``str | None``) and emits a
    ``LossWarning`` on the context if competing aliases disagree.
    """
    # Modern structured form wins outright when present.
    explicit_commit = section.attribution.commit
    bool_aliases: list[tuple[str, bool | None]] = [
        ("includeCoAuthoredBy", section.include_co_authored_by),
        ("coauthoredBy", section.coauthored_by),
        ("gitAttribution", section.git_attribution),
    ]
    present_bools = [(name, val) for name, val in bool_aliases if val is not None]

    def _bool_to_neutral(b: bool) -> str | None:
        return "" if b is False else None

    # Detect bool disagreements (ignore None entries).
    bool_values = {val for _, val in present_bools}
    bools_disagree = len(bool_values) > 1

    if explicit_commit is not None:
        # If a bool alias contradicts the explicit template, warn.
        contradicts = False
        for _, b in present_bools:
            neutral_from_bool = _bool_to_neutral(b)
            if neutral_from_bool != explicit_commit:
                contradicts = True
                break
        if contradicts:
            ctx.warn(
                LossWarning(
                    domain=Domains.DIRECTIVES,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "claude commit attribution: attribution.commit and one or "
                        "more boolean aliases disagree; preferring the modern "
                        "attribution.commit value and discarding the bool aliases."
                    ),
                ),
            )
        return explicit_commit

    # No structured form. Walk the bool precedence ladder.
    if not present_bools:
        return None
    chosen_name, chosen_val = present_bools[0]
    if bools_disagree:
        ctx.warn(
            LossWarning(
                domain=Domains.DIRECTIVES,
                target=BUILTIN_CLAUDE,
                message=(
                    f"claude commit attribution: boolean aliases disagree "
                    f"({', '.join(f'{n}={v}' for n, v in present_bools)}); "
                    f"resolving via precedence "
                    f"includeCoAuthoredBy > coauthoredBy > gitAttribution -> "
                    f"using {chosen_name}={chosen_val}."
                ),
            ),
        )
    return _bool_to_neutral(chosen_val)


class ClaudeDirectivesCodec:
    target: ClassVar[TargetId] = BUILTIN_CLAUDE
    domain: ClassVar[Domains] = Domains.DIRECTIVES
    target_section: ClassVar[type[BaseModel]] = ClaudeDirectivesSection
    # Only upstream-schema-recognized paths are claimed. The legacy
    # ``coauthoredBy`` / ``gitAttribution`` aliases are not in the
    # schemastore-derived ``ClaudeSettings`` model and so cannot be
    # validated by the schema-drift gate; they are still honored by
    # the section model for input tolerance.
    claimed_paths: ClassVar[frozenset[FieldPath]] = frozenset(
        {
            FieldPath(segments=("outputStyle",)),
            FieldPath(segments=("attribution", "commit")),
            FieldPath(segments=("attribution", "pr")),
            FieldPath(segments=("includeCoAuthoredBy",)),
        },
    )

    @staticmethod
    def to_target(model: Directives, ctx: TranspileCtx) -> ClaudeDirectivesSection:
        section = ClaudeDirectivesSection()
        if model.system_prompt_file is not None:
            section.outputStyle = model.system_prompt_file
        if model.commit_attribution is not None:
            # Always write the canonical modern form. Empty string is
            # the documented "hide" sentinel; non-empty strings are
            # commit-trailer templates. We never re-emit the
            # deprecated/community bool aliases.
            section.attribution = ClaudeAttribution(commit=model.commit_attribution)
        if model.personality is not None:
            # P1-E — Claude has no personality concept. Drop the value
            # rather than guess a Claude-side approximation, but surface
            # the loss as a typed warning so the operator (or a higher
            # layer) can see what was discarded and why.
            ctx.warn(
                LossWarning(
                    domain=Domains.DIRECTIVES,
                    target=BUILTIN_CLAUDE,
                    message=(
                        "P1-E: directives.personality "
                        f"({model.personality.value!r}) has no Claude equivalent; "
                        "dropping during to_target. The value is preserved in "
                        "neutral and will continue to round-trip through the "
                        "Codex codec."
                    ),
                    field_path=FieldPath(segments=("personality",)),
                ),
            )
        return section

    @staticmethod
    def from_target(section: ClaudeDirectivesSection, ctx: TranspileCtx) -> Directives:
        return Directives(
            system_prompt_file=section.outputStyle,
            commit_attribution=_resolve_commit_attribution(section, ctx),
        )


__all__ = [
    "ClaudeAttribution",
    "ClaudeDirectivesCodec",
    "ClaudeDirectivesSection",
]
