"""Field-walker for the no-silent-upstream-drops static test.

Walks every wire-level field in a target's generated upstream Pydantic
``full_model`` (e.g. ``ClaudeCodeSettings``, ``ConfigToml``) and classifies
each field path against the codecs registered for that target.

Classification (per  spec; see ``test_no_silent_upstream_drops.py``
docstring for the full rationale):

* **Claimed** — some codec for that target has the field's path in its
  ``claimed_paths``. (Round-trip is verified by the property tests.)
* **Pass-through** — Chameleon would absorb the field's wire data
  losslessly without any codec actively translating it. The judgement
  is *operational*, not just structural — see the rule in
  ``classify_field`` below.
* **Loss-warned** — at least one codec source-file emits a typed
  ``LossWarning`` whose ``field_path`` argument or ``message`` text
  references this field by name. The match is intentionally a string
  search (not semantic): the codec author opted into surfacing the loss
  to the operator, and that opt-in is the contract this test enforces.

Anything else is a **silent drop** — wire data the operator wrote that
disappears with no record. That's the bug class this test exists to
catch.

Where this walker is deliberately conservative
----------------------------------------------

* The walk descends into nested ``BaseModel`` fields, ``dict[K, BaseModel]``
  values, ``list[BaseModel]`` elements, ``RootModel[...]`` wrappers, and
  every ``BaseModel`` arm of a Union (PEP 604 or ``typing.Union``). Lessons
  from  F2: anything that can carry operator-written keys is reachable.

* Field-path *segments* are upstream **wire keys** — the alias when one is
  declared, otherwise the Python attribute name. That mirrors how
  ``claimed_paths`` is authored in the codecs (e.g. ``mcpServers`` not
  ``mcp_servers``) and how the assemblers route by top-level key.

* The ``LossWarning`` source-text scan is a deliberately blunt
  ``re.findall`` over each codec module. False positives are possible when
  a field name is a common English word also appearing in unrelated text,
  but they only ever push a field from "silent drop" to "loss-warned" —
  the *strict* direction. The test reports use of the heuristic so reviewers
  can audit borderline matches.
"""

from __future__ import annotations

import importlib
import re
import types
import typing
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, RootModel
from pydantic.fields import FieldInfo

from chameleon._types import TargetId
from chameleon.codecs._protocol import Codec

# -- Disposition ---------------------------------------------------------


@dataclass(frozen=True)
class Disposition:
    """One field's classification result.

    ``label`` is exactly one of: ``claimed``, ``pass-through``,
    ``loss-warned``, ``silent-drop``. The other attributes carry the
    evidence the classifier used to reach that label so the test report
    can render a useful diagnosis.
    """

    label: str
    why: str = ""
    by_codec: tuple[str, ...] = ()
    extra_allow_at: tuple[str, ...] | None = None


# -- Walking -------------------------------------------------------------


def iter_field_paths(
    model: type[BaseModel],
) -> Iterator[tuple[tuple[str, ...], FieldInfo, type[BaseModel]]]:
    """Yield every wire-level leaf-or-table-key field path in ``model``.

    For each yielded ``(segments, info, parent)``:

    * ``segments`` is the tuple of upstream wire keys (alias when
      declared, otherwise the Python attribute name).
    * ``info`` is the ``FieldInfo`` carrying the field's annotation.
    * ``parent`` is the immediate parent BaseModel class (i.e. the class
      that *declares* this field). Callers use ``parent.model_config``
      to inspect ``extra="allow"`` / ``extra="forbid"`` semantics.

    The walker descends into:

    * Nested ``BaseModel`` annotations (incl. ``Optional[BaseModel]``).
    * ``RootModel[X]`` wrappers — replaced by the root annotation ``X``.
    * Union arms — every ``BaseModel`` branch is descended.
    * ``dict[K, V]`` values when ``V`` resolves to a ``BaseModel``.
    * ``list[T]`` elements when ``T`` resolves to a ``BaseModel``.

    Cycle protection: each ``(parent_class, field_name)`` pair is yielded
    once, but recursion into the same model class twice (e.g., recursive
    schema) is short-circuited via ``_seen_models`` to keep the walk
    finite.
    """
    yield from _walk(model, (), set())


