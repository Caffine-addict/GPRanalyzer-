"""Tests for studio/gps_georef.py and studio/geo_export.py — GPS fallback, KML and DXF."""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
import pytest
from pyproj import Transformer

from core import geo
from studio import geo_export, gps_georef
from studio.gps_georef import Fix

ORIGIN = (13.0115, 77.5615)  # where the delivered lines were recorded


def fixes_along(metres_per_trace: float, traces: list[int]) -> list[Fix]:
    return [Fix(t, *geo.from_local(t * metres_per_trace, 0.0, *ORIGIN)) for t in traces]


# --- GPS fallback -------------------------------------------------------------------------------

def test_a_gps_that_follows_the_wheel_gives_a_track_with_its_measured_disagreement() -> None:
    # The GPS reports 0.95x the wheel distance at every fix: 9.5 m of wheel -> 0.475 m short at the end.
    fixes = fixes_along(0.025 * 0.95, list(range(1, 381, 15)))
    result = gps_georef.assess(fixes, trace_spacing_m=0.025, line_length_m=9.5)
    assert result.kind == "track" and len(result.reference_points) == 3
    assert result.max_disagreement_m == pytest.approx(0.025 * 0.05 * (fixes[-1].trace - 1), abs=0.01)
    assert all(p.accuracy_m == result.max_disagreement_m for p in result.reference_points)
    assert result.reference_points[0].chainage_m == pytest.approx(0.025)  # chainage from the fix's own trace
    assert "absolute accuracy is unverified" in result.reason


def test_a_frozen_gps_gives_a_location_and_never_a_line() -> None:
    fixes = [Fix(t, *geo.from_local(0.02 * i, 0.0, *ORIGIN)) for i, t in enumerate(range(1, 380, 40))]
    result = gps_georef.assess(fixes, trace_spacing_m=0.025, line_length_m=9.5)
    assert result.kind == "location" and result.reference_points == []
    assert result.radius_m is not None and result.radius_m >= 9.5
    assert "direction is unknown" in result.reason


def test_one_fix_is_not_enough_for_anything() -> None:
    assert gps_georef.assess(fixes_along(0.025, [5]), 0.025, 9.5).kind == "none"


def test_malformed_gps_rows_are_skipped_not_repaired(tmp_path: Path) -> None:
    (tmp_path / gps_georef.GPS_FILE).write_text(
        "5\n00000,00001,13.0115,77.5615,948,0.8\n00001,xx,13.0116,77.5616\n00002,00009,0,0\n00003,00012,13.0117,77.5617\n")
    assert [f.trace for f in gps_georef.read_fixes(tmp_path)] == [1, 12]


def test_fixes_exactly_on_the_lat_lon_boundary_are_kept(tmp_path: Path) -> None:
    # The range check is -90 <= lat <= 90 and -180 <= lon <= 180 — an off-by-one would drop these.
    (tmp_path / gps_georef.GPS_FILE).write_text(
        "2\n00000,00001,90,77.5615,948,0.8\n00001,00002,13.0115,180,948,0.8\n")
    assert [f.trace for f in gps_georef.read_fixes(tmp_path)] == [1, 2]


def test_read_fixes_sorts_rows_logged_out_of_trace_order(tmp_path: Path) -> None:
    (tmp_path / gps_georef.GPS_FILE).write_text(
        "2\n00000,00012,13.0117,77.5617,948,0.8\n00001,00001,13.0115,77.5615,948,0.8\n")
    assert [f.trace for f in gps_georef.read_fixes(tmp_path)] == [1, 12]


def test_a_ratio_exactly_at_min_movement_still_counts_as_a_track(monkeypatch: pytest.MonkeyPatch) -> None:
    # The check is "ratio >= MIN_MOVEMENT"; a ratio sitting exactly on the boundary must still pass.
    fixes = fixes_along(0.025, [1, 380])
    wheel = (fixes[-1].trace - fixes[0].trace) * 0.025
    monkeypatch.setattr(gps_georef, "distance_m", lambda *a, **k: wheel * gps_georef.MIN_MOVEMENT)
    result = gps_georef.assess(fixes, trace_spacing_m=0.025, line_length_m=9.5)
    assert result.kind == "track"


