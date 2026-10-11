#!/bin/sh
# Run the live-pipeline API (127.0.0.1:8000) and the dashboard (127.0.0.1:5173, the built app) as
# background services for this Mac user: start at login, restart if they stop.
# Logs: ~/Library/Logs/GPRStudio/{api,dashboard}.log
# Remove: launchctl bootout gui/$(id -u)/com.gprstudio.api  (and com.gprstudio.dashboard)
# Rebuild the dashboard after changing it: (cd dashboard && npm run build), then restart its service.
set -eu
REPO=$(cd "$(dirname "$0")/../.." && pwd)
LOGS="$HOME/Library/Logs/GPRStudio"
PY="$REPO/.venv/bin/python"
mkdir -p "$LOGS" "$HOME/Library/LaunchAgents"

install() {  # label, working directory, log name, command (run through the project's Python)
  label=$1; dir=$2; log=$3; command=$4
  plist="$HOME/Library/LaunchAgents/$label.plist"
  # Started through the project's Python: macOS lets that reach the Desktop folder; a shell or
  # node started by launchd it does not ("Operation not permitted"). Children inherit the permission.
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-c</string><string>$command</string></array>
  <key>WorkingDirectory</key><string>$dir</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/$log.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$log.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "$label installed"
}

install com.gprstudio.api "$REPO" api \
  "import subprocess, sys; sys.exit(subprocess.call([sys.executable, '-m', 'uvicorn', 'api.server:app', '--host', '127.0.0.1', '--port', '8000']))"
install com.gprstudio.dashboard "$REPO/dashboard" dashboard \
  "import subprocess, sys; sys.exit(subprocess.call(['/opt/homebrew/bin/npx', 'vite', 'preview', '--host', '127.0.0.1', '--port', '5173', '--strictPort']))"
