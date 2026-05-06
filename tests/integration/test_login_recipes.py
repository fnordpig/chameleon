"""Verify the login-time recipe docs (`docs/login/*.md`) stay in sync with the
real CLI.

These tests are the only thing keeping copy/paste recipes from rotting:

* the embedded plist/unit/shell snippets must be syntactically valid for their
  host system,
* every `chameleon` invocation embedded in a recipe must use only flags the
  current `chameleon --help` actually supports,
* the `--on-conflict=...` value baked into each recipe must be one of the
  strategies `on_conflict_to_strategy` accepts.

If any of these fire, the doc has drifted from the CLI — fix the doc, not the
test.
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from chameleon import cli
from chameleon.merge.resolve import on_conflict_to_strategy

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_LOGIN = REPO_ROOT / "docs" / "login"


# --------------------------------------------------------------------------- #
# Fenced-code-block extraction
# --------------------------------------------------------------------------- #


_FENCE_RE = re.compile(
    r"^```(?P<lang>[A-Za-z0-9_+-]*)\s*\n"  # opening fence + optional lang
    r"(?P<body>.*?)"  # body
    r"^```\s*$",  # closing fence on its own line
    re.MULTILINE | re.DOTALL,
)


def _fences(md_text: str) -> list[tuple[str, str]]:
    """Return [(lang, body), ...] for every fenced code block in `md_text`."""
    return [(m.group("lang").lower(), m.group("body")) for m in _FENCE_RE.finditer(md_text)]


def _fence_by_lang(md_text: str, lang: str) -> str:
    """Return the body of the first fenced block whose lang matches `lang`."""
    for found_lang, body in _fences(md_text):
        if found_lang == lang:
            return body
    msg = f"no fenced code block with lang={lang!r} in document"
    raise AssertionError(msg)


def _all_fences_with_lang(md_text: str, lang: str) -> list[str]:
    return [body for found_lang, body in _fences(md_text) if found_lang == lang]


# --------------------------------------------------------------------------- #
# CLI surface — built once
# --------------------------------------------------------------------------- #


def _parser() -> argparse.ArgumentParser:
    return cli._build_parser()


def _resolve_against_cli(argv: list[str]) -> argparse.Namespace:
    """Parse `argv` with the live chameleon parser; assert it succeeds.

    `argparse` raises SystemExit on unknown flags or unknown subcommands;
    we let that propagate so the test fails with a useful message.
    """
    parser = _parser()
    return parser.parse_args(argv)


def _extract_chameleon_argv(argv: list[str]) -> list[str]:
    """Given a full argv (e.g. ["uv", "run", "chameleon", "merge", ...])
    return the slice after the `chameleon` token, or raise if absent."""
    if "chameleon" not in argv:
        msg = f"no `chameleon` token in argv {argv!r}"
        raise AssertionError(msg)
    idx = argv.index("chameleon")
    return argv[idx + 1 :]


def _on_conflict_value(argv: list[str]) -> str:
    """Pull the --on-conflict value out of an argv (supports both
    --on-conflict=X and --on-conflict X form)."""
    for i, tok in enumerate(argv):
        if tok.startswith("--on-conflict="):
            return tok.split("=", 1)[1]
        if tok == "--on-conflict" and i + 1 < len(argv):
            return argv[i + 1]
    msg = f"no --on-conflict flag in argv {argv!r}"
    raise AssertionError(msg)


# --------------------------------------------------------------------------- #
# launchd
# --------------------------------------------------------------------------- #


def _launchd_program_arguments(plist_xml: str) -> list[str]:
    """Parse a plist string and return the ProgramArguments array as a list of
    Python strings, by walking the dict children pairwise (key/value)."""
    root = ET.fromstring(plist_xml)
    # plist > dict
    dict_el = root.find("dict")
    assert dict_el is not None, "plist has no <dict>"
    children = list(dict_el)
    program_args: list[str] | None = None
    i = 0
    while i < len(children):
        node = children[i]
        if node.tag == "key" and (node.text or "") == "ProgramArguments":
            value = children[i + 1]
            assert value.tag == "array", (
                f"ProgramArguments must be followed by <array>, got <{value.tag}>"
            )
            program_args = [(s.text or "") for s in value.findall("string")]
            break
        i += 1
    assert program_args is not None, "plist has no ProgramArguments key"
    return program_args


def test_launchd_plist_is_valid_xml_and_uses_live_cli_flags() -> None:
    md = (DOCS_LOGIN / "launchd.md").read_text(encoding="utf-8")
    plist_xml = _fence_by_lang(md, "xml")

    # 1. Parses as XML.
    program_args = _launchd_program_arguments(plist_xml)

    # 2. Points at uv-run-chameleon.
    assert "uv" in program_args[0], (
        f"plist ProgramArguments[0] should be a uv binary, got {program_args[0]!r}"
    )
    assert program_args[1] == "run"
    assert program_args[2] == "chameleon"

    # 3. The flags after `chameleon` resolve against the live parser.
    chameleon_argv = _extract_chameleon_argv(program_args)
    ns = _resolve_against_cli(chameleon_argv)
    assert ns.cmd == "merge"

    # 4. --on-conflict value is one the resolver accepts.
    raw = _on_conflict_value(chameleon_argv)
    on_conflict_to_strategy(raw)  # raises on invalid input


def test_launchd_doctor_followup_uses_live_cli_flags() -> None:
    """The plist doc embeds a follow-up shell snippet calling
    `chameleon doctor --notices-only --quiet`. Confirm those flags resolve."""
    md = (DOCS_LOGIN / "launchd.md").read_text(encoding="utf-8")
    sh_snippet = _fence_by_lang(md, "sh")
    assert "chameleon doctor" in sh_snippet
    # Strip everything before `chameleon` in the line, drop the trailing
    # `|| true` and conditional prefix.
    line = next(line for line in sh_snippet.splitlines() if "chameleon doctor" in line)
    chameleon_segment = line[line.index("chameleon doctor") :]
    # Cut at `||` or `&&` so we don't try to lex `|| true`.
    for sep in ("||", "&&"):
        if sep in chameleon_segment:
            chameleon_segment = chameleon_segment.split(sep, 1)[0]
    argv = shlex.split(chameleon_segment)
    chameleon_argv = _extract_chameleon_argv(argv)
    ns = _resolve_against_cli(chameleon_argv)
    assert ns.cmd == "doctor"


# --------------------------------------------------------------------------- #
# systemd
# --------------------------------------------------------------------------- #


_EXEC_START_RE = re.compile(r"^ExecStart\s*=\s*(?P<cmd>.+)$", re.MULTILINE)


def _systemd_units(md_text: str) -> list[tuple[str, str]]:
    """Return every `ini`/`systemd` fence as (label, body) pairs.

    A list (not a dict) because systemd recipes commonly include multiple
    units that all open with `[Unit]` — keying on the first section header
    would silently collide.
    """
    out: list[tuple[str, str]] = []
    for i, body in enumerate(_all_fences_with_lang(md_text, "ini")):
        m = re.search(r"^\[(?P<sec>[^\]]+)\]", body, re.MULTILINE)
        # Suffix with the index so the label is always unique even when two
        # units both open with `[Unit]`.
        label = f"{m.group('sec') if m else 'unit'}#{i}"
        out.append((label, body))
    return out


def test_systemd_unit_parses_as_ini() -> None:
    """The .service / .timer fences must parse as INI sections.

    `configparser` rejects unit files because [Install] WantedBy=
    multi-value semantics aren't quite INI, so we use a relaxed
    "every non-blank non-comment line is either [Section] or key=value"
    rule that matches systemd's own loader behavior closely enough.
    """
    md = (DOCS_LOGIN / "systemd.md").read_text(encoding="utf-8")
    units = _systemd_units(md)
    assert units, "no `ini` fenced blocks found in systemd.md"

    for label, body in units:
        in_section = False
        for line_num, raw in enumerate(body.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_section = True
                continue
            assert in_section, (
                f"systemd unit {label!r} has key/value before any [Section] "
                f"on line {line_num}: {raw!r}"
            )
            assert "=" in line, (
                f"systemd unit {label!r} line {line_num} is neither a section "
                f"header nor a key=value: {raw!r}"
            )


def test_systemd_execstart_uses_live_cli_flags() -> None:
    md = (DOCS_LOGIN / "systemd.md").read_text(encoding="utf-8")
    units = _systemd_units(md)
    # At least one unit body must contain ExecStart= for chameleon merge.
    found = False
    for _label, body in units:
        m = _EXEC_START_RE.search(body)
        if not m:
            continue
        found = True
        argv = shlex.split(m.group("cmd").strip())
        # The recipe form is `/usr/local/bin/uv run chameleon merge ...`
        chameleon_argv = _extract_chameleon_argv(argv)
        ns = _resolve_against_cli(chameleon_argv)
        assert ns.cmd == "merge", f"ExecStart resolved to subcommand {ns.cmd!r}, expected merge"
        on_conflict_to_strategy(_on_conflict_value(chameleon_argv))
    assert found, "no ExecStart= line found in any systemd unit fence"


# --------------------------------------------------------------------------- #
# zlogin shell snippet
# --------------------------------------------------------------------------- #


def _bash_available() -> bool:
    return shutil.which("bash") is not None


@pytest.mark.skipif(not _bash_available(), reason="bash not on PATH")
def test_zlogin_shell_snippets_are_syntactically_valid_bash(tmp_path: Path) -> None:
    md = (DOCS_LOGIN / "zlogin.md").read_text(encoding="utf-8")
    snippets = _all_fences_with_lang(md, "sh")
    assert snippets, "no `sh` fenced blocks found in zlogin.md"
    for i, body in enumerate(snippets):
        f = tmp_path / f"snippet-{i}.sh"
        f.write_text(body, encoding="utf-8")
        result = subprocess.run(
            ["bash", "-n", str(f)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"zlogin snippet {i} failed bash -n: {result.stderr!r}\nbody:\n{body}"
        )


def _chameleon_invocations(snippet: str) -> list[list[str]]:
    """Pull every `chameleon ...` invocation out of a shell snippet.

    Splits on shell control operators (`&&`, `||`, `;`, `|`, `{`, `}`) so a
    single line like `cmd && chameleon merge ... || echo` yields just the
    chameleon argv.
    """
    # Strip line continuations so multi-line `... \\\n    ...` becomes one line.
    flat = snippet.replace("\\\n", " ")
    pieces = re.split(r"\|\||&&|;|\||\{|\}", flat)
    out: list[list[str]] = []
    for raw_piece in pieces:
        piece = raw_piece.strip()
        if not piece.startswith("chameleon"):
            continue
        # shlex won't tolerate the unbalanced `>/dev/null` etc. — strip
        # redirections aggressively before lex.
        piece = re.sub(r"\s+>/?\S+", "", piece)
        piece = re.sub(r"\s+2?>&?\d+", "", piece)
        try:
            argv = shlex.split(piece)
        except ValueError:
            continue
        out.append(argv)
    return out


def test_zlogin_chameleon_invocations_use_live_cli_flags() -> None:
    md = (DOCS_LOGIN / "zlogin.md").read_text(encoding="utf-8")
    snippets = _all_fences_with_lang(md, "sh")
    invocations: list[list[str]] = []
    for body in snippets:
        invocations.extend(_chameleon_invocations(body))
    assert invocations, "no `chameleon ...` invocations found in zlogin.md"
    for argv in invocations:
        # argv[0] is "chameleon"; pass the rest to the parser.
        ns = _resolve_against_cli(argv[1:])
        assert ns.cmd in {"merge", "doctor"}, (
            f"unexpected chameleon subcommand in zlogin recipe: {ns.cmd!r}"
        )
        if ns.cmd == "merge":
            on_conflict_to_strategy(_on_conflict_value(argv[1:]))


# --------------------------------------------------------------------------- #
# Cross-recipe invariants
# --------------------------------------------------------------------------- #


_VALID_ON_CONFLICT_BARE = {"fail", "keep", "prefer-neutral", "prefer-lkg"}


def _every_recipe_merge_argv() -> list[tuple[str, list[str]]]:
    """Return [(recipe_label, chameleon_merge_argv), ...] for every recipe."""
    out: list[tuple[str, list[str]]] = []

    # launchd
    md = (DOCS_LOGIN / "launchd.md").read_text(encoding="utf-8")
    plist_xml = _fence_by_lang(md, "xml")
    program_args = _launchd_program_arguments(plist_xml)
    out.append(("launchd", _extract_chameleon_argv(program_args)))

    # systemd
    md = (DOCS_LOGIN / "systemd.md").read_text(encoding="utf-8")
    for label, body in _systemd_units(md):
        m = _EXEC_START_RE.search(body)
        if not m:
            continue
        argv = shlex.split(m.group("cmd").strip())
        if "chameleon" not in argv:
            continue
        chameleon_argv = _extract_chameleon_argv(argv)
        if chameleon_argv and chameleon_argv[0] == "merge":
            out.append((f"systemd[{label}]", chameleon_argv))

    # zlogin
    md = (DOCS_LOGIN / "zlogin.md").read_text(encoding="utf-8")
    for body in _all_fences_with_lang(md, "sh"):
        for argv in _chameleon_invocations(body):
            if len(argv) >= 2 and argv[1] == "merge":
                out.append(("zlogin", argv[1:]))

    return out


def test_every_login_recipe_invokes_chameleon_merge() -> None:
    """Every login-time recipe must call `chameleon merge` (the documented
    login-time entry point) at least once."""
    for label in ("launchd.md", "systemd.md", "zlogin.md"):
        md = (DOCS_LOGIN / label).read_text(encoding="utf-8")
        assert "chameleon merge" in md or "chameleon</string>" in md, (
            f"{label} does not invoke `chameleon merge`"
        )


def test_every_recipe_uses_a_valid_on_conflict_value() -> None:
    """Every `chameleon merge` invocation in every recipe sets a documented
    `--on-conflict` value, and that value parses via `on_conflict_to_strategy`."""
    merge_argvs = _every_recipe_merge_argv()
    assert merge_argvs, "found no `chameleon merge` invocations across recipes"
    for label, argv in merge_argvs:
        raw = _on_conflict_value(argv)
        # Bare value is either one of the named strategies, or `prefer=<name>`.
        assert raw in _VALID_ON_CONFLICT_BARE or raw.startswith("prefer="), (
            f"{label}: --on-conflict={raw!r} is not a documented form"
        )
        # And the resolver actually accepts it.
        on_conflict_to_strategy(raw)


def test_every_recipe_argv_resolves_against_chameleon_help() -> None:
    """Bulk check: every chameleon argv we lifted out parses under the live
    argparse without raising. argparse exits non-zero with a SystemExit on
    unknown flags or unknown subcommands."""
    for label, argv in _every_recipe_merge_argv():
        try:
            _resolve_against_cli(argv)
        except SystemExit as e:  # pragma: no cover — failure path is the assert
            pytest.fail(f"{label}: argv {argv!r} failed to parse: SystemExit({e.code})")
