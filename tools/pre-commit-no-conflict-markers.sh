#!/usr/bin/env bash
# pre-commit hook: reject commits that introduce raw git conflict markers.
#
# Operator-installed (opt-in) — see AGENTS.md "Pre-commit hook" section.
# To install:
#   ln -s ../../tools/pre-commit-no-conflict-markers.sh .git/hooks/pre-commit
#
# Why: chameleon Wave-2 had a near-miss where a commit was authored with
# raw <<<<<<< / ======= / >>>>>>> markers in tracked files (caught and
# discarded before reaching main). This hook fails the commit FAST so
# the operator gets immediate feedback rather than a CI surprise.
#
# Scope: only the diff that is staged for this commit (added or modified
# lines). Pre-existing markers elsewhere in the tree are out of scope —
# this hook is about preventing new ones.
#
# Patterns are assembled from character repetition at runtime so this
# script does not contain literal conflict-marker lines that would
# otherwise self-match if it were ever scanned.

set -euo pipefail

LT=$(printf '%.0s<' {1..7})
EQ=$(printf '%.0s=' {1..7})
GT=$(printf '%.0s>' {1..7})
# Diff lines start with "+", so we look for added lines that are
# conflict markers. The leading "+" (not "++") is what git diff uses
# for added content; "++ " prefixes the file header which we ignore
# via the --diff-filter=ACMR scope. We use [+] (a one-element char
# class) instead of \+ because awk treats + as the ERE quantifier and
# different awk implementations disagree on how to escape it.
PATTERN="^[+](${LT} |${EQ}\$|${GT} )"

# Limit to staged content. --cached gives us the index vs HEAD diff.
# --no-color so our regex isn't dodging ANSI escapes.
# -U0 because we only care about changed lines, not context.
# --diff-filter=ACMR scopes to Added/Copied/Modified/Renamed (skip
# deletions and type changes — they can't introduce markers).
DIFF=$(git diff --cached --no-color -U0 --diff-filter=ACMR || true)

if [ -z "$DIFF" ]; then
  exit 0
fi

# Find offending lines and the file they belong to. We rebuild the
# file context as we walk the diff because grep alone loses it.
OFFENDERS=$(printf '%s\n' "$DIFF" | awk -v pat="$PATTERN" '
  /^diff --git / {
    # diff --git a/path b/path  -> grab the b/path
    file = $4
    sub(/^b\//, "", file)
    next
  }
  $0 ~ pat {
    print file ": " $0
  }
')

if [ -n "$OFFENDERS" ]; then
  echo "ERROR: staged changes contain raw git conflict markers:" >&2
  echo "" >&2
  printf '%s\n' "$OFFENDERS" >&2
  echo "" >&2
  echo "Resolve the conflict and re-stage before committing." >&2
  echo "(To bypass for an emergency, use: git commit --no-verify)" >&2
  exit 1
fi

exit 0
