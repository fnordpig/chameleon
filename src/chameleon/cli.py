"""Chameleon CLI — argparse subcommand router with typed parsing.

Per design spec (strict typing): CLI arg parsing produces typed
TargetId, Domains, OnConflict values; no string match statements
downstream.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

from chameleon import __version__
from chameleon._types import FileOwnership, TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge.changeset import walk_changes
from chameleon.merge.drift import unified_diff
from chameleon.merge.engine import MergeEngine, MergeRequest, MergeResult
from chameleon.merge.resolutions import compute_decision_hash, render_change_path
from chameleon.merge.resolve import (
    InteractiveResolver,
    LatestResolutionError,
    LatestResolver,
    NonInteractiveResolver,
    Resolver,
    Strategy,
    on_conflict_to_strategy,
    stdin_is_a_tty,
)
from chameleon.schema._constants import OnConflict
from chameleon.schema.neutral import Neutral, Resolutions
from chameleon.state.git import GitRepo
from chameleon.state.locks import partial_owned_write
from chameleon.state.notices import NoticeStore
from chameleon.state.paths import StatePaths
from chameleon.state.transaction import TransactionStore
from chameleon.targets._protocol import Target
from chameleon.targets._registry import TargetRegistry


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--neutral", type=str, default=None, help="path to the neutral.yaml file")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--no-warn",
        action="store_true",
        help="suppress LossWarning errata after merge",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "emit per-target progress on stderr, "
            "summarise per-codec claim and warning counts after merge, "
            "surface pending state-repo notices and unresolved transactions at every command."
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chameleon",
        description=(
            "Transpile a neutral agent configuration into Claude Code, "
            "Codex CLI, and other agent-specific formats — and back again."
        ),
    )
    parser.add_argument("--version", action="version", version=f"chameleon {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_init = sub.add_parser("init", help="first-time bootstrap")
    _add_common_args(p_init)

    p_merge = sub.add_parser("merge", help="round-trip neutral <-> targets")
    _add_common_args(p_merge)
    p_merge.add_argument(
        "--on-conflict",
        type=str,
        default=None,
        help="latest | fail | keep | prefer-neutral | prefer-lkg | prefer=<target>",
    )
    p_merge.add_argument("--profile", type=str, default=None)

    p_status = sub.add_parser("status", help="per-target drift summary")
    _add_common_args(p_status)

    p_diff = sub.add_parser(
        "diff",
        help="unified diff between state-repo HEAD and live target files",
    )
    _add_common_args(p_diff)
    p_diff.add_argument(
        "target",
        type=str,
        nargs="?",
        default=None,
        help="target id; omit to diff every registered target",
    )

    p_log = sub.add_parser("log", help="state-repo timeline for one target")
    _add_common_args(p_log)
    p_log.add_argument("target", type=str)
    p_log.add_argument("--json", action="store_true")

    p_adopt = sub.add_parser("adopt", help="merge resolving every conflict in favor of one target")
    _add_common_args(p_adopt)
    p_adopt.add_argument("target", type=str)

    p_discard = sub.add_parser("discard", help="overwrite live with state-repo HEAD")
    _add_common_args(p_discard)
    p_discard.add_argument("target", type=str)
    p_discard.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive confirmation; required off a TTY",
    )

    p_validate = sub.add_parser("validate", help="schema-validate the neutral file")
    _add_common_args(p_validate)

    p_doctor = sub.add_parser("doctor", help="environment health check")
    _add_common_args(p_doctor)
    p_doctor.add_argument("--clear-notices", action="store_true")
    p_doctor.add_argument("--notices-only", action="store_true")

    p_targets = sub.add_parser("targets", help="target plugin operations")
    _add_common_args(p_targets)
    p_targets.add_argument("op", choices=["list"])

    p_resolutions = sub.add_parser(
        "resolutions",
        help="inspect or clear stored conflict resolutions ()",
    )
    _add_common_args(p_resolutions)
    p_resolutions.add_argument(
        "op",
        choices=["list", "clear"],
        help="list: show stored resolutions; clear: remove one or all entries",
    )
    p_resolutions.add_argument(
        "path",
        nargs="?",
        default=None,
        help="(clear only) the resolution path to remove; omit to clear all",
    )
    p_resolutions.add_argument(
        "--yes",
        action="store_true",
        help="(clear) skip the interactive confirmation; required off a TTY",
    )

    return parser


def _resolve_paths(args: argparse.Namespace) -> StatePaths:
    override = Path(args.neutral) if args.neutral else None
    return StatePaths.resolve(neutral_override=override)


def _cmd_init(args: argparse.Namespace) -> int:
    paths = _resolve_paths(args)
    targets = TargetRegistry.discover()
    n_targets = sum(1 for _ in targets.target_ids())

    if args.dry_run:
        # Dry-run is side-effect-free. Describe what init WOULD do without
        # writing the neutral file or invoking the merge engine's write
        # path. The follow-up `chameleon init` (no flag) does the work.
        sys.stdout.write("init --dry-run: side-effect-free; would do the following:\n")
        if not paths.neutral.exists():
            sys.stdout.write(f"  - create neutral file at {paths.neutral}\n")
        else:
            sys.stdout.write(f"  - leave existing neutral file at {paths.neutral}\n")
        sys.stdout.write(f"  - run merge with strategy=KEEP across {n_targets} target(s)\n")
        sys.stdout.write("  - rerun without --dry-run to apply\n")
        return 0

    if not paths.neutral.exists():
        paths.neutral.parent.mkdir(parents=True, exist_ok=True)
        starter = Neutral(schema_version=1)
        paths.neutral.write_text(dump_yaml(starter.model_dump(mode="json")), encoding="utf-8")
        sys.stdout.write(f"init: wrote minimal neutral at {paths.neutral}\n")

    strategy = Strategy(kind=OnConflict.KEEP)
    engine = MergeEngine(targets=targets, paths=paths, strategy=strategy)
    result = engine.merge(MergeRequest(dry_run=False))
    sys.stdout.write(f"init: {result.summary}\n")
    return result.exit_code


def _resolver_from_args(args: argparse.Namespace) -> Resolver:
    """Pick a Resolver based on --on-conflict and TTY presence.

    Omitted ``--on-conflict`` defaults to ``latest``: pick the uniquely
    newest changed source when Chameleon can prove it, then prompt on a
    TTY for ambiguity. Explicit non-latest strategies are
    non-interactive by design.
    """
    raw = args.on_conflict or "latest"
    strategy = on_conflict_to_strategy(raw)
    if strategy.kind is OnConflict.LATEST:
        if stdin_is_a_tty():
            return LatestResolver(InteractiveResolver())
        return LatestResolver()
    return NonInteractiveResolver(strategy)


def _cmd_merge(args: argparse.Namespace) -> int:
    paths = _resolve_paths(args)
    targets = TargetRegistry.discover()
    resolver = _resolver_from_args(args)
    if args.verbose:
        _emit_verbose_preamble(paths, targets)
    engine = MergeEngine(targets=targets, paths=paths, resolver=resolver)
    try:
        result = engine.merge(MergeRequest(profile_name=args.profile, dry_run=args.dry_run))
    except LatestResolutionError as e:
        sys.stderr.write(f"error: {e}\n")
        sys.stderr.write(
            "rerun `chameleon merge` from an interactive shell or choose "
            "`--on-conflict=<strategy>` explicitly\n"
        )
        return 1
    # On dry-run, render any FileDiffs as a unified diff (one per file the
    # engine would have written) before the summary line. Reuses the same
    # `_emit_diff` colorizer the `chameleon diff` path uses so dry-run and
    # post-merge `diff` look identical.
    if args.dry_run and result.diffs and not args.quiet:
        stdout_console = Console(file=sys.stdout, highlight=False, soft_wrap=True)
        for fd in result.diffs:
            if not fd.changed:
                continue
            diff_text = unified_diff(
                fd.before,
                fd.after,
                label=fd.repo_path,
                head_label=f"{fd.target.value} live",
                live_label=f"{fd.target.value} merge",
            )
            _emit_diff(diff_text, stdout_console=stdout_console)
    if not args.quiet:
        sys.stdout.write(result.summary + "\n")
    # P0-2: print LossWarnings to stderr after the summary so the operator
    # sees what (if anything) was skipped without changing the exit code.
    # The warning includes the field-level error from Pydantic so the
    # operator can find the offending key in their live config.
    if not args.no_warn:
        for w in result.warnings:
            sys.stderr.write(f"warning: [{w.target.value}/{w.domain.value}] {w.message}\n")
    if args.verbose:
        _emit_verbose_summary(result, targets)
    return result.exit_code


def _emit_verbose_preamble(paths: StatePaths, targets: TargetRegistry) -> None:
    """Stderr summary of what's about to run + any pending operator state.

    Surfaces stale transactions and unacknowledged notices at every
    --verbose invocation so the operator doesn't have to remember to
    run `chameleon doctor`. The check is cheap; the visibility is
    valuable.
    """
    target_ids = sorted(t.value for t in targets.target_ids())
    sys.stderr.write(f"verbose: state_root={paths.state_root}\n")
    sys.stderr.write(f"verbose: neutral={paths.neutral}\n")
    sys.stderr.write(f"verbose: targets=[{', '.join(target_ids)}]\n")
    notices = NoticeStore(paths.notices_dir).entries()
    if notices:
        sys.stderr.write(f"verbose: {len(notices)} pending notice(s) — run `chameleon doctor`\n")
    pending_tx = TransactionStore(paths.tx_dir).entries()
    if pending_tx:
        sys.stderr.write(
            f"verbose: {len(pending_tx)} unresolved transaction(s) — run `chameleon doctor`\n"
        )


def _emit_verbose_summary(result: MergeResult, targets: TargetRegistry) -> None:
    """Per-target warning count tally on stderr after the merge summary.

    Cheap aggregation of what the operator already saw line-by-line; the
    tally line surfaces the shape of which target(s) had cross-target
    asymmetries the operator might want to investigate.
    """
    counts_by_target: dict[str, int] = {}
    for w in result.warnings:
        counts_by_target[w.target.value] = counts_by_target.get(w.target.value, 0) + 1
    for tid in sorted(t.value for t in targets.target_ids()):
        n = counts_by_target.get(tid, 0)
        sys.stderr.write(f"verbose: [{tid}] {n} LossWarning(s)\n")
    if result.merge_id:
        sys.stderr.write(f"verbose: merge_id={result.merge_id}\n")


def _cmd_targets(args: argparse.Namespace) -> int:
    targets = TargetRegistry.discover()
    if args.op == "list":
        for tid in sorted(targets.target_ids(), key=lambda t: t.value):
            sys.stdout.write(f"{tid.value}\n")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    paths = _resolve_paths(args)
    notices = NoticeStore(paths.notices_dir)
    if args.clear_notices:
        notices.clear()
        sys.stdout.write("notices cleared\n")
        return 0
    items = notices.entries()
    if args.notices_only:
        for n in items:
            sys.stdout.write(f"NOTICE [{n.timestamp.isoformat()}] {n.reason}\n")
        return 1 if items else 0
    sys.stdout.write(f"chameleon {__version__}\n")
    sys.stdout.write(f"state_root: {paths.state_root}\n")
    sys.stdout.write(f"neutral:    {paths.neutral}\n")
    if items:
        sys.stdout.write(f"notices:    {len(items)} pending\n")
    tx_store = TransactionStore(paths.tx_dir)
    pending = tx_store.entries()
    if pending:
        sys.stdout.write(f"transactions: {len(pending)} unresolved\n")
        # Surface each stale marker's merge_id so an operator (or recovery
        # tooling) can correlate the marker file with the per-target
        # state-repo commit that recorded the same Merge-Id trailer.
        for tx in pending:
            sys.stdout.write(f"  stale tx: {tx.merge_id} started_at={tx.started_at.isoformat()}\n")
        return 1
    return 0 if not items else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    paths = _resolve_paths(args)
    if not paths.neutral.exists():
        sys.stderr.write(f"neutral file not found: {paths.neutral}\n")
        return 1
    try:
        Neutral.model_validate(load_yaml(paths.neutral))
    except Exception as e:
        sys.stderr.write(f"validation failed: {e}\n")
        return 1
    sys.stdout.write("ok\n")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Per-target drift summary plus pending-state surfacing.

    Replaces the previous shape (one-liner from a dry-run merge that
    conveyed nothing useful when clean). The new output gives the
    operator: neutral file presence, per-target clean/drift state, and
    counts of any pending notices or unresolved transactions. Exit code
    is 0 if everything is clean and nothing pending; 1 if any drift OR
    pending state exists.
    """
    paths = _resolve_paths(args)
    targets = TargetRegistry.discover()

    sys.stdout.write(f"chameleon {__version__}\n")
    sys.stdout.write(f"neutral: {paths.neutral}")
    if not paths.neutral.exists():
        sys.stdout.write("  (missing — run `chameleon init`)\n")
    else:
        sys.stdout.write("  (present)\n")

    any_drift = False
    for target_id in sorted(targets.target_ids(), key=lambda t: t.value):
        target_cls = targets.get(target_id)
        if target_cls is None:
            continue
        drift, _diff_text, status = _diff_one_target(target_cls, paths, target_id)
        sys.stdout.write(f"  {status}\n")
        if drift:
            any_drift = True

    notices = NoticeStore(paths.notices_dir).entries()
    if notices:
        sys.stdout.write(f"notices: {len(notices)} pending — run `chameleon doctor`\n")
    pending_tx = TransactionStore(paths.tx_dir).entries()
    if pending_tx:
        sys.stdout.write(f"transactions: {len(pending_tx)} unresolved — run `chameleon doctor`\n")

    return 1 if (any_drift or notices or pending_tx) else 0


