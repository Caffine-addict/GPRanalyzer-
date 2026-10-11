#!/bin/sh
# Run deploy/backup.sh every night at 02:30 while this Mac is on (launchd runs a missed job when it
# next wakes). Log: ~/Library/Logs/GPRStudio/backup.log. Remove: launchctl bootout gui/$(id -u)/com.gprstudio.backup
# Back up to another disk with: BACKUP_DIR=/Volumes/<drive>/GPR deploy/macos/install_backup_service.sh
set -eu
REPO=$(cd "$(dirname "$0")/../.." && pwd)
LABEL=com.gprstudio.backup
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/GPRStudio"
mkdir -p "$LOGS" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <!-- started through the project's Python: macOS lets that reach the Desktop folder, /bin/bash run
       by launchd it does not ("Operation not permitted"); the script inherits the permission -->
  <key>ProgramArguments</key><array><string>$REPO/.venv/bin/python</string><string>-c</string>
    <string>import subprocess, sys; sys.exit(subprocess.call(["/bin/bash", "deploy/backup.sh"]))</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>BACKUP_DIR</key><string>${BACKUP_DIR:-$REPO/backups}</string>
  </dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>$LOGS/backup.log</string>
  <key>StandardErrorPath</key><string>$LOGS/backup.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "nightly backup scheduled (02:30) to ${BACKUP_DIR:-$REPO/backups}; log: $LOGS/backup.log"
