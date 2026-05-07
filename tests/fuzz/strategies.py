"""Custom Hypothesis strategies for Chameleon's neutral schema (Wave-F1).

This module is the boilerplate-eliminator for Wave-F2's fuzz tests. It
does three things:

1. Registers a ``TargetId`` strategy that draws from the *registered*
   target set — without this, ``st.from_type(TargetId)`` produces
   arbitrary text and almost every example fails ``TargetId``'s
   registry validator. Field annotations like
   ``dict[TargetId, str]`` then propagate the fix automatically.

2. Registers explicit ``st.builds(...)`` strategies for every
   neutral submodel that uses ``default_factory`` (Pydantic's
   default-factory sentinel does NOT survive Hypothesis's auto-derived
   ``st.from_type`` — confirmed empirically against Hypothesis 6.x).
   Without these registrations, ``from_type(Identity)`` etc. produce
   ``ValidationError`` on every example because the ``<factory>``
   sentinel leaks through as a literal value.

3. Provides composite strategies for adversarial inputs that Wave-F2
   tests will compose into round-trip and differential tests:

   * :func:`unicode_torture` — text spanning BMP, SMP, RTL,
     combining marks, and variation selectors (surrogates excluded).
   * :func:`extra_keys_at_random_depth` — splices unmodelled JSON
     keys into a random Pydantic-model descent point so we can
     verify ``extra="forbid"`` actually rejects them.
   * :func:`partial_neutral_with_holes` — emits a ``Neutral`` with
     a random subset of fields populated to simulate operator-
     authored partial state.

4. Exposes :func:`cross_target_shared_paths` — the manually-curated
   list of ``FieldPath``\\s where Claude and Codex are expected to
   carry the same neutral value. Wave-F2's cross-target differential
   test will iterate this list.

The discipline is: Wave-F2 tests use ``@given(model=...)`` and rely on
the registrations here, with per-test overrides only for adversarial
slices the auto-strategy cannot express.
"""

from __future__ import annotations

import re
from typing import Any

from hypothesis import strategies as st
from pydantic import AnyHttpUrl, BaseModel

from chameleon._types import FieldPath, JsonValue, TargetId
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.authorization import (
    Authorization,
    DefaultMode,
    FilesystemPolicy,
    NetworkPolicy,
    Reviewer,
)
from chameleon.schema.capabilities import (
    Capabilities,
    McpServerStdio,
    McpServerStreamableHttp,
    PluginEntry,
    PluginMarketplace,
    PluginMarketplaceSource,
)
from chameleon.schema.directives import Directives, Personality, Verbosity
from chameleon.schema.environment import Environment, InheritPolicy
from chameleon.schema.governance import Governance, Trust, Updates, UpdatesChannel
from chameleon.schema.identity import (
    AuthMethod,
    Identity,
    IdentityAuth,
    IdentityEndpoint,
    ReasoningEffort,
)
from chameleon.schema.interface import Interface, Voice, VoiceMode
from chameleon.schema.lifecycle import (
    History,
    HistoryPersistence,
    HookCommandShell,
    HookMatcher,
    Hooks,
    Lifecycle,
    Telemetry,
    TelemetryExporter,
)
from chameleon.schema.neutral import Neutral
from chameleon.schema.passthrough import PassThroughBag
from chameleon.schema.profiles import Profile

# ----------------------------------------------------------------------
# Bounds chosen to keep examples cheap. Per the task spec: dicts/lists
# capped at 8 entries, strings capped at 200 chars. These are *defaults*;
# Wave-F2 tests can pass `max_size` overrides into per-test strategies
# when a specific shape needs more.
# ----------------------------------------------------------------------

MAX_COLLECTION_SIZE: int = 8
MAX_STRING_SIZE: int = 200

# ----------------------------------------------------------------------
# JsonValue — the recursive any-JSON value used by PassThroughBag.items
# and the extra-keys composite. Hypothesis's `recursive` strategy is the
# right tool here; `deferred` would also work but `recursive` gives us
# explicit control over leaves vs. extension.
# ----------------------------------------------------------------------

