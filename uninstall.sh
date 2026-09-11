#!/bin/sh
# Remove the LaunchAgent and every file the installer left on this machine.
LABEL="local.session-viewer"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/.local/share/session-viewer"    # the copied script
rm -rf "$HOME/.local/state/session-viewer"    # the auth token
rm -f "$HOME/Library/Logs/session-viewer.log" # the detached log
echo "removed. a reinstall mints a new token, so reload any tab left open."