def _walk(
    model: type[BaseModel],
    prefix: tuple[str, ...],
    _seen_models: set[type[BaseModel]],
) -> Iterator[tuple[tuple[str, ...], FieldInfo, type[BaseModel]]]:
    if model in _seen_models:
        return
    _seen_models = _seen_models | {model}

    # ``RootModel[X]`` wraps a single ``root: X`` field; descend into ``X``
    # rather than yielding ``root`` (that segment never appears on the wire).
    if issubclass(model, RootModel):
        root_info = model.model_fields.get("root")
        if root_info is not None:
            yield from _descend_annotation(root_info.annotation, prefix, _seen_models)
        return

    for py_name, info in model.model_fields.items():
        seg = info.alias if info.alias is not None else py_name
        path = (*prefix, seg)
        yield (path, info, model)
        yield from _descend_annotation(info.annotation, path, _seen_models)


def _descend_annotation(
    ann: object,
    path: tuple[tuple[str, ...] | str, ...],
    _seen_models: set[type[BaseModel]],
) -> Iterator[tuple[tuple[str, ...], FieldInfo, type[BaseModel]]]:
    """Recurse into ``ann``: yield from any embedded ``BaseModel``."""
    # ``path`` is the parent path (already-resolved tuple of wire keys).
    parent_path: tuple[str, ...] = tuple(seg for seg in path if isinstance(seg, str))

    for sub_model in _basemodels_in_annotation(ann):
        yield from _walk(sub_model, parent_path, _seen_models)


def _basemodels_in_annotation(ann: object) -> Iterable[type[BaseModel]]:
    """Yield every ``BaseModel`` subclass reachable inside ``ann``.

    Descends into ``Union`` (incl. PEP 604 ``X | None``), ``list[T]``,
    ``dict[K, V]``, ``Optional[X]``, and ``RootModel`` subclasses.
    Returns concrete BaseModel subclasses only; bare scalars / enums /
    literals are ignored.
    """
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        yield ann
        return
    origin = typing.get_origin(ann)
    if origin is None:
        return
    args = typing.get_args(ann)
    if origin in (typing.Union, types.UnionType):
        for arg in args:
            yield from _basemodels_in_annotation(arg)
        return
    # list[T], tuple[T, ...], set[T] — descend into the element type.
    if origin in (list, set, frozenset, tuple):
        for arg in args:
            yield from _basemodels_in_annotation(arg)
        return
    # dict[K, V] — keys are wire-level strings; descend into V only.
    if origin in (dict, Mapping):
        if len(args) >= 2:
            yield from _basemodels_in_annotation(args[1])
        return


# -- Classification ------------------------------------------------------


