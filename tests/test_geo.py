"""Tests for core/geo.py and reference/sue_geo.py — placing things on the map from chainage."""

from __future__ import annotations

import pytest

from core import geo
from reference import sue_geo

# A straight road due east from a Bangalore-ish origin: 100 m of chainage = 100 m east.
ORIGIN = (12.97, 77.60)


def east_of_origin(metres: float) -> tuple[float, float]:
    return geo.from_local(metres, 0.0, *ORIGIN)


def test_distance_is_the_wgs84_geodesic() -> None:
    # One degree of latitude at the equator is 110,574 m on WGS 84 (a sphere would say 111,195).
    assert geo.distance_m(0, 0, 1, 0) == pytest.approx(110_574, abs=2)


def test_the_local_frame_agrees_with_the_geodesic_at_site_scale() -> None:
    lat, lon = geo.from_local(700.0, 700.0, *ORIGIN)  # ~1 km diagonal
    assert geo.distance_m(*ORIGIN, lat, lon) == pytest.approx((2 * 700**2) ** 0.5, abs=0.01)


def test_local_frame_round_trips() -> None:
    lat, lon = geo.from_local(350.0, -120.0, *ORIGIN)
    assert geo.to_local(lat, lon, *ORIGIN) == pytest.approx((350.0, -120.0), abs=1e-6)
    assert geo.distance_m(*ORIGIN, lat, lon) == pytest.approx((350**2 + 120**2) ** 0.5, rel=1e-4)


def test_interpolation_lands_proportionally_between_anchors() -> None:
    anchors = [(0.0, *ORIGIN), (100.0, *east_of_origin(100))]
    lat, lon = geo.interpolate(25.0, anchors)  # type: ignore[misc]
    assert geo.to_local(lat, lon, *ORIGIN) == pytest.approx((25.0, 0.0), abs=1e-3)


def test_outside_the_anchors_there_is_no_position_rather_than_an_extrapolation() -> None:
    anchors = [(10.0, *ORIGIN), (100.0, *east_of_origin(90))]
    assert geo.interpolate(5.0, anchors) is None and geo.interpolate(101.0, anchors) is None
    assert geo.interpolate(50.0, anchors[:1]) is None


def row(chainage: float, lat: float, lon: float, drawing: str = "Road A.pdf") -> dict[str, str]:
    return {"drawing": drawing, "sheet": "1", "lat": str(lat), "lon": str(lon), "chainage_m": str(chainage)}


def callout(chainage: str, drawing: str = "Road A.pdf") -> dict[str, str]:
    return {"drawing": drawing, "sheet": "1", "utility": "UC", "depth_m": "0.6", "chainage_m": chainage,
            "chainage_source": "plan_ticks", "status": "clear", "read_by": "text"}


STRAIGHT = [row(c, *east_of_origin(c)) for c in (0, 60, 120, 180)]


def test_a_callout_is_placed_at_its_chainage_in_lon_lat_order() -> None:
    fc = sue_geo.feature_collection([callout("90")], STRAIGHT)
    (feature,) = fc["features"]
    lon, lat = feature["geometry"]["coordinates"]
    assert geo.to_local(lat, lon, *ORIGIN) == pytest.approx((90.0, 0.0), abs=0.05)
    props = feature["properties"]
    assert (props["position_method"], props["position_confidence"]) == ("interpolated_from_printed_latlong", "estimated")


def test_a_straight_road_has_near_zero_holdout_error() -> None:
    assessed = sue_geo.assess("Road A.pdf", STRAIGHT)
    assert assessed.usable and assessed.holdout_p90_m == pytest.approx(0.0, abs=0.01)


def test_a_bent_road_reports_the_bend_as_holdout_error() -> None:
    bent = [row(0, *ORIGIN), row(60, *geo.from_local(60, 10, *ORIGIN)), row(120, *east_of_origin(120))]
    # Predicting the middle point from its neighbours misses it by its 10 m offset.
    assert sue_geo.assess("Road A.pdf", bent).holdout_p90_m == pytest.approx(10.0, abs=0.2)


