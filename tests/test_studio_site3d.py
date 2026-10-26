"""The 3D site scene: which lines become curtains, which are drawn as a circle, which are listed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.geo import from_local
from studio import session, site3d
from studio.georef import ReferencePoint

ORIGIN = (12.97, 77.6)


def _channel(dielectric: float | None = 9.0) -> session.ChannelInfo:
    return session.ChannelInfo(extension="RAD", label="RAD · shallow", n_traces=401, n_samples=256, trace_spacing_m=0.025,
                               sample_interval_ns=0.1, dielectric_assumed=dielectric, line_length_m=10.025,
                               time_window_ns=25.6, max_depth_m=None)


def _track(job: str, north_m: float, dielectric: float | None = 9.0) -> site3d.LineSource:
    """A GPS-track line running 10 m due east, `north_m` north of the origin."""
    points = [ReferencePoint(c, *from_local(c - 5.0, north_m, *ORIGIN), accuracy_m=3.0, source="GPS") for c in (0.0, 10.0)]
    found = SimpleNamespace(reference_points=points, reason="GPS track")
    return site3d.LineSource(job, Path(job), "gps_track", found, {"RAD": _channel(dielectric)})


@pytest.fixture(autouse=True)
def _no_stored_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(site3d.candidate_store, "load_candidates", lambda job, job_dir: [])
    monkeypatch.setattr(site3d.pick_store, "load_picks", lambda job: [])
    monkeypatch.setattr(site3d.review_store, "load_reviews", lambda job: [])
    monkeypatch.setattr(site3d, "_time_zero_ns", lambda job_dir, channel: 2.0)


def _site_estimate() -> tuple[float, str]:
    return 6.25, "measured on this site (12 arches; header unusable)"


def test_each_position_source_is_drawn_its_own_way() -> None:
    frozen = SimpleNamespace(centre=from_local(0.0, 20.0, *ORIGIN), radius_m=8.0, reason="GPS froze")
    lines = [_track("S_A", -1.0), _track("S_B", 1.0),
             site3d.LineSource("S_C", Path("S_C"), "gps_location", frozen, {"RAD": _channel()}),
             site3d.LineSource("S_D", Path("S_D"), None, None, {"RAD": _channel()})]
    scene = site3d.scene("S", lines, "RAD", _site_estimate, detected=False)
    assert [line["job"] for line in scene["lines"]] == ["S_A", "S_B"]
    assert [spot["job"] for spot in scene["located"]] == ["S_C"]
    assert scene["located"][0]["radius_m"] == 8.0
    assert scene["unplaced"] == [{"job": "S_D", "reason": "no reference points and no usable GPS"}]


def test_a_curtain_follows_its_track_in_local_metres_and_never_past_the_line() -> None:
    scene = site3d.scene("S", [_track("S_A", 0.0)], "RAD", _site_estimate, detected=False)
    line = scene["lines"][0]
    assert line["covered_m"] == [0.0, 10.0]
    (c0, e0, n0), (c1, e1, n1) = line["path"][0], line["path"][-1]
    assert (c0, c1) == (0.0, 10.0)
    assert e1 - e0 == pytest.approx(10.0, abs=0.01)  # east, as surveyed
    assert n1 - n0 == pytest.approx(0.0, abs=0.01)
    assert line["time_zero_ns"] == 2.0


def test_velocity_says_where_it_came_from() -> None:
    header = site3d.scene("S", [_track("S_A", 0.0, 9.0)], "RAD", _site_estimate, detected=False)["lines"][0]
    assert header["velocity"]["source"] == "assumed"
    assert header["velocity"]["value"] == pytest.approx(0.2998 / 3, abs=1e-4)
    site = site3d.scene("S", [_track("S_A", 0.0, None)], "RAD", _site_estimate, detected=False)["lines"][0]
    assert site["velocity"]["source"] == "site"
    assert site["velocity"]["value"] == pytest.approx(0.2998 / 2.5, abs=1e-4)


def test_the_site_estimate_is_only_measured_when_a_line_needs_it() -> None:
    calls = []

    def estimate() -> tuple[float, str]:
        calls.append(1)
        return _site_estimate()

    site3d.scene("S", [_track("S_A", 0.0, 9.0), _track("S_B", 1.0, 9.0)], "RAD", estimate, detected=False)
    assert calls == []
    site3d.scene("S", [_track("S_A", 0.0, None), _track("S_B", 1.0, None)], "RAD", estimate, detected=False)
    assert calls == [1]  # measured once for the site, not once per line