@dataclass
class TargetClassifier:
    """Pre-computed lookup tables for one target's classification pass."""

    target: TargetId
    full_model: type[BaseModel]
    codecs: tuple[type[Codec], ...]
    # Indexed claimed paths (tuple form) → list of "<TargetId>/<Domain>" strings.
    claimed_index: dict[tuple[str, ...], list[str]] = field(default_factory=dict)
    # Top-level wire key → list of codec sections that handle that key.
    top_level_to_codec: dict[str, list[type[Codec]]] = field(default_factory=dict)
    # Loss-warning text-presence index: wire-segment → list of "<TargetId>/<Domain>".
    loss_warned_segments: dict[str, list[str]] = field(default_factory=dict)
    # Loss-warning explicit FieldPath(segments=(…)) tuples → list of codec names.
    loss_warned_paths: dict[tuple[str, ...], list[str]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        target: TargetId,
        full_model: type[BaseModel],
        codecs: tuple[type[Codec], ...],
        codec_source_files: Mapping[type[Codec], Path],
    ) -> TargetClassifier:
        clf = cls(target=target, full_model=full_model, codecs=codecs)

        for codec in codecs:
            label = f"{codec.target}/{codec.domain.value}"
            for path in codec.claimed_paths:
                clf.claimed_index.setdefault(path.segments, []).append(label)
                if path.segments:
                    clf.top_level_to_codec.setdefault(path.segments[0], []).append(codec)

        # Scan each codec's source for LossWarning emissions. We harvest
        # two pieces of evidence:
        #   1. ``field_path=FieldPath(segments=(...))`` arguments — exact paths.
        #   2. The full ``LossWarning(...)`` constructor text — split into
        #      tokens and indexed by individual segments. A field whose
        #      *name* (any segment) appears in some warning text is
        #      considered loss-warned.
        for codec in codecs:
            label = f"{codec.target}/{codec.domain.value}"
            src_path = codec_source_files.get(codec)
            if src_path is None or not src_path.exists():
                continue
            text = src_path.read_text(encoding="utf-8")

            for warn_block in _iter_loss_warning_blocks(text):
                # Capture every ``segments=(…)`` tuple inside this block.
                for seg_tuple in _iter_field_path_segments(warn_block):
                    clf.loss_warned_paths.setdefault(seg_tuple, []).append(label)
                # Capture every quoted-string token in this block — these
                # are the ``message=...`` parts. We index on bare wire
                # segment names (e.g., ``"context_window"``,
                # ``"PreCompact"``) so the classifier can match a field
                # to the warning even when the path tuple isn't spelled.
                for token in _iter_text_tokens(warn_block):
                    clf.loss_warned_segments.setdefault(token, []).append(label)

        return clf

    def classify_field(
        self,
        path: tuple[str, ...],
        parent: type[BaseModel],
    ) -> Disposition:
        """Return the disposition for a field at ``path`` whose immediate
        declaring class is ``parent``.

        Order of preference (and why):

        1. **Claimed** — strongest evidence the field round-trips. Per
           spec, when a field could be classified both as claimed and
           via some other mechanism, claimed wins.

        2. **Exact-path Loss-warned** — the codec author explicitly named
           this field in a ``LossWarning(field_path=FieldPath(...))``.
           This is the codec author's deliberate annotation that the
           field is documented-lossy. It outranks pass-through because
           the operator's intent is captured.

        3. **Pass-through** — Chameleon's data-routing absorbs the field
           losslessly. Either the assembler's verbatim pass-through bag
           (top-level wire keys no codec claims) or the codec section's
           ``extra="allow"`` chain harvesting via ``__pydantic_extra__``.
           Most upstream fields land here; that's the design's
           wire-data-preservation backbone.

        4. **Heuristic Loss-warned** — the last-resort fallback. Some
           ``LossWarning(...)`` somewhere in the codec source quotes a
           token that matches a segment of this path. The match is a
           string heuristic (no semantic verification). Ranked below
           pass-through because the heuristic produces false positives
           (e.g. ``mcpServers`` mentioned in the capabilities codec's
           warnings doesn't really make every other ``mcpServers``-named
           field "loss-warned").

        5. **Silent drop** — none of the above applied.
        """
        # 1. Claimed: prefix-or-equal match against any codec's
        # ``claimed_paths``. Prefix semantics: claiming ``("hooks",)``
        # covers everything under ``hooks.*``.
        for claimed_segs, codec_labels in self.claimed_index.items():
            if _path_covers(claimed_segs, path):
                return Disposition(
                    label="claimed",
                    why=(
                        f"covered by codec claim {claimed_segs!r}"
                        if claimed_segs != path
                        else "exact claim"
                    ),
                    by_codec=tuple(sorted(set(codec_labels))),
                )

        # 2. Exact-path LossWarning match — codec author opted in by
        # passing this exact path as ``field_path=FieldPath(segments=…)``.
        if path in self.loss_warned_paths:
            return Disposition(
                label="loss-warned",
                why=f"LossWarning(field_path=FieldPath(segments={path!r}))",
                by_codec=tuple(sorted(set(self.loss_warned_paths[path]))),
            )

        # 3. Pass-through-eligible: codec section ``extra="allow"`` chain
        # or assembler pass-through bag.
        passthrough = self._passthrough_via_codec_section(path)
        if passthrough is not None:
            return passthrough

        # 4. Heuristic LossWarning match — last-resort. A quoted token
        # in some ``LossWarning(...)`` text matches a segment of this
        # path. Check the *last* segment first (most specific), then
        # any segment as a fallback.
        for seg in reversed(path):
            if seg in self.loss_warned_segments:
                return Disposition(
                    label="loss-warned",
                    why=(
                        f"LossWarning text mentions {seg!r} "
                        "(string-match heuristic; not a path-shape match)"
                    ),
                    by_codec=tuple(sorted(set(self.loss_warned_segments[seg]))),
                )

        # 5. Silent drop.
        return Disposition(
            label="silent-drop",
            why=(
                "no codec claims this path, no LossWarning mentions it, "
                "and no codec section ancestor has extra='allow' to "
                "harvest it via __pydantic_extra__"
            ),
        )

    def _passthrough_via_codec_section(
        self,
        path: tuple[str, ...],
    ) -> Disposition | None:
        """Decide whether the wire-data routing keeps this field alive.

        Top-level wire keys not claimed by any codec land in the
        assembler's pass-through bag (verbatim copy). Top-level wire keys
        claimed by a codec route the entire subtree into that codec's
        section model; if any ancestor along the path inside the section
        model has ``extra="allow"``, the deeper key survives via
        ``__pydantic_extra__``.
        """
        if not path:
            return None

        top = path[0]
        codecs_for_top = self.top_level_to_codec.get(top, [])

        if not codecs_for_top:
            # Top-level key the assembler's bag absorbs.
            return Disposition(
                label="pass-through",
                why=(
                    f"top-level wire key {top!r} is not claimed by any codec; "
                    "assembler routes it to the verbatim pass-through bag"
                ),
            )

        # The top-level key is claimed — descend into each handling codec's
        # ``target_section`` and check for an ``extra="allow"`` ancestor
        # along the path.
        for codec in codecs_for_top:
            section_cls = codec.target_section
            allow_at = _walk_section_for_extra_allow(section_cls, path)
            if allow_at is not None:
                return Disposition(
                    label="pass-through",
                    why=(
                        f"codec section {section_cls.__name__} has "
                        f"extra='allow' at {allow_at!r}; unmodelled "
                        "descendants harvest via __pydantic_extra__"
                    ),
                    by_codec=(f"{codec.target}/{codec.domain.value}",),
                    extra_allow_at=allow_at,
                )

        return None