def _stdin_is_a_tty() -> bool:
    """Module-level seam so tests can override TTY detection.

    The merge.resolve helper reads ``sys.stdin.isatty()`` directly; we wrap
    here so the discard prompt path can be exercised end-to-end without
    actually attaching a pty.
    """
    return stdin_is_a_tty()


def _confirm_discard(prompt: str, *, console: Console) -> bool:
    """Prompt the operator to confirm a destructive discard.

    Wrapping `Confirm.ask` behind a function lets tests inject a deterministic
    answer without monkey-patching rich internals.
    """
    return bool(Confirm.ask(prompt, console=console, default=False))


_PartialLayer = Callable[[dict[str, object]], dict[str, object]]


def _make_partial_layer(
    head_obj: dict[str, object],
    owned_keys: frozenset[str],
) -> _PartialLayer:
    """Build the `partial_owned_write` callback that replays HEAD's owned keys.

    Defining this at module scope (rather than as a closure inside the discard
    loop) keeps ruff B023 happy and makes the layering rule directly testable
    without driving the CLI.
    """

    def layer(existing: dict[str, object]) -> dict[str, object]:
        merged = dict(existing)
        for k in owned_keys:
            if k in head_obj:
                merged[k] = head_obj[k]
            elif k in merged:
                del merged[k]
        return merged

    return layer


