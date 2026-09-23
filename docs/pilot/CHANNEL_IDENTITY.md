# What RAD/RA1/RA2 actually are — measured 2026-09-23

## The question

Every corroboration claim in this project ("seen on 2+ channels", "independent receivers agree")
rests on an assumption about what the three channel files actually are. Two possibilities:

1. Three spatially separate receiver antennas, each at its own fixed offset — genuine
   independent-geometry corroboration, comparable to stereo vision.
2. Three frequency bands recorded simultaneously by one antenna at one position — multi-frequency
   confirmation from a single vantage point, not independent geometry.

This was never checked against the file headers before now. The answer changes what agreement
across channels is actually evidence of.

## The evidence

Full text headers were dumped for all 3 channels of all 4 delivered jobs
(`_parse_text_header` in `parsers/spr.py`, read directly — not through the parser's derived
fields). The pattern is identical across every job:

| Field | RAD | RA1 | RA2 | Same or different across channels |
|---|---|---|---|---|
| `SPR_CHANNEL_NUM` | 0 | 1 | 2 | different (just an index) |
| `SPR_SAMPLING_INTERVAL` | 100 | 200 | 400 | different — exact 1:2:4 cascade |
| `ANTENNA_TYPE` | 4 | 3 | 2 | different (numeric code) |
| `ANTENNA_TYPE_DETECTED` | eQUANTUM | eQUANTUM | eQUANTUM | **identical, every job** |
| `RADAR_HEAD_MARK` | 3 | 3 | 3 | **identical, every job** |
| `TVG_START_GAIN` | varies | varies | varies | different per channel, per job |
| `SPR_SHAFT_INTERVAL` (wheel encoder) | 0.025 | 0.025 | 0.025 | **identical, every job** |
| `ACQUISITION_DATE`/`TIME` | same | same | same | **identical within a job** |
| `LINE_ID` | 01 | 01 | 01 | **identical within a job** |
| `INSTRUMENT` | `SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0` | same | same | **identical** |
| `RECEIVER_SPECS` | `SUBSURFACE_IMAGING_SYSTEMS` | same | same | **identical** |
| `SPR_LINE_SEPARATION` | 1 | 1 | 1 | identical on all 3 channels, all 4 jobs |

## The conclusion

**RAD/RA1/RA2 are three simultaneous frequency channels from one antenna head at one position —
not three spatially separate receivers.**

The decisive fields:

- `ANTENNA_TYPE_DETECTED: eQUANTUM` is identical on all three channels, every job — the software
  itself identifies one physical antenna unit as the source of all three files. If these were
  three separately-positioned receiver antennas, this field would either differ or not be
  reported once for all three.
- `RADAR_HEAD_MARK: 3` is likewise identical across all three — same physical head.
- `SPR_SAMPLING_INTERVAL` forms an exact 100/200/400 ps cascade — the classic implementation of a
  multi-frequency antenna array, where a lower center frequency needs a coarser/longer time
  window and a higher one needs finer resolution. This is a time-domain sampling difference, not
  a spatial one.
- `ANTENNA_TYPE` (the numeric code) differs per channel, every job, in the same 4/3/2 pattern —
  consistent with three different frequency-tuned antenna elements inside one eQuantum housing,
  each carrying its own internal type code.
- **Correction, 2026-09-23** — an earlier draft of this document claimed "no position-offset or
  separation field exists in any of the 12 headers." That was wrong, and a re-check caught it:
  `SPR_LINE_SEPARATION` does exist, in all 12 files. It reads `1` in every one of them, identical
  across the three channels of every job. Two things follow. First, the honest version of the
  original point is narrower: no field varies *per channel* in a way that would encode a spatial
  offset between RAD, RA1 and RA2 — a genuine multi-offset array would need exactly that to
  reconcile positions, and no such field exists. Second, `SPR_LINE_SEPARATION` most plausibly
  means spacing between adjacent *survey lines* (parallel passes of a 3D grid — `LINE_ID` is `01`
  here), not spacing between receivers inside one head; that reading rests on the field's name and
  on its being constant across channels, not on vendor documentation, which does not exist for
  this format. Treat it as an inference, and re-check it if a multi-line job ever arrives.
- `SPR_SHAFT_INTERVAL`, `LINE_ID`, and the acquisition timestamp are identical across all three
  channels of a job — all three were logged from the same wheel-encoder trace stream on the same
  physical pass.

This also matches "eQuantum" as a real US Radar antenna product name (the "Quantum Imager" is
publicly described as a simultaneous tri-frequency system in one housing) — though the
`INSTRUMENT`/`RECEIVER_SPECS` fields separately and consistently say `SUBSURFACE_IMAGING_SYSTEMS
SPRSCAN_3D`. The most consistent reading of both facts together: a Subsurface Imaging Systems
SPRScan 3D data-acquisition unit paired with a US-Radar-made eQuantum antenna head — a modular
DAU/antenna relationship, not necessarily the DAU vendor being wrong or the antenna vendor being
wrong. This vendor-relationship question is a secondary curiosity; it does not change the
structural conclusion above, which is answerable from the header alone regardless of who made
which part.

## What this means for every corroboration claim in this codebase

**"Independent receivers" was the wrong description and has been corrected everywhere it
appeared** (`studio/corroborate.py`, `core/contracts.py`, `evidence/quality.py`,
`studio/interpret.py`, `studio/server.py`, `studio/session.py`, `reason/prompt.py`, the three
`reason/prompts/v1_*.txt` templates sent to the LLM, `docs/pilot/CAPABILITY_STATEMENT.md`, and the
corresponding tests).

**What agreement across 2+ channels still proves, honestly:**

- Each channel is sampled at its own time resolution and fitted independently (separate RANSAC
  hyperbola fit per channel), so agreement is not one channel's own processing artefact repeating
  itself.
- A reflection strong enough to produce a consistent, matching hyperbola across two or three
  *different frequency bands* is stronger evidence than a single fit on one band — frequency-
  dependent effects (resonance, attenuation, penetration) make a coincidental cross-band match on
  noise unlikely.

**What it does *not* prove, and previously implied that it did:**

- It is not independent-geometry corroboration. All channels share one antenna position, one
  pass, one wheel-encoder reading, one ground-coupling condition, and one operator technique. A
  systematic error common to the whole pass — a mis-set permittivity, a coupling anomaly, an
  encoder slip at that exact spot — could show up identically on every channel at once, the way it
  never could for two receivers towed at genuinely different offsets.
- It is not the same evidentiary strength as stereo/multi-static geometric corroboration, which
  the phrase "independent receivers" implied throughout the client-facing capability statement and
  the LLM prompt text.

The pass/fail behavior of `studio/corroborate.py`'s clustering (2+ channels required, permittivity
agreement required) is unchanged by this finding — only the stated justification and the
represented evidentiary strength are corrected. Whether QL-B1 should still require only 2 agreeing
frequency channels, given this weaker-than-previously-claimed form of corroboration, is a judgement
call worth a second look (not changed in this pass — flagged here for that decision).
