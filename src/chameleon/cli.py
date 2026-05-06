"""Chameleon CLI — argparse subcommand router with typed parsing.

Per design spec §5.4 (strict typing): CLI arg parsing produces typed
TargetId, Domains, OnConflict values; no string match statements
downstream.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from chameleon import __version__
from chameleon._types import TargetId
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge.engine import MergeEngine, MergeRequest
from chameleon.merge.resolve import Strategy, on_conflict_to_strategy
from chameleon.schema._constants import OnConflict
from chameleon.schema.neutral import Neutral
from chameleon.state.git import GitRepo
from chameleon.state.notices import NoticeStore
from chameleon.state.paths import StatePaths
from chameleon.state.transaction import TransactionStore
from chameleon.targets._registry import TargetRegistry


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--neutral", type=str, default=None, help="path to the neutral.yaml file")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


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
        default="fail",
        help="fail | keep | prefer-neutral | prefer-lkg | prefer=<target>",
    )
    p_merge.add_argument("--profile", type=str, default=None)

    p_status = sub.add_parser("status", help="per-target drift summary")
    _add_common_args(p_status)

    p_diff = sub.add_parser("diff", help="show drift detail for one target")
    _add_common_args(p_diff)
    p_diff.add_argument("target", type=str)

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

    p_validate = sub.add_parser("validate", help="schema-validate the neutral file")
    _add_common_args(p_validate)

    p_doctor = sub.add_parser("doctor", help="environment health check")
    _add_common_args(p_doctor)
    p_doctor.add_argument("--clear-notices", action="store_true")
    p_doctor.add_argument("--notices-only", action="store_true")

    p_targets = sub.add_parser("targets", help="target plugin operations")
    _add_common_args(p_targets)
    p_targets.add_argument("op", choices=["list"])

    return parser


def _resolve_paths(args: argparse.Namespace) -> StatePaths:
    override = Path(args.neutral) if args.neutral else None
    return StatePaths.resolve(neutral_override=override)


def _cmd_init(args: argparse.Namespace) -> int:
    paths = _resolve_paths(args)
    targets = TargetRegistry.discover()

    if not paths.neutral.exists():
        paths.neutral.parent.mkdir(parents=True, exist_ok=True)
        starter = Neutral(schema_version=1)
        paths.neutral.write_text(dump_yaml(starter.model_dump(mode="json")), encoding="utf-8")
        sys.stdout.write(f"init: wrote minimal neutral at {paths.neutral}\n")

    strategy = Strategy(kind=OnConflict.KEEP)
    engine = MergeEngine(targets=targets, paths=paths, strategy=strategy)
    result = engine.merge(MergeRequest(dry_run=args.dry_run))
    sys.stdout.write(f"init: {result.summary}\n")
    return result.exit_code


def _cmd_merge(args: argparse.Namespace) -> int:
    paths = _resolve_paths(args)
    targets = TargetRegistry.discover()
    strategy = on_conflict_to_strategy(args.on_conflict)
    engine = MergeEngine(targets=targets, paths=paths, strategy=strategy)
    result = engine.merge(MergeRequest(profile_name=args.profile, dry_run=args.dry_run))
    sys.stdout.write(result.summary + "\n")
    return result.exit_code


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
    paths = _resolve_paths(args)
    targets = TargetRegistry.discover()
    engine = MergeEngine(targets=targets, paths=paths, strategy=Strategy(kind=OnConflict.KEEP))
    result = engine.merge(MergeRequest(dry_run=True))
    sys.stdout.write(result.summary + "\n")
    return 0 if "nothing to do" in result.summary else 1


def _cmd_diff(args: argparse.Namespace) -> int:
    sys.stdout.write(f"diff for {args.target}: not implemented in V0\n")
    return 0


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


def _cmd_discard(args: argparse.Namespace) -> int:
    sys.stdout.write(f"discard {args.target}: not implemented in V0\n")
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