_json_leaf: st.SearchStrategy[JsonValue] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31 - 1),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=MAX_STRING_SIZE),
)

json_value: st.SearchStrategy[JsonValue] = st.recursive(
    _json_leaf,
    lambda children: st.one_of(
        st.lists(children, max_size=MAX_COLLECTION_SIZE),
        st.dictionaries(
            keys=st.text(max_size=MAX_STRING_SIZE),
            values=children,
            max_size=MAX_COLLECTION_SIZE,
        ),
    ),
    max_leaves=16,
)


# ----------------------------------------------------------------------
# Unicode torture — text strategy across BMP, SMP, RTL, combining marks,
# variation selectors. Surrogate codepoints (U+D800..U+DFFF) excluded
# because they are illegal in well-formed Unicode strings and only
# arise from buggy decoders. We intentionally include U+0000 through
# U+10FFFF apart from that gap.
# ----------------------------------------------------------------------

# RTL block: U+0590-U+08FF covers Hebrew, Arabic, Syriac, Arabic
# Supplement, Thaana, NKo, Samaritan, Mandaic, Arabic Extended-A.
_rtl: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(
        min_codepoint=0x0590,
        max_codepoint=0x08FF,
        # Use blacklist_categories rather than whitelist so structurally
        # invalid characters in the range still get filtered.
        blacklist_categories=("Cs",),
    ),
    max_size=MAX_STRING_SIZE,
)

# Combining marks: U+0300-U+036F (Combining Diacritical Marks) plus
# U+1AB0-U+1AFF and U+1DC0-U+1DFF (the supplements). We include them
# layered onto a base character via concatenation in `unicode_torture`.
_combining: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(
        whitelist_categories=("Mn", "Mc", "Me"),
    ),
    max_size=8,
)

# Variation selectors: U+FE00-U+FE0F (text/emoji presentation toggles).
_variation: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(min_codepoint=0xFE00, max_codepoint=0xFE0F),
    max_size=4,
)

# SMP / astral plane: emoji and historic scripts. Cap to widely-supported
# blocks rather than the full SMP because some embedded systems blow up
# on poorly-allocated codepoints in private-use planes.
_smp: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(
        min_codepoint=0x10000,
        max_codepoint=0x1FFFF,
        blacklist_categories=("Cs", "Cn"),
    ),
    max_size=MAX_STRING_SIZE // 2,
)

# Plain BMP minus surrogates and the ASCII control range that breaks
# many YAML/JSON serializers. This is the workhorse leaf for Wave-F2.
_bmp: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(
        min_codepoint=0x0020,
        max_codepoint=0xFFFF,
        blacklist_categories=("Cs",),
    ),
    max_size=MAX_STRING_SIZE,
)


@st.composite
def unicode_torture(draw: st.DrawFn) -> str:
    """Adversarial unicode generator covering BMP, SMP, RTL, combining
    marks, and variation selectors.

    Each draw concatenates one or more chunks from the per-block
    sub-strategies. The result is always well-formed (no lone
    surrogates) but is intentionally hostile to naive normalisation,
    width assumption, and grapheme-cluster handling.
    """
    parts: list[str] = []
    n = draw(st.integers(min_value=1, max_value=4))
    for _ in range(n):
        choice = draw(
            st.sampled_from(
                ["bmp", "smp", "rtl", "combining", "variation"],
            ),
        )
        if choice == "bmp":
            parts.append(draw(_bmp))
        elif choice == "smp":
            parts.append(draw(_smp))
        elif choice == "rtl":
            parts.append(draw(_rtl))
        elif choice == "combining":
            # Combining marks are meaningful only after a base char.
            base = draw(st.characters(min_codepoint=0x0041, max_codepoint=0x007A))
            parts.append(base + draw(_combining))
        else:  # variation
            base = draw(st.characters(min_codepoint=0x2600, max_codepoint=0x26FF))
            parts.append(base + draw(_variation))
    out = "".join(parts)
    return out[:MAX_STRING_SIZE]


