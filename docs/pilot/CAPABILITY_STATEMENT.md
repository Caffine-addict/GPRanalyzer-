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
- **Cross-channel corroboration.** Three independent receivers see the same ground; a target
  confirmed on two or more is real evidence in a way a single detection on one line never is.
- **Plain-language interpretation**, from a language model that is explicitly instructed to say
  when it cannot tell one thing from another (a small stone from a small pipe, for instance) —
  and does, in practice, say exactly that rather than guessing with confidence.
- **Industry-standard quality grading.** Every finding is graded on the PAS 128 / ASCE 38
  ladder — the language a client already procures survey work in — rather than an in-house
  confidence score nobody outside this project would recognise.
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
  excavation or a utility record. One target (`Job_0703`) is corroborated on two independent
  channels, which is real evidence, but corroboration is not verification.
- **Not able to tell a stone from a cable from one line.** This is physics, not a software gap:
  both draw the same hyperbola. The system says so rather than guessing.

## The honest one-line summary

It turns a GPR line into a measured, graded, explained shortlist for a person to act on faster
— it does not replace the person, and it does not put anything on a map yet.
