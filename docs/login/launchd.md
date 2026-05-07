# Login-time Chameleon on macOS (launchd)

Place this file at `~/Library/LaunchAgents/io.waugh.chameleon.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.waugh.chameleon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/uv</string>
        <string>run</string>
        <string>chameleon</string>
        <string>merge</string>
        <string>--on-conflict=latest</string>
        <string>--quiet</string>
        <string>--no-warn</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>/Users/YOU</string>
    <key>StandardOutPath</key>
    <string>/Users/YOU/.local/state/chameleon/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOU/.local/state/chameleon/launchd.err</string>
</dict>
</plist>
```

Enable with `launchctl load ~/Library/LaunchAgents/io.waugh.chameleon.plist`.

## Surfacing failures

Run launchd after any dotfiles pull/install step. Clean merges stay
silent. `--on-conflict=latest` exits non-zero only when Chameleon
cannot prove a uniquely newest source in this non-interactive context.
launchd writes stderr to its log file, so add this to your shell rc if
you want a reminder to inspect Chameleon state:

```sh
command -v chameleon >/dev/null && chameleon doctor --notices-only --quiet || true
```

For an actual ambiguity, rerun `chameleon merge` from an interactive
shell so Chameleon can explain the sources and prompt you for the
resolution.
