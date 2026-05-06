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
        <string>--on-conflict=fail</string>
        <string>--quiet</string>
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

`--on-conflict=fail` exits non-zero on conflict. launchd swallows stderr
to its log file most operators don't watch. To surface conflicts on next
interactive shell, add this to your shell rc:

```sh
command -v chameleon >/dev/null && chameleon doctor --notices-only --quiet || true
```

`chameleon doctor` reports any pending `LoginNotice` records the merge
engine wrote when it failed. Acknowledge them with
`chameleon doctor --clear-notices` after addressing the conflict.
