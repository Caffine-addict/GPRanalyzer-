# Day 3 — pilot session script

Framing: **a pilot / validation session** on the interpreter's workstation, against the four
survey lines already delivered — not a live capability demo, not automated detection, not
mapping. Read `CAPABILITY_STATEMENT.md` before the session if you haven't; lead with it if
anyone asks what this does before you get to show them.

## Day 3, before anyone arrives

1. **Check for a stale server.** `lsof -i :8500` — if anything's listening, check how long it's
   been up (`ps -p <pid> -o lstart,command`) before assuming it's today's. Kill and restart if
   in doubt; a process from a prior day silently serves old code (this bit us once already —
   see the vault's Decision Log, 2026-09-16).
2. **Start fresh**: `.venv/bin/python -m studio`.
3. **Re-run the gate**, quickly, so you're not finding out about a broken test live:
   `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check . && .venv/bin/python -m mypy core sources parsers preprocess detect render evidence risk reason store pipeline api reports studio simulate reference`
4. **Pick the targets you'll actually show** (from tonight's dry run) and mark them in the
   Studio if not already saved.
5. **Pre-warm interpretations**: `.venv/bin/python scripts/prewarm_interpretations.py` — against
   the *same* server process from step 2. Do not restart the Studio after this; the cache is
   in-memory and restarting empties it. If the session runs long and someone wants to interpret
   a target you didn't pre-warm, say so plainly rather than let a stall look like a crash — a
   real Groq rate-limit failure looks identical to "no answer from the model" and is handled
   gracefully, but still reads badly if nobody expects it.
6. Have `docs/pilot/GPS_DIAGNOSTIC.md` and `docs/pilot/RISK_WEIGHTS_REVIEW.md` open or printed —
   both are things you found by checking, worth stating plainly if the session goes well.

## The session itself

1. **Open with the capability statement**, one sentence: *"This measures a GPR line and helps
   read it faster and more consistently — it doesn't automatically detect utilities, and it
   doesn't put anything on a map yet. Both of those need things we don't have: labelled ground
   truth, and a working GPS on this receiver."* Naming the limits first is stronger than being
   asked about them.
2. **Open a real line** (`Job_0703` is the one with a corroborated target — start there).
   Show: the radargram, the automated candidate boxes (`Candidates` button), the processing
   chain.
3. **Pick a target, fit its velocity**, show depth flip from `estimated` to `calibrated` once
   the hyperbola is fitted from its own curvature — this is the one number in the whole system
   that's a genuine measurement, not an assumption, and it's worth pausing on.
4. **Interpret it.** Read the written half aloud, specifically the hedge (e.g. "cannot be
   distinguished between these possibilities") — this is the honesty behaviour to point to when
   anyone asks "does it know what it is." It doesn't, and it says so.
5. **Export the target list** (`Export CSV`). Show chainage, class, PAS 128 grade — and say,
   plainly, that there's no coordinate column, and why: hand them `GPS_DIAGNOSTIC.md`. This is
   a genuine finding about their equipment, not a software gap, and it's the strongest thing in
   the whole session if you frame it as a finding rather than an apology.
6. **If risk weights come up**: they exist, they're reasoned (excavation consequence per
   class), and they haven't been checked by a utilities person yet — `RISK_WEIGHTS_REVIEW.md`
   has the detail if wanted.
7. **Close on next steps**, not apologies: labels (company Q#1/#6) unlock training a real
   detector; the GPS finding unlocks real map positions once someone checks the receiver
   setting; both are the company's decision, not blocked on more of our engineering.

## If something breaks live

- **Groq stalls or errors**: the panel says "no answer from the model — the measurements above
  still stand." That's correct behaviour, not a bug — say so and move on with the measured
  numbers, which are unaffected.
- **A job won't open**: don't try to debug it live. Move to a job you tested in the dry run.
- **Someone asks for live/real-time detection**: that's the expectation gap. Point back to the
  opening line in step 1 — it was said first, on purpose.
