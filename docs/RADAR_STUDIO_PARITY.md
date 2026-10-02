# GPR Studio → Radar Studio functional parity — gap table

**No code in this document.** Public feature text fetched 2026-09-23 from
usradar.com/gpr-software/radar-studio/ (quoted below, attributed by section). "GPR Studio today"
verified against the actual codebase (`studio/`, `reports/`), not recollection — command used
noted where relevant. **Priority is set from `docs/SINGLE_PASS_MAPPING.md`: live single-pass
mapping first**, because that is the stated product goal and everything else compounds on it.
This document builds *functions*, never Radar Studio's branding, icon art, or UI layout.

---

## What the vendor page actually says (verbatim, by section)

| Section | Quoted text |
|---|---|
| Features — GPS/mapping | "Integrate GPS data seamlessly to create dynamic, visual maps that add environmental context to your findings" |
| Features — processing | "Radar Studio offers many data processing options designed to improve data interpretation. The automatic settings allow for new users to instantly get usable results with only minimal training" |
| Features — 3D | "Radar Studio offers advanced 3D imaging that allows the user to precisely calibrate velocity and depth. Plain view images are displayed in the mapping window and changes are updated in real time" |
| Features — annotation | "notes, select color codes, and choose icons to be displayed on the data and in the map window. Additionally, lines can be drawn in the map window to represent utilities" |
| Features — export | "Export your finished product into a wide variety of different file formats. This can range from automatically generated reports as well as advanced CAD and Google Earth Files" |
| Features — depth slices | "Create depth slices to add greater visual context to your findings and bring your data to life" |
| Capabilities | "infinitely customizable user interface with intuitive controls and functions" |

**Not found on the page, despite being in scope for this table — flagged rather than assumed:**
*time* slices specifically (only "depth slices" is stated), and **SEG-Y** export (the page names
"CAD and Google Earth Files" and "automatically generated reports"; SEG-Y is not mentioned). Both
rows below are kept because the task scope asked for them, marked accordingly.

---

## Gap table

