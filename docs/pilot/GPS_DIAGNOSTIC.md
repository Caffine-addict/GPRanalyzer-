# GPS diagnostic — measured 2026-09-15/17

## The finding, in one sentence

On the four survey lines delivered so far, the onboard GPS reported a healthy fix while
recording almost no movement on three of them — and on the one line where it did move, it
disagreed with the wheel-encoder distance by 12%, growing to more than a metre of drift by
the far end.

## The numbers

| Line | Wheel-encoder length | GPS start→end distance | Distinct GPS fixes logged | Reported fix quality |
|---|---|---|---|---|
| Job_0696 | 9.47 m | 1.17 m | 7 of 60 | HDOP 0.8, DGPS fix |
| Job_0703 | 9.58 m | 0.41 m | 4 of 45 | HDOP 0.9, DGPS fix |
| Job_0720 | 9.80 m | 0.19 m | 2 of 28 | HDOP 0.8, DGPS fix |
| Job_0730 | 9.53 m | 8.84 m | 25 of 26 | HDOP 0.9, DGPS fix |

The wheel encoder is trustworthy: all four lines land at a consistent, physically plausible
9.5–9.8 m regardless of what the GPS did. It's the reference these numbers are checked against.

**The reported fix quality does not distinguish the broken lines from the working one.** All
four report the same HDOP band and the same DGPS fix type. Nothing in the receiver's own status
output would tell an operator, in the field, that three of the four lines were about to log a
frozen position.

Even on Job_0730 — the one line that moved — the disagreement is not random noise, it's a
steady drift: the GPS-derived position is 3 cm short of the wheel encoder at the start of the
line and 1.17 m long by the end. That shape (small error near the start, growing steadily)
is consistent with a scale/calibration mismatch between the GPS's own distance measurement and
the wheel encoder, not with ordinary GPS jitter.

## What this means practically

- A target's position **along the line** (chainage, from the wheel encoder) is reliable on all
  four lines and is what the current target-list export reports.
- A target's position **on the site plan** (a real map coordinate) cannot currently be produced
  from the onboard GPS with any confidence — on 3 of 4 lines there is no usable signal at all,
  and on the 4th the error is large enough to place a target more than a metre from where it
  actually is.
- This is why the current target-list export (`picks.csv`) reports chainage only, with no
  coordinate column — see `docs/pilot/CAPABILITY_STATEMENT.md`.

## Recommended next step (for the company, not something we can resolve from the data alone)

1. **Check the receiver's operating mode.** A frozen position with a healthy reported fix is a
   classic symptom of a static-hold or position-averaging mode being left on during a moving
   survey. If that's the cause, it's a device setting, not a hardware fault.
2. **A quick field test would confirm it in under two minutes**: log the GPS once standing
   still, and once walking a known ~10 m distance, and compare. If the frozen-coordinate pattern
   shows up in both, it's a setting; if only when stationary, the receiver is working as
   designed and the walking failures need a different explanation (antenna placement, sky view,
   interference).
3. Until this is resolved, chainage-only remains the honest way to report position — reporting
   a GPS-derived coordinate on these lines would be reporting a number nobody has verified as
   accurate.