def test_a_perfectly_agreeing_track_still_gets_a_nonzero_accuracy_floor() -> None:
    # Disagreement is 0 m here, but a reference point can never claim perfect accuracy.
    fixes = fixes_along(0.025, list(range(1, 381, 15)))
    result = gps_georef.assess(fixes, trace_spacing_m=0.025, line_length_m=9.5)
    assert result.max_disagreement_m == 0.0
    assert all(p.accuracy_m == 0.01 for p in result.reference_points)


def test_a_locations_centre_is_the_mean_of_its_fixes_not_the_first() -> None:
    # The wheel covers ~9.5 m over these traces; the GPS barely moves (frozen receiver), so this
    # stays a "location" — but its centre must average all three fixes, not just the first.
    fixes = [Fix(1, 13.0, 77.0), Fix(190, 13.0, 77.0), Fix(380, 13.00003, 77.0)]
    result = gps_georef.assess(fixes, trace_spacing_m=0.025, line_length_m=9.5)
    assert result.kind == "location"
    assert result.centre == pytest.approx((13.00001, 77.0), abs=1e-7)


_JOB_0730 = Path("Dataset/DSU_GPR_Files/Job_0730")


@pytest.mark.skipif(not _JOB_0730.exists(), reason="SPR dataset not present")
def test_the_real_lines_are_classed_as_the_gps_diagnostic_measured() -> None:
    kinds = {}
    for job in ("Job_0696", "Job_0703", "Job_0720", "Job_0730"):
        kinds[job] = gps_georef.assess(gps_georef.read_fixes(_JOB_0730.parent / job), 0.025, 9.5).kind
    assert kinds == {"Job_0696": "location", "Job_0703": "location", "Job_0720": "location", "Job_0730": "track"}


# --- exports ------------------------------------------------------------------------------------

def collection() -> dict[str, object]:
    lon0, lat0 = ORIGIN[1], ORIGIN[0]
    end = geo.from_local(9.5, 0.0, *ORIGIN)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon0, lat0], [end[1], end[0]]]},
         "properties": {"kind": "radar_line", "id": "Job_X", "position_method": "onboard_gps_track",
                        "position_error_m": 0.93, "warnings": ["GPS short by 7%"]}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon0, lat0]},
         "properties": {"kind": "claim", "identity": "pipe <b>&</b> 'duct'", "review_status": "proposed",
                        "position_error_m": 0.93}},
    ]}


def test_utm_zones_are_chosen_from_the_site() -> None:
    assert geo_export.utm_epsg(77.56, 13.01) == 32643  # Bangalore
    assert geo_export.utm_epsg(70.13, 23.07) == 32642  # Gandhidham
    assert geo_export.utm_epsg(151.2, -33.9) == 32756  # southern hemisphere


def test_kml_is_valid_xml_lon_lat_and_escapes_untrusted_text() -> None:
    kml = geo_export.to_kml(collection(), "Test & <title>")
    root = ET.fromstring(kml)
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    names = [n.text for n in root.iter("{http://www.opengis.net/kml/2.2}name")]
    assert "pipe <b>&</b> 'duct' — proposed" in names  # survived as literal text
    coords = root.find(".//k:Point/k:coordinates", ns).text  # type: ignore[union-attr]
    lon, lat, _ = (float(v) for v in coords.split(","))
    assert (lon, lat) == pytest.approx((ORIGIN[1], ORIGIN[0]))
    assert "warning: GPS short by 7%" in kml.decode()