# ----------------------------------------------------------------------
# Identifier-shaped tokens — used for FieldPath segments. Must match
# Pydantic field-name conventions: starts with letter or underscore,
# followed by letters / digits / underscores.
# ----------------------------------------------------------------------

_IDENT_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_identifier: st.SearchStrategy[str] = st.from_regex(
    r"\A[A-Za-z_][A-Za-z0-9_]{0,30}\Z",
    fullmatch=True,
)


@st.composite
def field_paths(draw: st.DrawFn, min_depth: int = 1, max_depth: int = 4) -> FieldPath:
    """Well-formed :class:`FieldPath` of identifier-shaped segments."""
    depth = draw(st.integers(min_value=min_depth, max_value=max_depth))
    segments = tuple(draw(_identifier) for _ in range(depth))
    return FieldPath(segments=segments)


# ----------------------------------------------------------------------
# TargetId — register against the *registered* targets only. Without
# this, Hypothesis generates arbitrary text and `_must_be_registered`
# rejects ~100% of examples.
# ----------------------------------------------------------------------

target_ids: st.SearchStrategy[TargetId] = st.sampled_from([BUILTIN_CLAUDE, BUILTIN_CODEX])

st.register_type_strategy(TargetId, target_ids)

# ----------------------------------------------------------------------
# Pydantic URL — Hypothesis cannot synthesize `pydantic_core.Url` on its
# own. Build via a constrained text -> validate-through-AnyHttpUrl
# pipeline. Both AnyHttpUrl and the Codex-generated Url subclass route
# through the same constructor, so a single strategy covers both
# registrations.
# ----------------------------------------------------------------------

# Port is constrained as a separate strategy (1..65535) and concatenated
# at build time — putting the port range entirely in the regex would
# force `from_regex` to enumerate hundreds of overlapping branches.
_url_host: st.SearchStrategy[str] = st.from_regex(
    r"\A[a-z][a-z0-9-]{0,15}\.example\Z",
    fullmatch=True,
)
_url_path: st.SearchStrategy[str] = st.from_regex(
    r"\A(/[a-z0-9_-]{1,15}){0,3}\Z",
    fullmatch=True,
)
_url_port: st.SearchStrategy[int | None] = st.one_of(
    st.none(), st.integers(min_value=1, max_value=65535)
)
_url_scheme: st.SearchStrategy[str] = st.sampled_from(["http", "https"])


@st.composite
def _draw_url(draw: st.DrawFn) -> AnyHttpUrl:
    scheme = draw(_url_scheme)
    host = draw(_url_host)
    port = draw(_url_port)
    path = draw(_url_path)
    port_part = f":{port}" if port is not None else ""
    return AnyHttpUrl(f"{scheme}://{host}{port_part}{path}")


http_urls: st.SearchStrategy[AnyHttpUrl] = _draw_url()

st.register_type_strategy(AnyHttpUrl, http_urls)

# ----------------------------------------------------------------------
# Helpers for capped collections of typed values. Hypothesis's defaults
# allow unbounded lists/dicts which inflate example shrinking time and
# cause sporadic deadline-exceeded notes. The cap is the same as the
# task spec's MAX_COLLECTION_SIZE.
# ----------------------------------------------------------------------


def _cap_dict[K, V](
    keys: st.SearchStrategy[K],
    values: st.SearchStrategy[V],
) -> st.SearchStrategy[dict[K, V]]:
    return st.dictionaries(keys=keys, values=values, max_size=MAX_COLLECTION_SIZE)


def _cap_list[V](values: st.SearchStrategy[V]) -> st.SearchStrategy[list[V]]:
    return st.lists(values, max_size=MAX_COLLECTION_SIZE)


def _short_text() -> st.SearchStrategy[str]:
    return st.text(max_size=MAX_STRING_SIZE)


def _opt[T](strat: st.SearchStrategy[T]) -> st.SearchStrategy[T | None]:
    return st.one_of(st.none(), strat)


# ----------------------------------------------------------------------
# Identity domain
# ----------------------------------------------------------------------