def _diff_one_target(
    target_cls: type[Target],
    paths: StatePaths,
    target_id: TargetId,
) -> tuple[bool, str, str]:
    """Diff one target's state-repo HEAD against its live config files.

    Returns ``(has_drift, stdout_text, status_line)``. ``status_line`` is
    suitable for stderr; ``stdout_text`` is the concatenated unified diff
    across all FileSpecs (empty when clean).
    """
    repo_path = paths.target_repo(target_id)
    repo_exists = (repo_path / ".git").exists()
    repo = GitRepo(repo_path) if repo_exists else None

    chunks: list[str] = []
    drift = False
    for spec in target_cls.assembler.files:
        live_path = Path(os.path.expanduser(spec.live_path))
        live_bytes = live_path.read_bytes() if live_path.exists() else b""
        head_bytes = repo.read_at_head(spec.repo_path) if repo is not None else None
        if head_bytes is None:
            head_bytes = b""
        diff_text = unified_diff(
            head_bytes,
            live_bytes,
            label=spec.repo_path,
            head_label=f"{target_id.value} HEAD",
            live_label=spec.live_path,
        )
        if diff_text:
            drift = True
            chunks.append(diff_text)

    status = f"drift detected on {target_id.value}" if drift else f"{target_id.value}: clean"
    return drift, "".join(chunks), status


