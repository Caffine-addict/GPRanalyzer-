# GeoJSON and map integration — plan

Status: **planned, not started** (written 2026-09-30). Closes the largest gap in
`docs/RADAR_STUDIO_PARITY.md` (map window, GPS/mapping, KML/CAD export — all P0/P1 there) using the
georeferencing method already settled in `docs/SINGLE_PASS_MAPPING.md` §d. Nothing here reopens
that decision; this is the build order and the data contract.

## Where we are

| | State |
|---|---|
| Radar lines (SPRScan jobs) | Position = chainage along the line only. The onboard GPS is measured broken (`docs/pilot/GPS_DIAGNOSTIC.md`): 3 of 4 lines log near-zero movement, the 4th drifts over 1 m from the wheel encoder. |
| Vendor SUE drawings | 1,743 utility call-outs with chainage (`_processed/sue_utilities.csv`), and 335 printed Lat/Long points at sheet starts and ends (`sue_geo_points.csv`). |
| Map view, GeoJSON, KML, DXF | None. |

**Already measured, and the reason the vendor layer can come first:** walking the printed
points in order reproduces each road's stated length on 6 of 8 roads within ~1% (Ulsoor site-1
600 m vs 600, Central site-1 841 vs 840, Dickenson 1214 vs 1235 …). Two do not: Kasturba site-1
(1,404 m vs 1,270 — extra or out-of-order points) and Ulsoor site-2 (539 m vs 610 — the last
sheet's points missing). Those two ship flagged until explained.

## Principles (carried over, not new)

- **A position says how it was obtained and how good it is**, exactly as depth does today:
  `position_method` + `position_confidence` (`calibrated` / `estimated` / `unavailable`) +
  `position_error_m`. A chainage never silently becomes a coordinate.
- **No GPS rehabilitation.** Lines are placed from surveyed reference points (§d).
- **GeoJSON per RFC 7946**: WGS 84, coordinates in **longitude, latitude** order. CAD exports use
  a projected metric CRS (below), converted at export time only.
- **The review status travels with the feature.** A model claim exported to a map is still a
  claim; only `confirmed` features may be styled as findings.

## The GeoJSON contract

One `FeatureCollection` per export; every feature carries the same core properties.

| Feature `kind` | Geometry | From |
|---|---|---|
| `vendor_callout` | Point | SUE drawings (phase 1) |
| `radar_line` | LineString | a georeferenced job (phase 3) |
| `target` | Point | an interpreter pick or a detector candidate (phase 3) |
| `claim` | Point | a reasoning-layer claim with its review status (phase 3) |
| `traced_feature` | LineString | a utility run across lines (phase 5, `SINGLE_PASS_MAPPING.md` §b) |

Core `properties`:

```
kind, id, source            # e.g. "Sky Group SUE, Ulsoor Road site-1, sheet 3"
chainage_m                  # always kept, the measured quantity
position_method             # "interpolated_from_printed_latlong" | "chainage_between_reference_points"
position_confidence         # "estimated" until reference points are survey-grade
position_error_m            # stated bound, see each phase
depth_m, depth_basis        # as in the Studio today
utility, material           # vendor label, or model/interpreter identity
review_status               # "proposed" | "confirmed" | "rejected" | null (vendor data)
read_by                     # "text" | "ocr" for vendor data
```

## Phases

### Phase 1 — vendor utilities as GeoJSON (no new data needed) · size S

- `reference/sue_geo.py`: for each drawing, order its printed points by chainage and place every
  call-out by piecewise-linear interpolation along that polyline, at its chainage.
- `position_error_m` = label offset (the draughtsman places the label a few metres from the
  utility) + interpolation error between points ~60 m apart on a curving road. Measure it rather
  than assume it: hold out each interior point, predict it from its neighbours, and report the
  distribution.
- Kasturba site-1 and Ulsoor site-2 flagged `position_confidence: "unavailable"` until their
  length mismatch is explained; OCR-read rows (`read_by: "ocr"`) kept but marked.
- Output: `_processed/sue_utilities.geojson`, listed in Survey documents.
- **Done when:** the hold-out error is measured and stated in the file's metadata, a unit test
  pins the interpolation and the lon/lat order, and the file opens correctly in QGIS or
  geojson.io.

### Phase 2 — Map panel in GPR Studio · size M

- Leaflet (BSD-2), vendored as a static file like the rest of `studio/static/` (no npm build).
- Layers: vendor utilities coloured by type; call-outs that could reach the bore depth
  (the `reaches_safe_path` status) highlighted; clicking one opens its marked PDF at that sheet.
- **Basemap: decision needed** (below). Default is none — a scale bar and grid — so the Studio
  still works offline on site.
- "Export GeoJSON" for the visible layers.
- **Done when:** a Playwright check opens the panel, toggles layers, and follows a call-out to
  its sheet; no console errors.

### Phase 3 — georeferenced radar lines · size M · **blocked on field data**

- `annotations/<job>/georef.json`: two or more reference points
  `{chainage_m, lon, lat, source, accuracy_m}` — entered in the Studio (typed, or clicked on the
  map) or imported from the topography survey. A third point near the midpoint on lines over
  ~15 m, so a bowed path shows up instead of silently biasing positions (§d).
- Positions by interpolation between reference points. `position_error_m` = the reference
  points' stated accuracy + a path-straightness term, which is measurable only when a third
  point exists.
- Picks, candidates and reviewed claims appear on the map beside the vendor layer, and export in
  the same GeoJSON.
- **Blocker:** start/end coordinates for the SPRScan lines. None exist in the delivered data.
  Needs the company's topography survey or site plan — add to `docs/COMPANY_QUESTIONS.md`.
- **Done when:** a line with three known points reproduces the middle one within its stated
  error, and every exported target carries `position_method` and `position_error_m`.

### Phase 4 — KML and DXF export · size S–M

- **KML** for Google Earth: written directly (small XML), styled by review status.
- **DXF** for CAD with `ezdxf` (MIT): one layer per utility type and per review status, in a
  projected CRS via `pyproj` (MIT). **Decision needed** on the CRS (below).
- **Done when:** both open in Google Earth / a DXF viewer at the right place, and a round-trip
  test reads back the coordinates written.

### Phase 5 — later, depends on multiple lines over one site · size L

Traced features across adjacent lines (`SINGLE_PASS_MAPPING.md` §b), the coverage and
confidence overlay (§c), and depth slices (`RADAR_STUDIO_PARITY.md`). Each needs two or more
georeferenced lines over the same ground; none is worth starting before phase 3 has real lines.

## Decisions needed before building

1. **Basemap:** none (works offline), OpenStreetMap tiles when online (needs internet on site
   and attribution; fine for internal use), or the client's site plan as an image overlay
   (best for deliverables, needs the plan). Recommendation: none by default, OSM as a toggle,
   site-plan overlay in phase 3.
2. **CAD coordinate system:** Bangalore sits in UTM zone 43N (EPSG:32643), Gandhidham–Mundra in
   42N (EPSG:32642). Use UTM per site, or the client's local grid if they have one.
3. **Where reference points come from** for radar lines: the company's topography survey (as
   `SINGLE_PASS_MAPPING.md` assumes), RTK on the day, or a site plan. This decides phase 3's
   accuracy more than any code does.
4. **Whether the vendor layer goes into client deliverables** or stays internal as a comparison.

## Dependencies to add

`leaflet` (static, phase 2), `pyproj` and `ezdxf` (phase 4). All permissively licensed, with
wheels on every platform; nothing added before its phase.
