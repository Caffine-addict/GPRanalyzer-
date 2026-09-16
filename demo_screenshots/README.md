# Demo screenshots

Real screenshots of GPR Studio running against the four delivered SPR survey lines
(`Dataset/DSU_GPR_Files`) — nothing staged or mocked. Captured 2026-09-16.

- **01_landing_job_list.png** — the workstation on load: the four survey lines, and the
  signature library of confirmed hyperbolas from the company's own deliverable sheets, used
  to compare what's on screen against known-real examples.
- **02_radargram_with_panels.png** — `Job_0703`, RAD channel, opened. One target already
  marked (green marker), with the processing chain, the velocity readout (0.0999 m/ns, from
  the file header — flagged `assumed` until a hyperbola is fitted), and the target list on
  the right.
- **03_interpretation.png** — that target interpreted. The measured half (class
  `low_snr_point_reflector`, depth 0.450 m from a velocity fitted to *this target's own*
  hyperbola, so it reads `calibrated`, QL-B2 on the PAS 128 ladder) and the written half
  (a Groq-hosted model, told to say when it cannot tell a stone from a cable — and it does:
  *"cannot be distinguished between these possibilities"*). This is the honesty behaviour
  the whole project is built around, not a cherry-picked answer.
- **04_candidates.png** — `Job_0730`, RA2 (the deep channel), automated phase-1 candidate
  boxes: hyperbola-shaped regions found by classical signal processing, before any human has
  looked at the line. This is what a surveyor starts from, not a finished answer.

## What these screenshots do not show

No screenshot here claims a map position. The onboard GPS on these four lines was diagnosed
2026-09-15 as unusable for placing a target (3 of 4 lines' receivers report a healthy fix
while the logged position never moves) — see the project's own notes for the full account.
Every depth/position/amplitude figure visible above carries an explicit `calibrated` /
`estimated` / `unavailable` label; nothing here is a number invented to look complete.

There is no trained detector behind any of this (`weights/best.pt` does not exist) — the
candidate boxes in 04 come from classical signal processing, and the class in 03 comes from
measured shape/amplitude rules, not a learned model. See the repo's own `CLAUDE.md` for the
full, current state.
