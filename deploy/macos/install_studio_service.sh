#!/bin/sh
# Run GPR Studio as a background service for this Mac user: starts at login, restarts if it stops.
# Logs: ~/Library/Logs/GPRStudio/studio.log. Remove: launchctl bootout gui/$(id -u)/com.gprstudio.studio
set -eu
REPO=$(cd "$(dirname "$0")/../.." && pwd)
LABEL=com.gprstudio.studio
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/GPRStudio"
mkdir -p "$LOGS" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$REPO/.venv/bin/python</string><string>-m</string><string>studio</string></array>
  <!-- Dataset/, annotations/ and .env are found from here -->
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <!-- restarts after a crash, or while Postgres is still starting after a reboot -->
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/studio.log</string>
  <key>StandardErrorPath</key><string>$LOGS/studio.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "GPR Studio service installed; log: $LOGS/studio.log"
