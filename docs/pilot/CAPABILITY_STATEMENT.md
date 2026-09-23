# What this is, and what it isn't

For briefing the client before day 3. The commitment made was broad — "AI-assisted / automated
GPR analysis," no specifics — and everything below is comfortably inside that. Nothing here is
damage control; it's an accurate description of a real, working tool.

## What it is

**An interpreter's workstation** that measures a GPR survey line and helps a person read it
faster and more consistently than doing it by eye alone:

- **Automated candidate detection.** Classical signal processing finds hyperbola-shaped regions
  on a line before anyone looks at it — the starting point a surveyor works from, not a
  finished answer.
- **Measured depth, with its provenance stated.** When a target's own hyperbola can be fitted,
  its depth comes from a wave velocity measured on that target, not assumed from an operator
  dial — and the system says so (`calibrated`) rather than letting an assumption pass as a
  measurement (`estimated`).
- **Cross-channel corroboration — 5 targets, on the current numbers.** Three frequency channels
  (RAD/RA1/RA2) from one antenna head see the same ground from the same position. Of 15 distinct
  objects found across the four lines, 13 appear on two or more channels, but only **5** also
  agree on the permittivity they imply — and only those 5 are corroboration rather than
  coincidence, because two channels that disagree about the ground did not measure the same
  thing. Since all three channels share one antenna position and pass, even those 5 are
  multi-frequency confirmation, not independently-positioned receiver corroboration
  (`docs/pilot/CHANNEL_IDENTITY.md`). Both corrections are recent: an earlier version of this
  statement said "independent receivers", and the reporting script was quoting the 13 rather
  than the 5. Raw output for every figure: `docs/pilot/VERIFICATION_2026-09-23.md`.
- **Plain-language interpretation**, from a language model that is explicitly instructed to say
  when it cannot tell one thing from another (a small stone from a small pipe, for instance) —
  and does, in practice, say exactly that rather than guessing with confidence.
- **Findings reported against PAS 128, and reported as ungraded.** Every finding is measured
  against the ladder a client procures survey work in — and comes back `ungraded`, with the
  reason and with what would have to change. No PAS 128 level is supportable here: QL-B1 needs
  a second geophysical *technique* (three frequency channels of GPR are one), QL-B1/B2 are
  accuracy bands nothing has been validated against, and every QL-B level needs a georeferenced
  position this survey's GPS cannot provide. Saying so is the point — an in-house confidence
  score that quietly implied a survey grade would be worse than no grade at all.
  See `docs/pilot/CHANNEL_IDENTITY.md` and `evidence/quality.py`.
- **A measured diagnostic finding about the survey equipment itself**: the onboard GPS on the
  delivered lines cannot currently place a target on a map (see `GPS_DIAGNOSTIC.md`) — found by
  actually checking it against the wheel encoder, not assumed to work because the receiver
  reported a healthy fix.

## What it is not

- **Not an automated detector.** There is no trained model behind any of this — candidate
  detection is classical signal processing, and classification comes from measured rules
  (amplitude, spacing, polarity), not a learned model. `weights/best.pt` does not exist.
- **Not real-time.** Results appear per line after export/import, in about a second — not while
  the antenna is moving. There is no live device protocol; that answer hasn't come from the
  company yet.
- **Not a mapping tool, on this data.** The target list exports chainage (distance along the
  line) and never a map coordinate, because the onboard GPS cannot currently be trusted to
  place anything — see `GPS_DIAGNOSTIC.md`. This is a finding from checking the actual data, not
  a design limitation that could be coded around.
- **Not validated against ground truth.** Nothing found so far has been checked against an
  excavation or a utility record. 5 targets are corroborated across frequency channels that also
  agree on the ground they imply, which is real evidence, but corroboration is not verification.
- **No measured benefit claimed for the detector's own changes.** A previous claim that a fix to
  the direct-wave handling "recovered real signal", and that corroboration "roughly doubled", was
  re-derived on 2026-09-23 and **withdrawn**: like-for-like, candidate boxes rose 155→166 while
  *credible* targets fell 92→88, and corroboration as a share of credible moved 12.0%→13.6%. The
  fix is still right on physics; what was wrong was claiming its benefit had been measured.
- **Not able to tell a stone from a cable from one line.** This is physics, not a software gap:
  both draw the same hyperbola. The system says so rather than guessing.

## The honest one-line summary

It turns a GPR line into a measured, graded, explained shortlist for a person to act on faster
— it does not replace the person, and it does not put anything on a map yet.