def _emit_diff(diff_text: str, *, stdout_console: Console) -> None:
    """Render a unified-diff string to stdout, colorizing on a TTY.

    On a non-TTY (pipe / file redirect / capsys) we write the raw diff so
    downstream consumers (`patch`, file redirection, golden-file tests) see
    byte-identical content.
    """
    if not diff_text:
        return
    if not stdout_console.is_terminal:
        stdout_console.file.write(diff_text)
        return
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("+++") or line.startswith("---"):
            stdout_console.print(line.rstrip("\n"), style="bold", highlight=False)
        elif line.startswith("@@"):
            stdout_console.print(line.rstrip("\n"), style="cyan", highlight=False)
        elif line.startswith("+"):
            stdout_console.print(line.rstrip("\n"), style="green", highlight=False)
        elif line.startswith("-"):
            stdout_console.print(line.rstrip("\n"), style="red", highlight=False)
        else:
            stdout_console.print(line.rstrip("\n"), highlight=False)


def _cmd_diff(args: argparse.Namespace) -> int:
    """`chameleon diff [<target>]` — git-diff-style exit codes (0/1/>1)."""
    paths = _resolve_paths(args)
    targets = TargetRegistry.discover()
    stderr = Console(stderr=True, highlight=False)
    stdout = Console(file=sys.stdout, highlight=False, soft_wrap=True)

    if args.target is None:
        target_ids = sorted(targets.target_ids(), key=lambda t: t.value)
    else:
        try:
            tid = TargetId(value=args.target)
        except ValueError as e:
            stderr.print(f"[red]error[/]: {e}")
            return 2
        if targets.get(tid) is None:
            stderr.print(f"[red]error[/]: no registered target {args.target!r}")
            return 2
        target_ids = [tid]

    any_drift = False
    for tid in target_ids:
        target_cls = targets.get(tid)
        if target_cls is None:
            continue
        drift, diff_text, status = _diff_one_target(target_cls, paths, tid)
        if drift:
            any_drift = True
            stderr.print(f"[yellow]{status}[/]")
            _emit_diff(diff_text, stdout_console=stdout)

    return 1 if any_drift else 0


