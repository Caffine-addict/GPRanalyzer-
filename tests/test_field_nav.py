"""The phone field view's navigation arithmetic (studio/static/js/geo_nav.js), run under Node and
checked against the server's own geodesic distance and known bearings."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.geo import distance_m

_MODULE = Path(__file__).resolve().parents[1] / "studio" / "static" / "js" / "geo_nav.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")

LAT, LON = 12.9701495, 77.6419588  # company Set 1


def _run(expression: str) -> object:
    script = f"import * as nav from {json.dumps(_MODULE.as_uri())}; console.log(JSON.stringify({expression}));"
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize(("dlat", "dlon"), [(0.0001, 0.0), (0.0, 0.0001), (0.00003, -0.00004), (-0.002, 0.003)])
def test_distance_matches_the_server_geodesic(dlat: float, dlon: float) -> None:
    js = _run(f"nav.distanceM({LAT}, {LON}, {LAT + dlat}, {LON + dlon})")
    assert js == pytest.approx(distance_m(LAT, LON, LAT + dlat, LON + dlon), abs=0.005)  # 5 mm over 400 m


@pytest.mark.parametrize(("dlat", "dlon", "bearing", "point"),
                         [(0.0001, 0, 0, "N"), (0, 0.0001, 90, "E"), (-0.0001, 0, 180, "S"), (0, -0.0001, 270, "W")])
def test_bearings_point_the_right_way(dlat: float, dlon: float, bearing: float, point: str) -> None:
    got = _run(f"[nav.bearingDeg({LAT}, {LON}, {LAT + dlat}, {LON + dlon}), "
               f"nav.compassPoint(nav.bearingDeg({LAT}, {LON}, {LAT + dlat}, {LON + dlon}))]")
    assert got[0] == pytest.approx(bearing, abs=0.01) and got[1] == point


def test_inside_gps_error_the_crew_is_told_to_tape_out_not_shown_an_arrow() -> None:
    item = f"{{lat: {LAT + 0.00002}, lon: {LON}, horizontal_error_m: 0.2, tape: '4.00 m along L1 from its start'}}"
    near = _run(f"nav.guidance({{lat: {LAT}, lon: {LON}, accuracy: 4}}, {item})")  # no timestamp: treated as fresh
    assert near["arrived"] and "tape" in near["text"] and "4.00 m along L1" in near["text"]
    far = _run(f"nav.guidance({{lat: {LAT - 0.0003}, lon: {LON}, accuracy: 4}}, {item})")
    assert not far["arrived"] and far["text"].split(" m ")[1].startswith("N")


NOW = 1_000_000


def _guide(here: str, now: int = NOW) -> dict:
    item = f"{{lat: {LAT + 0.0003}, lon: {LON}, horizontal_error_m: 0.2, tape: '4.00 m along L1 from its start'}}"
    return _run(f"nav.guidance({here}, {item}, {now})")


def test_a_poor_fix_never_says_you_have_arrived() -> None:
    # 33 m away with ±40 m: the old rule called this "arrived"
    got = _guide(f"{{lat: {LAT}, lon: {LON}, accuracy: 40, timestamp: {NOW}}}")
    assert not got["usable"] and "too weak" in got["text"] and "tape out" in got["text"]


def test_a_stale_fix_stops_guiding() -> None:
    got = _guide(f"{{lat: {LAT + 0.0003}, lon: {LON}, accuracy: 4, timestamp: {NOW - 30_000}}}")
    assert not got["usable"] and "GPS lost 30 s ago" in got["text"]


def test_a_good_fresh_fix_guides() -> None:
    got = _guide(f"{{lat: {LAT}, lon: {LON}, accuracy: 4, timestamp: {NOW - 2000}}}")
    assert got["usable"] and not got["arrived"] and got["metres"] == pytest.approx(33.2, abs=0.2)


@pytest.mark.parametrize(("bearing", "heading", "angle"), [(90, 90, 0), (90, 0, 90), (0, 90, 270), (10, 350, 20)])
def test_the_arrow_turns_with_the_phone(bearing: float, heading: float, angle: float) -> None:
    # facing east with the target due east: straight ahead, not "right"
    assert _run(f"nav.arrowAngle({bearing}, {heading})") == pytest.approx(angle)
