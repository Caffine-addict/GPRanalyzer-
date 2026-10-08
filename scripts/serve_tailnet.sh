#!/bin/sh
# Serve GPR Studio (127.0.0.1:8500) to your own Tailscale devices over HTTPS.
# `serve` reaches only devices signed in to your tailnet; never use `funnel`, which is the public internet.
set -eu
TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
[ -x "$TS" ] || { echo "Tailscale is not installed: see docs/HOSTING.md step 4" >&2; exit 1; }
"$TS" status >/dev/null || { echo "Tailscale is not signed in: open the Tailscale app and sign in" >&2; exit 1; }
"$TS" serve --bg --https=443 http://127.0.0.1:8500
"$TS" serve status