| Feature | Radar Studio (public claim) | GPR Studio today | Effort | Priority |
|---|---|---|---|---|
| **Processing with auto settings** | "automatic settings allow ... instantly get usable results with only minimal training" | `studio/processing.py::ProcessingChain` exposes dewow, background removal (none/mean/moving), bandpass, gain (none/agc/exponential/linear), migration, stacking — **every parameter is manual**, no default/auto profile picks settings for the operator. Confirmed: `ProcessingChain`'s dataclass defaults are all "off", not "auto-tuned". | **Small.** One function proposing a starting chain from measured signal properties (e.g. SNR-driven gain choice) — a suggestion the operator can override, never a silent choice. Read-only inference over existing signal measurements already computed. | **P2** — quality-of-life, not a live-mapping blocker; do after the mapping surface exists. |
| **Annotations: notes, colour codes, icons on data and map** | "notes, select color codes, and choose icons to be displayed on the data and in the map window" | `studio/picks.py::Pick` has `label` and `note` (free text) only — no colour or icon field, and no map window to display on. | **Medium.** Add `colour`/`icon` fields to `Pick` (a closed enum, not free text, so risk-class colouring can stay meaningful rather than becoming operator-chosen chaos); render them on the radargram now, on the map surface once it exists. | **P1**, bundled with the map window below — an icon with nowhere to render is half a feature. |
| **Map window with utility lines drawn on it** | "lines can be drawn in the map window to represent utilities" | **Does not exist.** No map surface anywhere in `studio/`. | **Large.** This is the coverage/confidence overlay from `docs/SINGLE_PASS_MAPPING.md` §c, plus the traced-feature model from §b to have something to draw *as* a line rather than a scatter of points. Depends on §d (chainage→map placement) existing first. | **P0 — the single highest-priority item in this whole table.** It is not a UI nicety here; it is the literal product goal (place findings on a topography map, flag zones needing a repeat pass). Radar Studio drawing "lines to represent utilities" is the same traced-feature concept Part 5 already specified independently. |
| **Depth slices** | "Create depth slices ... bring your data to life" | Not implemented. `render/bscan.py` renders one B-scan at a time; nothing slices across a survey volume at a fixed depth. | **Medium**, and only meaningful with **multiple parallel lines** — a depth slice is inherently a multi-line product, so it has a real dependency on the map/traced-feature work above, not just on rendering. | **P2** — genuinely useful once several lines exist over one site; premature before the map surface. |
| **Time slices** *(asked for; not found on the vendor page — see note above)* | Not confirmed as a Radar Studio feature from the fetched text. | Not implemented. | N/A until confirmed the feature exists to parity against. | **Do not build speculatively** — recommend re-checking the vendor page's full site (not just this one URL) or asking the company, rather than guessing a feature into the gap table. |
| **3D imaging with real-time velocity/depth calibration** | "advanced 3D imaging ... precisely calibrate velocity and depth ... updated in real time" | Not implemented. `studio/velocity.py` calibrates velocity/depth per *pick*, in 2D, not as a live 3D volume. | **Large**, and — per Part 5(a) — a 3D volume from single-position, single-pass data has the same directional-ambiguity limit as everything else in this table: it can render depth beautifully and still cannot show utility *direction* without cross-line data. | **P3** — real value, but downstream of the map/traced-feature work; building it first would visualise data the system cannot yet orient correctly. |
| **GPS/map integration** | "Integrate GPS data seamlessly ... dynamic, visual maps" | The onboard GPS is measured broken on this data (`docs/pilot/GPS_DIAGNOSTIC.md`) — 3 of 4 lines show near-zero logged movement, the 4th drifts >1 m from the wheel-encoder length. Positions today are chainage only. | **Already scoped**: this is exactly `docs/SINGLE_PASS_MAPPING.md` §d (chainage + surveyed reference points, not GPS). Not new effort beyond what's already planned. | **P0**, same item as the map window — they are one dependency chain, not two features. |
| **Export: automatically generated reports** | "automatically generated reports" | `reports/generate.py` exists and produces a real PDF (end-of-survey, capability caveats, escaped free text) — **but only for the live-pipeline `Finding` path**, not wired to Studio `Pick`s. `studio/server.py` exports CSV only (`/picks.csv`). | **Small.** `reports/generate.py`'s report-building logic is not pipeline-specific at the data level; a `Pick`→report adapter analogous to `studio/interpret.py`'s `Pick`→`Evidence` bridge reuses it rather than duplicating PDF logic. | **P1** — genuinely useful now, low effort, no map dependency. |
| **Export: CAD / DXF** | "advanced CAD ... Files" | Not implemented. Confirmed: no DXF/CAD library anywhere in the dependency tree or code. | **Medium.** DXF is a well-specified format with mature open libraries (e.g. `ezdxf`) — the work is mapping traced features/picks to DXF entities correctly (layers per risk class, real-world coordinates), not the file format itself. **Depends on §d** for real-world coordinates to place anything meaningfully. | **P1**, sequenced after georeferencing (§d) lands — building DXF export before there's a real coordinate to put in it produces a file with chainage-only positions, which is not what a CAD consumer expects. |
| **Export: Google Earth (KML/KMZ)** | "Google Earth Files" | Not implemented. | **Small**, once georeferencing exists — KML is a simple, well-documented XML format; the entire cost is having a real lat/lon to write, i.e. §d again. | **P1**, same dependency as CAD. |
| **Export: SEG-Y** *(asked for; not found on the vendor page — see note above)* | Not confirmed as a Radar Studio feature from the fetched text. | Not implemented. SEG-Y is a standard seismic/GPR trace interchange format; this project already parses a proprietary trace format (`parsers/spr.py`), so writing SEG-Y out is a well-scoped, independent task regardless of whether Radar Studio itself offers it. | **Small–medium**, self-contained — no dependency on georeferencing, since SEG-Y trace headers can optionally carry position but don't require it. | **P2** — real interoperability value (letting a client open our data in any standard GPR tool), but not the product's stated goal, so it sits behind the mapping work. |
| **"Infinitely customizable" UI** | Marketing language, not a specific feature | N/A | N/A | **Not a target.** This is styling/branding language, not a function — explicitly out of scope per the instruction not to copy branding or UI. |

---

## Sequencing this table produces

1. **P0 — georeferencing (§d) → map window with traced-feature lines (§c/b).** Everything else
   in the "not yet found on the page" gap either depends on this or is independent quality-of-life.
   This is not a UI-parity argument; it is Part 5's product goal restated.
2. **P1, in any order once P0 lands** — annotation colour/icon (small, pairs naturally with the
   map surface being built anyway), PDF report for picks (small, reuses `reports/generate.py`
   outright), CAD/DXF and KML export (small-medium each, share the same real-coordinate
   dependency).
3. **P2** — processing auto-settings (nice, not blocking), depth slices (needs multiple lines
   over one site to mean anything), SEG-Y export (real value, independent of the map work,
   lower urgency than anything touching the stated product goal).
4. **P3** — 3D imaging. Genuinely large, and per Part 5(a) shares 2D single-pass mapping's
   fundamental limit: it can show depth beautifully and still cannot show utility direction
   without cross-line data the traced-feature model (§b) is what actually supplies.

**Nothing in this table is implemented in this pass** — gap table only, per the instruction.
