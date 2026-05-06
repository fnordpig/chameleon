# Login-time Chameleon via shell rc

For operators who don't want a launchd or systemd unit, the simplest
integration is a single line in `~/.zlogin` (or `~/.bash_profile`):

```sh
command -v chameleon >/dev/null && chameleon merge --on-conflict=keep --quiet || true
```

`--on-conflict=keep` is permissive — conflicts leave drift unresolved
silently (re-prompted on next interactive `chameleon merge`). If you
want fail-loud semantics in this hook, swap to `--on-conflict=fail`
and add a follow-up doctor check:

```sh
command -v chameleon >/dev/null && {
    chameleon merge --on-conflict=fail --quiet || \
        echo "chameleon merge failed; run: chameleon doctor"
}
```
