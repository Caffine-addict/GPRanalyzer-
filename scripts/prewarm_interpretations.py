#!/usr/bin/env python3
"""Interpret every existing pick on every job, once, against a running Studio.

Groq's org-wide tokens-per-minute limit was hit live during testing on ~10 findings — in a
client session, an interpretation stalling to "no answer from the model" reads as the product
failing. The Studio already caches interpretations by evidence fingerprint
(studio/server.py's _interpretation_cache), so run this once before anyone arrives and the
session replays from cache; only a genuinely new pick during the demo itself hits the API live.

The cache lives on app.state, in memory, per process — this only helps if it is run against
the SAME server process the session will actually use. Do not restart the Studio after running
this.

Usage (Studio must already be running):
    .venv/bin/python -m studio &
    .venv/bin/python scripts/prewarm_interpretations.py
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from studio import picks as pick_store
from studio import session

_BASE_URL = "http://127.0.0.1:8500"


def main() -> int:
    try:
        urllib.request.urlopen(_BASE_URL, timeout=3)
    except urllib.error.URLError:
        print(f"nothing answering at {_BASE_URL} — start the Studio first (.venv/bin/python -m studio)")
        return 1

    jobs = session.list_jobs()
    if not jobs:
        print("no jobs found")
        return 1

    total = warmed = failed = 0
    for job_name in jobs:
        picks = pick_store.load_picks(job_name)
        for pick in picks:
            total += 1
            url = f"{_BASE_URL}/api/jobs/{job_name}/picks/{pick.id}/interpret"
            start = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=30):
                    pass
                warmed += 1
                print(f"  {job_name}/{pick.id} ({pick.label or pick.channel}) — {time.monotonic() - start:.1f}s")
            except (urllib.error.URLError, urllib.error.HTTPError) as exc:
                failed += 1
                print(f"  {job_name}/{pick.id} — FAILED: {exc}")

    print(f"\n{warmed}/{total} interpretations cached, {failed} failed.")
    if total == 0:
        print("no picks exist yet — nothing to pre-warm. Run this again after picks are made.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