def _cmd_log(args: argparse.Namespace) -> int:
    paths = _resolve_paths(args)
    tid = TargetId(value=args.target)

    repo_path = paths.target_repo(tid)
    if not (repo_path / ".git").exists():
        sys.stdout.write("(no state-repo yet)\n")
        return 0
    log = GitRepo(repo_path).log()
    for entry in log:
        sys.stdout.write(f"{entry['sha'][:8]}  {entry['subject']}\n")
    return 0


def _cmd_adopt(args: argparse.Namespace) -> int:
    args.on_conflict = f"prefer={args.target}"
    args.profile = None
    return _cmd_merge(args)


def _cmd_discard(args: argparse.Namespace) -> int:  # noqa: PLR0911 — guard chain
    """Restore live target files to their state-repo HEAD content.

    Refuses to run off a TTY without ``--yes`` (so a CI shell or an
    auto-login hook can never silently overwrite a hand-edited file).
    Honors PARTIAL ownership: ``~/.claude.json`` keeps any keys outside
    the assembler's ``owned_keys`` even though the rest of the file is
    rewritten.
    """
    paths = _resolve_paths(args)
    targets = TargetRegistry.discover()
    stderr = Console(stderr=True, highlight=False)

    try:
        tid = TargetId(value=args.target)
    except ValueError as e:
        stderr.print(f"[red]error[/]: {e}")
        return 2
    target_cls = targets.get(tid)
    if target_cls is None:
        stderr.print(f"[red]error[/]: no registered target {args.target!r}")
        return 2

    repo_path = paths.target_repo(tid)
    if not (repo_path / ".git").exists():
        stderr.print(
            f"[red]error[/]: no state-repo for {tid.value} at {repo_path}; "
            "run `chameleon merge` first to capture HEAD"
        )
        return 2
    repo = GitRepo(repo_path)
    if repo.head_commit() is None:
        stderr.print(f"[red]error[/]: state-repo for {tid.value} has no HEAD commit")
        return 2

    if not args.yes:
        if not _stdin_is_a_tty():
            stderr.print(
                "[red]error[/]: refusing to discard without confirmation off a TTY; "
                "pass [bold]--yes[/] to confirm non-interactively"
            )
            return 2
        prompt = (
            f"overwrite live {tid.value} files with state-repo HEAD? "
            "any uncommitted edits will be lost"
        )
        if not _confirm_discard(prompt, console=stderr):
            stderr.print("[dim]aborted[/]")
            return 0

    # Apply per-FileSpec, honoring ownership semantics.
    for spec in target_cls.assembler.files:
        head_bytes = repo.read_at_head(spec.repo_path)
        if head_bytes is None:
            # File not tracked at HEAD — skip rather than truncate live.
            continue
        live_path = Path(os.path.expanduser(spec.live_path))
        live_path.parent.mkdir(parents=True, exist_ok=True)

        if spec.ownership is FileOwnership.PARTIAL:
            # Decode the HEAD blob as JSON, replay only the owned-key subset
            # back into live; everything else live keeps wins.
            head_obj = json.loads(head_bytes) if head_bytes.strip() else {}
            if not isinstance(head_obj, dict):
                head_obj = {}
            partial_owned_write(
                live_path,
                owned_keys=spec.owned_keys,
                update=_make_partial_layer(head_obj, spec.owned_keys),
            )
            continue

        # FULL ownership → atomic temp + rename.
        tmp = live_path.with_suffix(live_path.suffix + ".chameleon-tmp")
        tmp.write_bytes(head_bytes)
        tmp.replace(live_path)

    stderr.print(f"[green]restored[/] {tid.value} from HEAD")
    return 0