def test_dxf_is_in_utm_metres_and_round_trips_to_the_original_position() -> None:
    content, epsg = geo_export.to_dxf(collection())
    assert epsg == 32643
    doc = ezdxf.read(io.StringIO(content.decode()))
    line = next(e for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE")
    (x0, y0), (x1, y1) = [p[:2] for p in line.get_points()]
    # Metres on the UTM grid: 9.5 m of ground times the zone's scale factor here (1.00056 at
    # Bangalore, 2.56 degrees east of zone 43's central meridian).
    scale = geo_export.grid_scale(ORIGIN[1], ORIGIN[0], epsg)
    assert scale == pytest.approx(1.000555, abs=1e-5)
    assert ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 == pytest.approx(9.5 * scale, abs=0.002)
    back = Transformer.from_crs(epsg, 4326, always_xy=True).transform(x0, y0)
    assert back == pytest.approx((ORIGIN[1], ORIGIN[0]), abs=1e-7)
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert {"RADAR_LINE", "CLAIM_PROPOSED", "NOTES"} <= layers
    note = next(e.dxf.text for e in doc.modelspace() if e.dxf.layer == "NOTES")
    assert "EPSG:32643" in note and f"{scale:.5f}" in note


def test_kml_claim_style_depends_on_review_status() -> None:
    confirmed = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.0, 13.0]},
         "properties": {"kind": "claim", "identity": "pipe", "review_status": "confirmed"}}]}
    proposed = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.0, 13.0]},
         "properties": {"kind": "claim", "identity": "pipe", "review_status": "proposed"}}]}
    assert "#claim_confirmed" in geo_export.to_kml(confirmed, "t").decode()
    assert "#claim_proposed" in geo_export.to_kml(proposed, "t").decode()


def test_kml_draws_a_circle_for_a_line_location_not_a_point() -> None:
    lon, lat = ORIGIN[1], ORIGIN[0]
    located = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {"kind": "line_location", "id": "Job_X", "uncertainty_radius_m": 20.0}}]}
    kml = geo_export.to_kml(located, "t")
    root = ET.fromstring(kml)
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    ring = root.find(".//k:Polygon//k:coordinates", ns)
    assert ring is not None
    pts = [tuple(float(v) for v in p.split(",")[:2]) for p in ring.text.split()]
    radii = [geo.distance_m(lat, lon, py, px) for px, py in pts]
    assert all(r == pytest.approx(20.0, abs=0.2) for r in radii)


def test_dxf_layers_vendor_and_target_origin_control_naming() -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.56, 13.01]},
         "properties": {"kind": "vendor_callout", "utility": "Water Pipe"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.56, 13.01]},
         "properties": {"kind": "target", "origin": "interpreter pick"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.56, 13.01]},
         "properties": {"kind": "target", "origin": "detector candidate"}},
    ]}
    content, _epsg = geo_export.to_dxf(fc)
    doc = ezdxf.read(io.StringIO(content.decode()))
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert {"VENDOR_Water_Pipe", "TARGET_PICK", "TARGET_CANDIDATE"} <= layers


def test_dxf_refuses_data_spanning_two_utm_zones() -> None:
    two_sites = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.56, 13.01]}, "properties": {"kind": "target"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [70.13, 23.07]}, "properties": {"kind": "target"}},
    ]}
    with pytest.raises(ValueError, match="spans UTM zones"):
        geo_export.to_dxf(two_sites)


def test_control_characters_in_free_text_never_break_the_kml() -> None:
    fc = collection()
    fc["features"][1]["properties"]["identity"] = "void\x01bad\x0bname"  # type: ignore[index]
    root = ET.fromstring(geo_export.to_kml(fc, "title\x02"))  # must parse
    names = [n.text for n in root.iter("{http://www.opengis.net/kml/2.2}name")]
    assert "voidbadname — proposed" in names


def test_download_names_cannot_break_the_header() -> None:
    from studio.geo_routes import _safe_filename

    assert _safe_filename('Job"\r\nX-Evil: 1') == "Job___X-Evil__1"
    assert _safe_filename("Job_0730") == "Job_0730" and _safe_filename("") == "export"