identity_endpoints: st.SearchStrategy[IdentityEndpoint] = st.builds(
    IdentityEndpoint,
    base_url=_opt(_cap_dict(target_ids, _short_text())),
)

identity_auth: st.SearchStrategy[IdentityAuth] = st.builds(
    IdentityAuth,
    method=_opt(st.sampled_from(list(AuthMethod))),
    api_key_helper=_opt(_short_text()),
)

identities: st.SearchStrategy[Identity] = st.builds(
    Identity,
    reasoning_effort=_opt(st.sampled_from(list(ReasoningEffort))),
    thinking=_opt(st.booleans()),
    service_tier=_opt(_short_text()),
    context_window=_opt(st.integers(min_value=1, max_value=2**20)),
    compact_threshold=_opt(st.integers(min_value=1, max_value=2**20)),
    model_catalog_path=_opt(_short_text()),
    model=_opt(_cap_dict(target_ids, _short_text())),
    endpoint=identity_endpoints,
    auth=identity_auth,
)

st.register_type_strategy(IdentityEndpoint, identity_endpoints)
st.register_type_strategy(IdentityAuth, identity_auth)
st.register_type_strategy(Identity, identities)

# ----------------------------------------------------------------------
# Directives domain (no default_factory submodels — auto would work,
# but registering explicitly bounds string sizes for cheaper examples).
# ----------------------------------------------------------------------

directives: st.SearchStrategy[Directives] = st.builds(
    Directives,
    system_prompt_file=_opt(_short_text()),
    commit_attribution=_opt(_short_text()),
    output_style=_opt(_short_text()),
    language=_opt(_short_text()),
    personality=_opt(st.sampled_from(list(Personality))),
    verbosity=_opt(st.sampled_from(list(Verbosity))),
    show_thinking_summary=_opt(st.booleans()),
)

st.register_type_strategy(Directives, directives)

# ----------------------------------------------------------------------
# Capabilities domain
# ----------------------------------------------------------------------

mcp_stdio: st.SearchStrategy[McpServerStdio] = st.builds(
    McpServerStdio,
    command=_short_text(),
    args=_cap_list(_short_text()),
    env=_cap_dict(_short_text(), _short_text()),
    cwd=_opt(_short_text()),
)

mcp_http: st.SearchStrategy[McpServerStreamableHttp] = st.builds(
    McpServerStreamableHttp,
    url=http_urls,
    bearer_token_env_var=_opt(_short_text()),
    http_headers=_cap_dict(_short_text(), _short_text()),
)

mcp_servers: st.SearchStrategy[McpServerStdio | McpServerStreamableHttp] = st.one_of(
    mcp_stdio, mcp_http
)

plugin_entries: st.SearchStrategy[PluginEntry] = st.builds(PluginEntry, enabled=st.booleans())

# Plugin keys obey the canonical `<plugin>@<marketplace>` shape; codecs
# parse them, so the strategy must respect that or every example fails.
_plugin_key: st.SearchStrategy[str] = st.from_regex(
    r"\A[a-z][a-z0-9-]{0,15}@[a-z][a-z0-9-]{0,30}\Z",
    fullmatch=True,
)

plugin_marketplace_sources: st.SearchStrategy[PluginMarketplaceSource] = st.one_of(
    st.builds(
        PluginMarketplaceSource,
        kind=st.just("github"),
        repo=st.from_regex(r"\A[a-z][a-z0-9-]{0,15}/[a-z][a-z0-9-]{0,15}\Z", fullmatch=True),
        url=st.none(),
        path=st.none(),
        ref=_opt(_short_text()),
    ),
    st.builds(
        PluginMarketplaceSource,
        kind=st.just("git"),
        repo=st.none(),
        url=_short_text(),
        path=st.none(),
        ref=_opt(_short_text()),
    ),
    st.builds(
        PluginMarketplaceSource,
        kind=st.just("url"),
        repo=st.none(),
        url=_short_text(),
        path=st.none(),
        ref=st.none(),
    ),
    st.builds(
        PluginMarketplaceSource,
        kind=st.just("local"),
        repo=st.none(),
        url=st.none(),
        path=_short_text(),
        ref=st.none(),
    ),
)