def _classify_resolution_status(  # noqa: PLR0912 — single linear walk over targets+codecs
    paths: StatePaths,
    targets: TargetRegistry,
    n1: Neutral,
) -> dict[str, str]:
    """Compute per-resolution hash_status by replaying the change-walker.

    Returns a ``{path_key: status}`` dict where status is one of:
    ``current`` (the live ChangeRecord's hash matches the stored
    decision_hash), ``stale`` (the record exists but the hash drifted),
    or ``missing-record`` (no current ChangeRecord at this path — the
    disagreement is gone, the GC pass would prune this entry on the
    next merge).

    Operates against live target files exactly as the merge engine does
    on disassemble, but never writes anything; safe to run from a
    listing command.
    """
    if not n1.resolutions.items:
        return {}

    if paths.lkg.exists():
        n0 = Neutral.model_validate(load_yaml(paths.lkg))
    else:
        n0 = Neutral(schema_version=1)

    ctx = TranspileCtx()
    per_target_neutral: dict[TargetId, Neutral] = {}
    for tid in targets.target_ids():
        target_cls = targets.get(tid)
        if target_cls is None:
            continue
        live: dict[str, bytes] = {}
        for spec in target_cls.assembler.files:
            live_path = Path(os.path.expanduser(spec.live_path))
            if live_path.exists():
                live[spec.repo_path] = live_path.read_bytes()
        domains, _ = target_cls.assembler.disassemble(live, ctx=ctx)
        target_neutral = Neutral(schema_version=1)
        for codec_cls in target_cls.codecs:
            if codec_cls.domain not in domains:
                continue
            try:
                fragment = codec_cls.from_target(domains[codec_cls.domain], ctx)
            except NotImplementedError:
                continue
            setattr(target_neutral, codec_cls.domain.value, fragment)
        per_target_neutral[tid] = target_neutral

    records = walk_changes(n0, n1, per_target_neutral)
    by_path = {render_change_path(r): r for r in records}

    status: dict[str, str] = {}
    for path_key, resolution in n1.resolutions.items.items():
        record = by_path.get(path_key)
        if record is None:
            status[path_key] = "missing-record"
            continue
        if compute_decision_hash(record) == resolution.decision_hash:
            status[path_key] = "current"
        else:
            status[path_key] = "stale"
    return status


