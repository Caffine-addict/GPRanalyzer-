#!/usr/bin/env bash
# Start the built GPR Studio with a throwaway data folder and check it actually serves.
# Usage: packaging/smoke_test.sh   (after building with PyInstaller; works on macOS, Linux, Windows Git Bash)
set -euo pipefail
exe="dist/GPR Studio/GPR Studio"
[[ -f "$exe.exe" ]] && exe="$exe.exe"
home="$(mktemp -d)"
GPR_STUDIO_HOME="$home" BROWSER=true "$exe" > "$home/console.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do
  url="$(grep -o 'http://127.0.0.1:[0-9]*' "$home/studio.log" 2>/dev/null | tail -1 || true)"
  [[ -n "$url" ]] && break
  sleep 1
done
[[ -n "${url:-}" ]] || { echo "app did not start"; cat "$home/console.log"; exit 1; }
for path in / /static/js/app.js /api/jobs; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$url$path")"
  echo "$path -> $code"
  [[ "$code" == 200 ]] || exit 1
done
[[ -f "$home/config.yaml" && -d "$home/Lines" ]] || { echo "data folder not prepared"; exit 1; }
echo "smoke test passed"