plugin_marketplaces: st.SearchStrategy[PluginMarketplace] = st.builds(
    PluginMarketplace,
    source=plugin_marketplace_sources,
    auto_update=_opt(st.booleans()),
)

capabilities: st.SearchStrategy[Capabilities] = st.builds(
    Capabilities,
    mcp_servers=_cap_dict(_short_text(), mcp_servers),
    plugins=_cap_dict(_plugin_key, plugin_entries),
    plugin_marketplaces=_cap_dict(_short_text(), plugin_marketplaces),
    skills=_cap_list(_short_text()),
    subagents=_cap_dict(_short_text(), _short_text()),
    web_search=_opt(st.sampled_from(["cached", "live", "disabled"])),
)

st.register_type_strategy(McpServerStdio, mcp_stdio)
st.register_type_strategy(McpServerStreamableHttp, mcp_http)
st.register_type_strategy(PluginEntry, plugin_entries)
st.register_type_strategy(PluginMarketplaceSource, plugin_marketplace_sources)
st.register_type_strategy(PluginMarketplace, plugin_marketplaces)
st.register_type_strategy(Capabilities, capabilities)

# ----------------------------------------------------------------------
# Authorization domain
# ----------------------------------------------------------------------

filesystem_policies: st.SearchStrategy[FilesystemPolicy] = st.builds(
    FilesystemPolicy,
    allow_read=_cap_list(_short_text()),
    allow_write=_cap_list(_short_text()),
    deny_read=_cap_list(_short_text()),
    deny_write=_cap_list(_short_text()),
)

network_policies: st.SearchStrategy[NetworkPolicy] = st.builds(
    NetworkPolicy,
    allowed_domains=_cap_list(_short_text()),
    denied_domains=_cap_list(_short_text()),
    allow_local_binding=_opt(st.booleans()),
    allow_unix_sockets=_cap_list(_short_text()),
)

authorizations: st.SearchStrategy[Authorization] = st.builds(
    Authorization,
    default_mode=_opt(st.sampled_from(list(DefaultMode))),
    filesystem=filesystem_policies,
    network=network_policies,
    allow_patterns=_cap_list(_short_text()),
    ask_patterns=_cap_list(_short_text()),
    deny_patterns=_cap_list(_short_text()),
    reviewer=_opt(st.sampled_from(list(Reviewer))),
)

st.register_type_strategy(FilesystemPolicy, filesystem_policies)
st.register_type_strategy(NetworkPolicy, network_policies)
st.register_type_strategy(Authorization, authorizations)

# ----------------------------------------------------------------------
# Environment domain
# ----------------------------------------------------------------------

environments: st.SearchStrategy[Environment] = st.builds(
    Environment,
    variables=_cap_dict(_short_text(), _short_text()),
    inherit=_opt(st.sampled_from(list(InheritPolicy))),
    include_only=_cap_list(_short_text()),
    exclude=_cap_list(_short_text()),
    additional_directories=_cap_list(_short_text()),
    respect_gitignore=_opt(st.booleans()),
)

st.register_type_strategy(Environment, environments)

# ----------------------------------------------------------------------
# Lifecycle domain
# ----------------------------------------------------------------------

histories: st.SearchStrategy[History] = st.builds(
    History,
    persistence=_opt(st.sampled_from(list(HistoryPersistence))),
    max_bytes=_opt(st.integers(min_value=0, max_value=2**31 - 1)),
)

telemetries: st.SearchStrategy[Telemetry] = st.builds(
    Telemetry,
    exporter=_opt(st.sampled_from(list(TelemetryExporter))),
    endpoint=_opt(_short_text()),
)

hook_command_shells: st.SearchStrategy[HookCommandShell] = st.builds(
    HookCommandShell,
    command=_short_text(),
    timeout=_opt(st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False)),
)

