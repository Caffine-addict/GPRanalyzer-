#!/bin/sh
# Serve GPR Studio (127.0.0.1:8500) to your own Tailscale devices over HTTPS.
# `serve` reaches only devices signed in to your tailnet; never use `funnel`, which is the public internet.
# Uses the user-mode daemon from deploy/macos/install_tailscale_service.sh if present, else the Tailscale app.
set -eu
SOCK="$HOME/Library/Application Support/GPRStudio/tailscale/tailscaled.sock"
if [ -S "$SOCK" ]; then
  ts() { tailscale --socket="$SOCK" "$@"; }
elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
  ts() { /Applications/Tailscale.app/Contents/MacOS/Tailscale "$@"; }
else
  echo "Tailscale is not running: see docs/HOSTING.md" >&2; exit 1
fi
ts status >/dev/null || { echo "Tailscale is not signed in: tailscale --socket=\"$SOCK\" up" >&2; exit 1; }
ts serve --bg --https=443 http://127.0.0.1:8500
ts serve status
