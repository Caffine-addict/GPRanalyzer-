# Risk weights — one-pager for sign-off

**Who should read this**: whoever knows what actually happens when each of these gets struck.
This scoring has never been reviewed by anyone with utilities experience — it was set by the
engineer building the software, reasoning from excavation consequence, not from field
experience. Read this before day 3; flag anything that looks wrong, in either direction.

## What the number means

Every target gets a class (below), a weight (0–1), and a risk score. The score decides
`LOW` / `MEDIUM` / `HIGH` on the interpretation panel and the PDF report — `HIGH` is what a
survey team acts on first. `low_max = 0.35`, `medium_max = 0.65` (so 0.65–1.0 is `HIGH`).

## The 9 classes, weight, and the reasoning behind each

| Class | Weight | Reasoning |
|---|---|---|
| `elongated_linear_target` | **1.0** | A linear run is the classic live service — a strike hits its whole length, not one point. |
| `cavities` | **0.9** | A void can collapse under load, and the polarity rule behind this call is textbook, not yet field-validated on this instrument (see below). |
| `intersecting_linear_and_point_reflector` | **0.9** | A linear reflector with a point on it — one service crossing another. Worst place to dig; two things to hit. |
| `clear_point_reflector` | **0.7** | Stands clear of the ground on a single line. Cannot tell a pipe/duct/cable from a stone from one line alone — treated as the more expensive possibility. |
| `multiple_point_reflectors` | **0.6** | Regular spacing reads as built infrastructure (rebar, a duct bank), not scattered stones. |
| `cluttered_multi_target` | **0.5** | Crowded and irregular — something is there, the line can't resolve what. |
| `low_snr_point_reflector` | **0.4** | Deliberately ranked *above* the 0.2 default: an unreadable target should get a second look, not be waved through for being hard to measure. |
| `disturbed_zone` | **0.3** | Context, not an object — says someone has dug here before. |
| `strong_high_contrast_reflector` | 0.2 (default) | Never actually produced — needs a calibrated amplitude this instrument doesn't have. Listed for completeness. |

**Escalation to HIGH**: `elongated_linear_target` or `intersecting_linear_and_point_reflector`
at confidence ≥ 0.8, when 2 or more such detections appear in the same frame. These two classes
were chosen as "utility-like" under the current 9-class taxonomy, replacing an older single
"Utility" class the escalation rule was originally written against — this mapping is itself a
judgement call worth checking.

## The one incident that shaped this

Before 2026-09-14, only 3 of the 9 classes had a set weight; the rest fell to the 0.2 default.
That included the one real, cross-channel-corroborated target found so far in the delivered
data (`Job_0703`, ~8.5 m along the line, ~1.3 m deep, seen on two channels) — it scored `LOW`.
A tool that finds a live service and calls it low-risk is worse than one that finds nothing,
because someone acts on the "low" and doesn't dig carefully. The weights above are the fix.
They have not been checked by anyone but the person who wrote them.

## Specifically flagged as unvalidated, not just unreviewed

- **The `cavities` polarity rule** (a void reflects with the same polarity as the direct pulse;
  soil/water/metal reverses it) is standard GPR interpretation practice, but has only ever been
  checked against 2–3 simulated objects in this project (see the vault's Decision Log,
  2026-09-15) — nowhere near enough to trust operationally. `cavities` is the second-highest
  weight here.
- **The utility_classes mapping** for escalation (above) is a same-day guess at which of the
  9 classes count as "utility-like," ported from an older taxonomy that had one class for this.

## What to do with this

Sign off, adjust a weight, or flag a class you disagree with — in `config.yaml`'s `risk.weights`
block, not in code. If nothing changes before day 3, the demo script and the PDF report will
carry a note that these are provisional / unvalidated, per the pilot framing.