hook_matchers: st.SearchStrategy[HookMatcher] = st.builds(
    HookMatcher,
    matcher=_opt(_short_text()),
    hooks=_cap_list(hook_command_shells),
)

hooks: st.SearchStrategy[Hooks] = st.builds(
    Hooks,
    pre_tool_use=_opt(_cap_list(hook_matchers)),
    post_tool_use=_opt(_cap_list(hook_matchers)),
    notification=_opt(_cap_list(hook_matchers)),
    user_prompt_submit=_opt(_cap_list(hook_matchers)),
    stop=_opt(_cap_list(hook_matchers)),
    subagent_stop=_opt(_cap_list(hook_matchers)),
    pre_compact=_opt(_cap_list(hook_matchers)),
    session_start=_opt(_cap_list(hook_matchers)),
    session_end=_opt(_cap_list(hook_matchers)),
)

lifecycles: st.SearchStrategy[Lifecycle] = st.builds(
    Lifecycle,
    hooks=hooks,
    history=histories,
    telemetry=telemetries,
    cleanup_period_days=_opt(st.integers(min_value=0, max_value=3650)),
)

st.register_type_strategy(History, histories)
st.register_type_strategy(Telemetry, telemetries)
st.register_type_strategy(HookCommandShell, hook_command_shells)
st.register_type_strategy(HookMatcher, hook_matchers)
st.register_type_strategy(Hooks, hooks)
st.register_type_strategy(Lifecycle, lifecycles)

# ----------------------------------------------------------------------
# Interface domain
# ----------------------------------------------------------------------

voices: st.SearchStrategy[Voice] = st.builds(
    Voice,
    enabled=_opt(st.booleans()),
    mode=_opt(st.sampled_from(list(VoiceMode))),
)

interfaces: st.SearchStrategy[Interface] = st.builds(
    Interface,
    fullscreen=_opt(st.booleans()),
    theme=_opt(_short_text()),
    editor_mode=_opt(_short_text()),
    status_line_command=_opt(_short_text()),
    file_opener=_opt(_short_text()),
    voice=_opt(voices),
    motion_reduced=_opt(st.booleans()),
    notification_channel=_opt(_short_text()),
)

st.register_type_strategy(Voice, voices)
st.register_type_strategy(Interface, interfaces)

# ----------------------------------------------------------------------
# Governance domain
# ----------------------------------------------------------------------

trusts: st.SearchStrategy[Trust] = st.builds(
    Trust,
    trusted_paths=_cap_list(_short_text()),
    untrusted_paths=_cap_list(_short_text()),
)

updates: st.SearchStrategy[Updates] = st.builds(
    Updates,
    channel=_opt(st.sampled_from(list(UpdatesChannel))),
    minimum_version=_opt(_short_text()),
)

governances: st.SearchStrategy[Governance] = st.builds(
    Governance,
    managed=_cap_dict(_short_text(), _short_text()),
    trust=trusts,
    updates=updates,
    features=_cap_dict(_short_text(), st.booleans()),
)

st.register_type_strategy(Trust, trusts)
st.register_type_strategy(Updates, updates)
st.register_type_strategy(Governance, governances)

# ----------------------------------------------------------------------
# PassThroughBag — typed-as-JsonValue at neutral, target-validated at
# codec. The strategy mirrors that: any JsonValue is allowed.
# ----------------------------------------------------------------------

passthrough_bags: st.SearchStrategy[PassThroughBag] = st.builds(
    PassThroughBag,
    items=_cap_dict(_short_text(), json_value),
)

st.register_type_strategy(PassThroughBag, passthrough_bags)

# ----------------------------------------------------------------------
# Profile + Neutral
# ----------------------------------------------------------------------

profiles: st.SearchStrategy[Profile] = st.builds(
    Profile,
    identity=_opt(identities),
    directives=_opt(directives),
    capabilities=_opt(capabilities),
    authorization=_opt(authorizations),
    environment=_opt(environments),
    lifecycle=_opt(lifecycles),
    interface=_opt(interfaces),
    governance=_opt(governances),
)