def test_points_that_walk_much_further_than_the_chainage_fail_the_path_check() -> None:
    zigzag = [row(c, *geo.from_local(c, 40 if i % 2 else 0, *ORIGIN)) for i, c in enumerate((0, 60, 120, 180))]
    assessed = sue_geo.assess("Road A.pdf", zigzag)
    assert not assessed.usable and "covers" in assessed.reason
    fc = sue_geo.feature_collection([callout("90")], zigzag)
    assert fc["features"] == [] and fc["metadata"]["unplaced"] == {"drawing failed its path check": 1}


def test_unplaceable_callouts_are_counted_never_guessed() -> None:
    fc = sue_geo.feature_collection([callout("500"), callout(""), callout("10", drawing="Other.pdf")], STRAIGHT)
    assert fc["features"] == []
    assert fc["metadata"]["unplaced"] == {"chainage outside the printed points": 1, "no chainage": 1,
                                          "no printed points on this drawing": 1}


def test_printed_points_at_the_same_chainage_count_once() -> None:
    doubled = [*STRAIGHT, row(60.4, *east_of_origin(60.4))]
    assert len(sue_geo.assess("Road A.pdf", doubled).anchors) == 4


def test_interpolate_sorts_its_anchors_before_bracketing() -> None:
    # Anchors handed in out of chainage order must still interpolate correctly.
    anchors = [(100.0, *east_of_origin(100)), (0.0, *ORIGIN)]
    lat, lon = geo.interpolate(25.0, anchors)  # type: ignore[misc]
    assert geo.to_local(lat, lon, *ORIGIN) == pytest.approx((25.0, 0.0), abs=1e-3)


def test_interpolate_on_a_zero_length_segment_returns_its_point_without_dividing_by_zero() -> None:
    # Two anchors sharing a chainage form a zero-length segment; it must not raise.
    anchors = [(50.0, *ORIGIN), (50.0, *east_of_origin(5)), (100.0, *east_of_origin(100))]
    assert geo.interpolate(50.0, anchors) == pytest.approx(ORIGIN, abs=1e-9)


def test_points_exactly_one_metre_apart_in_chainage_are_both_kept() -> None:
    # _SAME_POINT_M (1.0) dedupes points *closer* than 1 m; exactly 1 m apart must survive.
    rows = [row(0, *ORIGIN), row(1.0, *east_of_origin(1.0)), row(120, *east_of_origin(120))]
    assert len(sue_geo.assess("Road A.pdf", rows).anchors) == 3


def test_path_ratio_exactly_at_tolerance_still_counts_as_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The check is "> PATH_TOLERANCE"; a ratio sitting exactly on the boundary must still pass.
    walked, span = 105.0, 100.0
    boundary = abs(walked / span - 1)  # computed the same way assess() computes it, bit-for-bit
    monkeypatch.setattr(sue_geo, "distance_m", lambda *a, **k: walked)
    monkeypatch.setattr(sue_geo, "PATH_TOLERANCE", boundary)
    assessed = sue_geo.assess("Road A.pdf", [row(0, *ORIGIN), row(span, *east_of_origin(span))])
    assert assessed.usable


def test_holdout_p90_is_the_90th_percentile_not_the_median(monkeypatch: pytest.MonkeyPatch) -> None:
    anchors_count = 11
    rows = [row(c * 10, *east_of_origin(c * 10)) for c in range(anchors_count)]
    walked_calls = [10.0] * (anchors_count - 1)  # keeps the path check usable (ratio == 1.0)
    holdout_calls = [float(v) for v in range(1, anchors_count - 1)]  # 1..9, already sorted
    queue = iter(walked_calls + holdout_calls)
    monkeypatch.setattr(sue_geo, "distance_m", lambda *a, **k: next(queue))
    assessed = sue_geo.assess("Road A.pdf", rows)
    # p90 index = int(0.9 * 8) = 7 -> sorted[7] == 8.0; the mutant's median index = int(0.5 * 8) = 4 -> 5.0
    assert assessed.holdout_p90_m == pytest.approx(8.0)
