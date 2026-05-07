# Login-time Chameleon via shell rc

For operators who don't want a launchd or systemd unit, run Chameleon
as the last login/update step, after your dotfiles repo has pulled and
installed. Clean merges stay silent:

```sh
command -v chameleon >/dev/null && chameleon merge --on-conflict=latest --quiet --no-warn || true
```

`--on-conflict=latest` accepts the uniquely newest source when it can
prove one. Ambiguous conflicts are the only time an interactive login
prints the Chameleon resolution preamble and asks you to choose neutral,
Claude, Codex, last-known-good, target-specific, or skip. `--no-warn`
suppresses end-of-merge lossy-codec errata so ordinary login stays
quiet.

For a repo-backed neutral file, pass it explicitly and still run last:

```sh
command -v chameleon >/dev/null && {
    chameleon merge \
        --neutral "${DOTFILES_DIR}/chameleon/neutral.yaml" \
        --on-conflict=latest \
        --quiet \
        --no-warn || \
            echo "chameleon merge failed; resolve the prompt or run: chameleon doctor"
}
```
