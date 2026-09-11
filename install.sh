#!/bin/sh
# Install the viewer as a LaunchAgent.
#
# viewer.py is copied out of this directory first. A LaunchAgent runs without
# the TCC grants a terminal session inherits, so it cannot read ~/Documents,
# ~/Desktop or ~/Downloads — leaving the script there fails at spawn with
# "Operation not permitted". ~/.local/share is not protected.
set -e

SRC="$(cd "$(dirname "$0")" && pwd)/viewer.py"
DEST_DIR="$HOME/.local/share/session-viewer"
DEST="$DEST_DIR/viewer.py"
PLIST="$HOME/Library/LaunchAgents/local.session-viewer.plist"
LOG="$HOME/Library/Logs/session-viewer.log"
LABEL="local.session-viewer"

[ -f "$SRC" ] || { echo "no viewer.py next to $0" >&2; exit 1; }

mkdir -p "$DEST_DIR" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cp "$SRC" "$DEST"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <!-- /usr/bin/python3 is the Command Line Tools interpreter (3.9). viewer.py
       targets that syntax deliberately, so this path does not drift when a
       homebrew python is upgraded or removed. -->
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$DEST</string>
  </array>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <!-- Detached, this is the only place an error will surface. -->
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>

  <key>WorkingDirectory</key>
  <string>/tmp</string>

  <key>EnvironmentVariables</key>
  <dict>
    <!-- launchd gives an agent a minimal PATH; keep the system tools findable. -->
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>

  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

printf 'waiting for the service'
i=0
while [ $i -lt 25 ]; do
  if curl -fsS -m 1 http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
    echo
    echo "up: http://127.0.0.1:8765"
    echo "log: $LOG"
    exit 0
  fi
  printf '.'
  sleep 1
  i=$((i + 1))
done

echo
echo "did not come up. Last lines of $LOG:" >&2
tail -20 "$LOG" >&2
exit 1