st.register_type_strategy(Profile, profiles)

neutrals: st.SearchStrategy[Neutral] = st.builds(
    Neutral,
    schema_version=st.just(1),
    identity=identities,
    directives=directives,
    capabilities=capabilities,
    authorization=authorizations,
    environment=environments,
    lifecycle=lifecycles,
    interface=interfaces,
    governance=governances,
    profiles=_cap_dict(_short_text(), profiles),
    targets=_cap_dict(target_ids, passthrough_bags),
)

st.register_type_strategy(Neutral, neutrals)


# ----------------------------------------------------------------------
# Composite strategy: extra_keys_at_random_depth.
# ----------------------------------------------------------------------


def _model_descent_paths(model_class: type[BaseModel]) -> list[tuple[str, ...]]:
    """Enumerate every (root, sub, sub-sub...) path that ends at a
    Pydantic-model field, including the empty path (root).

    Used by :func:`extra_keys_at_random_depth` to pick a splice point.
    Tuples are `()` for the root and e.g. `("identity", "endpoint")`
    for a nested submodel.
    """
    paths: list[tuple[str, ...]] = [()]
    seen: set[type[BaseModel]] = {model_class}

    def descend(cls: type[BaseModel], prefix: tuple[str, ...]) -> None:
        for name, field in cls.model_fields.items():
            ann = field.annotation
            # Only descend into BaseModel subclasses (not unions or
            # collection types — they don't have model_fields).
            if isinstance(ann, type) and issubclass(ann, BaseModel) and ann not in seen:
                seen.add(ann)
                child = (*prefix, name)
                paths.append(child)
                descend(ann, child)

    descend(model_class, ())
    return paths


@st.composite
def extra_keys_at_random_depth(
    draw: st.DrawFn, model_class: type[BaseModel]
) -> tuple[tuple[str, ...], dict[str, JsonValue]]:
    """Pick a random model-descent path inside ``model_class`` and emit
    a small ``dict[str, JsonValue]`` of unmodelled keys to splice there.

    Returns ``(path, extras)``. The caller is responsible for splicing
    ``extras`` into the dict-form of a model instance at ``path``.
    Wave-F2's extra-keys test uses this to verify ``extra="forbid"``
    rejects every spliced key.

    Extra key names are drawn from a regex that explicitly avoids
    colliding with any modelled field name at the chosen depth.
    """
    descent_paths = _model_descent_paths(model_class)
    path = draw(st.sampled_from(descent_paths))

    # Discover modelled field names at the splice point so we can avoid
    # collisions. If `path == ()` the splice point is the root model;
    # otherwise walk to it.
    here: type[BaseModel] = model_class
    for seg in path:
        ann = here.model_fields[seg].annotation
        # Walk only through BaseModel-typed segments (the ones
        # `_model_descent_paths` discovered).
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            here = ann
        else:
            # Path can't actually be reached as a model — bail gracefully.
            break

    modelled_names = set(here.model_fields.keys())
    # Generate keys that are identifier-shaped but not in `modelled_names`.
    # Ten attempts is plenty; if all collide we yield an empty extras
    # dict (still a useful test case — the splice is a no-op).
    extras: dict[str, JsonValue] = {}
    n_extras = draw(st.integers(min_value=1, max_value=4))
    for _ in range(n_extras):
        for _attempt in range(10):
            candidate = draw(_identifier)
            if candidate not in modelled_names and candidate not in extras:
                extras[candidate] = draw(json_value)
                break
    return path, extras


# ----------------------------------------------------------------------
# Composite strategy: partial_neutral_with_holes.
# ----------------------------------------------------------------------