def _path_covers(claim: tuple[str, ...], field_path: tuple[str, ...]) -> bool:
    """True iff ``claim`` covers (equals or is an ancestor of) ``field_path``.

    Codec authors may claim a sub-tree wholesale (e.g. ``("hooks",)``
    covers every wire field under ``hooks.*``). They may also claim a
    deeper leaf exactly (e.g. ``("permissions", "allow")`` claims that
    one leaf). We treat both as "claimed" — sub-tree claims propagate
    downward, exact claims match a single path.
    """
    if len(claim) > len(field_path):
        return False
    return field_path[: len(claim)] == claim


def _walk_section_for_extra_allow(
    section_cls: type[BaseModel],
    path: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Walk ``section_cls`` along ``path`` (skipping the routing top key,
    which the assembler resolves) and return the deepest ancestor whose
    ``model_config['extra']`` is ``"allow"``. Return None if no ancestor
    along the path is permissive.

    The walk stops as soon as it can't resolve a segment (e.g., the
    codec section doesn't model a particular nested table at all). In
    that case, if the section *root* is ``extra="allow"`` it absorbs the
    whole subtree as ``__pydantic_extra__`` — pass-through. Otherwise:
    silent drop.
    """
    # Path includes the top-level wire key as its first segment. The
    # codec section either has a field named exactly that key (so we
    # walk into it) or the section root absorbs it as extras (only when
    # the root is extra="allow").
    current: type[BaseModel] | None = section_cls
    deepest_allow: tuple[str, ...] | None = None

    if _model_extra_is_allow(section_cls):
        deepest_allow = ()

    for depth, seg in enumerate(path):
        if current is None:
            # We left the typed model graph; the deepest extra="allow"
            # ancestor (recorded above) decides.
            return deepest_allow
        # Resolve seg as a wire key (alias) or as a Python attr name.
        info = _find_field(current, seg)
        if info is None:
            # Section doesn't model this segment as a typed field. If
            # ``current`` is extra="allow", the segment survives via
            # ``__pydantic_extra__`` — pass-through.
            if _model_extra_is_allow(current):
                return path[: depth + 1]
            return deepest_allow
        # Step into the typed sub-model (if any).
        current = _basemodel_for(info.annotation)
        if current is not None and _model_extra_is_allow(current):
            deepest_allow = path[: depth + 1]

    return deepest_allow


def _basemodel_for(ann: object) -> type[BaseModel] | None:
    for sub in _basemodels_in_annotation(ann):
        return sub
    return None


def _find_field(model: type[BaseModel], wire_seg: str) -> FieldInfo | None:
    """Find ``wire_seg`` either as a Python field name or as a Pydantic alias.

    The same alias-aware lookup the codec protocol uses
    (``_find_field_by_name_or_alias`` in ``codecs/_protocol.py``).
    """
    fields = getattr(model, "model_fields", None)
    if not fields:
        return None
    if wire_seg in fields:
        return fields[wire_seg]
    for info in fields.values():
        if info.alias == wire_seg:
            return info
    return None


def _model_extra_is_allow(cls: type[BaseModel]) -> bool:
    cfg = getattr(cls, "model_config", None)
    if cfg is None:
        return False
    extra = cfg.get("extra") if isinstance(cfg, dict) else getattr(cfg, "extra", None)
    return extra == "allow"


# -- LossWarning source-text scanning ------------------------------------


_LOSS_WARNING_BLOCK_RE = re.compile(
    r"LossWarning\s*\(",
    re.MULTILINE,
)
_FIELD_PATH_RE = re.compile(
    # FieldPath(segments=("a", "b")) — minimal balanced segments tuple.
    r"FieldPath\s*\(\s*segments\s*=\s*\(([^)]*?)\)\s*\)",
    re.DOTALL,
)
_TOKEN_RE = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")


def _iter_loss_warning_blocks(text: str) -> Iterator[str]:
    """Yield each ``LossWarning(...)`` constructor body as a substring.

    Walks paren-balanced from each ``LossWarning(`` start; emits the
    block contents (excluding the outer parens). Robust against nested
    parens (FieldPath, function calls inside f-strings, etc.).
    """
    for match in _LOSS_WARNING_BLOCK_RE.finditer(text):
        start = match.end()  # just past the opening paren
        depth = 1
        i = start
        n = len(text)
        in_str: str | None = None
        while i < n and depth > 0:
            ch = text[i]
            if in_str is not None:
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch in ("'", '"'):
                in_str = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        if depth == 0:
            yield text[start : i - 1]


def _iter_field_path_segments(block: str) -> Iterator[tuple[str, ...]]:
    """Yield each ``FieldPath(segments=(…))`` tuple inside ``block``."""
    for m in _FIELD_PATH_RE.finditer(block):
        inner = m.group(1)
        segs = tuple(s.strip().strip("'\"") for s in inner.split(",") if s.strip())
        if segs:
            yield segs


def _iter_text_tokens(block: str) -> Iterator[str]:
    """Yield every quoted identifier-like token in ``block``.

    These come from ``message=...`` strings. Identifiers are matched
    permissively (``[A-Za-z_][A-Za-z0-9_]*``) so we catch
    ``alwaysThinkingEnabled``, ``model_context_window``, ``PreCompact``,
    etc. Common-English false positives are unavoidable but
    acceptable — see module docstring.
    """
    for m in _TOKEN_RE.finditer(block):
        yield m.group(1)


# -- Helpers for the test ------------------------------------------------


def codec_source_paths(codecs: Iterable[type[Codec]]) -> dict[type[Codec], Path]:
    """Map each codec class to the source file it's defined in.

    Resolves ``codec.__module__`` and reads the live ``__file__``
    attribute. Every V0 codec lives at
    ``src/chameleon/codecs/<target>/<domain>.py``.
    """
    out: dict[type[Codec], Path] = {}
    for codec in codecs:
        mod = importlib.import_module(codec.__module__)
        file_attr = getattr(mod, "__file__", None)
        if file_attr is not None:
            out[codec] = Path(file_attr)
    return out


def is_terminal_field(info: FieldInfo) -> bool:
    """A field is *terminal* (a leaf to assert against) iff its annotation
    contains no descendable BaseModel.

    The walker yields *every* field path — both terminals and
    interior table-keys (e.g., ``permissions`` is itself a wire key, as
    are ``permissions.allow`` and ``permissions.deny``). The test
    classifies all of them: a field path can be claimed at a non-leaf
    level (e.g., ``("hooks",)`` claims the whole hooks subtree).
    """
    return not any(True for _ in _basemodels_in_annotation(info.annotation))


def render_path(segments: tuple[str, ...]) -> str:
    """Dotted human-readable rendering of a field path."""
    return ".".join(segments)


__all__ = [
    "Disposition",
    "TargetClassifier",
    "codec_source_paths",
    "is_terminal_field",
    "iter_field_paths",
    "render_path",
]
