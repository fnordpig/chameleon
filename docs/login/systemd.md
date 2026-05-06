# Login-time Chameleon on Linux (systemd user units)

Place these at `~/.config/systemd/user/`:

`chameleon.service`:

```ini
[Unit]
Description=Chameleon — sync neutral config to AI agent targets
After=default.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/uv run chameleon merge --on-conflict=fail --quiet
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

`chameleon.timer`:

```ini
[Unit]
Description=Run chameleon on login (and every 4h thereafter)

[Timer]
OnBootSec=30s
OnUnitActiveSec=4h
Unit=chameleon.service

[Install]
WantedBy=timers.target
```

Enable:

```sh
systemctl --user enable --now chameleon.timer
```

Or run once at login via PAM (`pam_systemd`) plus
`systemctl --user start chameleon.service` from your shell rc.

Surface failures the same way as the launchd setup —
`chameleon doctor --notices-only` from your interactive shell rc.