def _cmd_resolutions(args: argparse.Namespace) -> int:  # noqa: PLR0911 — guard chain
    """`chameleon resolutions list|clear` — operator escape hatch."""
    paths = _resolve_paths(args)
    stderr = Console(stderr=True, highlight=False)

    if not paths.neutral.exists():
        stderr.print(f"[red]error[/]: neutral file not found: {paths.neutral}")
        return 2
    n1 = Neutral.model_validate(load_yaml(paths.neutral))

    if args.op == "list":
        targets = TargetRegistry.discover()
        status_map = _classify_resolution_status(paths, targets, n1)
        items = n1.resolutions.items
        if not items:
            sys.stdout.write("(no stored resolutions)\n")
            return 0
        # Plain-text table — keeps stdout pipe-friendly for tests and
        # operator scripting. Columns: path | decided_at | decision |
        # decision_target | hash_status. Width is computed once over the
        # data so columns line up without truncation.
        rows: list[tuple[str, str, str, str, str]] = []
        for path_key, resolution in sorted(items.items()):
            target_str = (
                resolution.decision_target.value if resolution.decision_target is not None else "-"
            )
            rows.append(
                (
                    path_key,
                    resolution.decided_at.isoformat(),
                    resolution.decision.value,
                    target_str,
                    status_map.get(path_key, "missing-record"),
                )
            )
        headers = ("path", "decided_at", "decision", "decision_target", "hash_status")
        widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        sys.stdout.write(fmt.format(*headers) + "\n")
        sys.stdout.write(fmt.format(*("-" * w for w in widths)) + "\n")
        for row in rows:
            sys.stdout.write(fmt.format(*row) + "\n")
        return 0

    # op == "clear"
    items = n1.resolutions.items
    if not items:
        sys.stdout.write("(no stored resolutions to clear)\n")
        return 0

    if args.path is not None:
        if args.path not in items:
            stderr.print(
                f"[red]error[/]: no resolution at {args.path!r}; "
                f"run `chameleon resolutions list` to see available paths"
            )
            return 2
        prompt = f"clear resolution at {args.path!r}?"
        targets_to_clear = [args.path]
    else:
        prompt = f"clear all {len(items)} stored resolution(s)?"
        targets_to_clear = list(items.keys())

    if not args.yes:
        if not _stdin_is_a_tty():
            stderr.print(
                "[red]error[/]: refusing to clear resolutions without confirmation off a TTY; "
                "pass [bold]--yes[/] to confirm non-interactively"
            )
            return 2
        if not _confirm_discard(prompt, console=stderr):
            stderr.print("[dim]aborted[/]")
            return 0

    remaining = {k: v for k, v in items.items() if k not in targets_to_clear}
    n1.resolutions = Resolutions(items=remaining)
    paths.neutral.write_text(
        dump_yaml(n1.model_dump(mode="json", exclude_none=False)),
        encoding="utf-8",
    )
    sys.stdout.write(f"cleared {len(targets_to_clear)} resolution(s)\n")
    return 0


_DISPATCH = {
    "init": _cmd_init,
    "merge": _cmd_merge,
    "status": _cmd_status,
    "diff": _cmd_diff,
    "log": _cmd_log,
    "adopt": _cmd_adopt,
    "discard": _cmd_discard,
    "validate": _cmd_validate,
    "doctor": _cmd_doctor,
    "targets": _cmd_targets,
    "resolutions": _cmd_resolutions,
}


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(argv) if argv is not None else sys.argv[1:]
    parser = _build_parser()
    if not args_list or args_list in (["--help"], ["-h"]):
        parser.print_help()
        return 0
    args = parser.parse_args(args_list)
    if args.cmd is None:
        parser.print_help()
        return 0
    handler = _DISPATCH.get(args.cmd)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