@st.composite
def partial_neutral_with_holes(draw: st.DrawFn) -> Neutral:
    """Generate a :class:`Neutral` with a random subset of domain fields
    populated.

    Operator-authored neutral files routinely leave entire domains as
    their default empty submodel. This strategy simulates that by
    picking a random subset of the eight domains to populate and
    leaving the rest at their factory defaults.
    """
    populate = draw(
        st.sets(
            st.sampled_from(
                [
                    "identity",
                    "directives",
                    "capabilities",
                    "authorization",
                    "environment",
                    "lifecycle",
                    "interface",
                    "governance",
                ]
            ),
            min_size=0,
            max_size=8,
        )
    )
    kwargs: dict[str, Any] = {"schema_version": 1}
    if "identity" in populate:
        kwargs["identity"] = draw(identities)
    if "directives" in populate:
        kwargs["directives"] = draw(directives)
    if "capabilities" in populate:
        kwargs["capabilities"] = draw(capabilities)
    if "authorization" in populate:
        kwargs["authorization"] = draw(authorizations)
    if "environment" in populate:
        kwargs["environment"] = draw(environments)
    if "lifecycle" in populate:
        kwargs["lifecycle"] = draw(lifecycles)
    if "interface" in populate:
        kwargs["interface"] = draw(interfaces)
    if "governance" in populate:
        kwargs["governance"] = draw(governances)
    return Neutral(**kwargs)


# ----------------------------------------------------------------------
# Cross-target shared paths — the manually-curated list. Wave-F2's
# differential test will iterate this and assert every path round-trips
# byte-identically through both targets.
# ----------------------------------------------------------------------


def cross_target_shared_paths() -> list[FieldPath]:
    """Neutral field-paths where Claude and Codex are expected to
    carry the same value.

    Curated from ``docs/superpowers/specs/2026-05-06-parity-gap.md``
    section "Shared concepts". This is intentionally small and
    principled — when we add cross-target codec coverage, we extend
    this list rather than a regex.
    """
    return [
        FieldPath(segments=("identity", "reasoning_effort")),
        FieldPath(segments=("identity", "model")),
        FieldPath(segments=("identity", "thinking")),
        FieldPath(segments=("directives", "commit_attribution")),
        FieldPath(segments=("directives", "system_prompt_file")),
        FieldPath(segments=("capabilities", "plugins")),
        FieldPath(segments=("capabilities", "plugin_marketplaces")),
        FieldPath(segments=("capabilities", "mcp_servers")),
        FieldPath(segments=("environment", "variables")),
    ]


# Sanity guard — if a future schema rename breaks this list, the
# import-time check catches it before any fuzz test runs. Each segment
# tuple must resolve through the Neutral model.
def _verify_cross_target_paths() -> None:
    for path in cross_target_shared_paths():
        cur: type[BaseModel] | None = Neutral
        for seg in path.segments:
            if cur is None or seg not in cur.model_fields:
                msg = (
                    f"cross_target_shared_paths references {path.render()!r} "
                    f"but segment {seg!r} is not a Neutral field; the schema "
                    f"likely renamed something — update strategies.py."
                )
                raise AssertionError(msg)
            ann = cur.model_fields[seg].annotation
            cur = ann if isinstance(ann, type) and issubclass(ann, BaseModel) else None


_verify_cross_target_paths()


__all__ = [
    "MAX_COLLECTION_SIZE",
    "MAX_STRING_SIZE",
    "authorizations",
    "capabilities",
    "cross_target_shared_paths",
    "directives",
    "environments",
    "extra_keys_at_random_depth",
    "field_paths",
    "filesystem_policies",
    "governances",
    "histories",
    "hook_command_shells",
    "hook_matchers",
    "hooks",
    "http_urls",
    "identities",
    "identity_auth",
    "identity_endpoints",
    "interfaces",
    "json_value",
    "lifecycles",
    "mcp_http",
    "mcp_servers",
    "mcp_stdio",
    "network_policies",
    "neutrals",
    "partial_neutral_with_holes",
    "passthrough_bags",
    "plugin_entries",
    "plugin_marketplace_sources",
    "plugin_marketplaces",
    "profiles",
    "target_ids",
    "telemetries",
    "trusts",
    "unicode_torture",
    "updates",
    "voices",
]


# Re-export for callers that want a quick "is X a valid identifier?" check.
def _is_identifier(s: str) -> bool:
    return bool(_IDENT_RE.match(s))
