#!/bin/sh
# Tailscale for this Mac user, without admin rights: tailscaled in userspace-networking mode as a
# login service. It can still `serve` the Studio over HTTPS to the tailnet, which is all we need.
# Remove: launchctl bootout gui/$(id -u)/com.gprstudio.tailscaled
set -eu
TAILSCALED=$(brew --prefix)/bin/tailscaled
LABEL=com.gprstudio.tailscaled
STATE="$HOME/Library/Application Support/GPRStudio/tailscale"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/GPRStudio"
[ -x "$TAILSCALED" ] || { echo "install it first: brew install tailscale" >&2; exit 1; }
mkdir -p "$STATE" "$LOGS" "$HOME/Library/LaunchAgents"
chmod 700 "$STATE"  # holds this machine's tailnet key
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$TAILSCALED</string>
    <string>--tun=userspace-networking</string>
    <string>--statedir=$STATE</string>
    <string>--socket=$STATE/tailscaled.sock</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOGS/tailscaled.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tailscaled.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "tailscaled running; socket: $STATE/tailscaled.sock"
