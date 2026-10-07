"""Tests for studio/site_map.py — field mode: a site's targets at their real positions."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from studio import imports, site_map
from studio.site_map import PlacedLine, SiteTarget, line_families, utility_runs

LAT0, LON0 = 12.9701, 77.6419
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def _latlon(east_m: float, north_m: float) -> tuple[float, float]:
    return LAT0 + north_m / 110_574.0, LON0 + east_m / M_PER_DEG_LON


def _target(job: str, east: float, north: float, depth: float) -> SiteTarget:
    lat, lon = _latlon(east, north)
    return SiteTarget(f"{job}#{east}", job, 0, lat, lon, depth, 0.1, 0.1, ("RAD",), "suggested", "test")


def test_a_pipe_crossing_three_parallel_lines_is_one_run() -> None:
    # lines run north, 1 m apart; a pipe runs east at north = 3 m, 0.6 m deep; clutter elsewhere
    targets = [_target(f"L{k}", k, 3.0 + 0.05 * k, 0.6) for k in range(3)] + [_target("L1", 1, 7.0, 0.3)]
    (run,) = utility_runs(targets, ["L0", "L1", "L2"], np.array([0.0, 1.0]))
    assert [t.job for t in run] == ["L0", "L1", "L2"]


def test_a_zigzag_or_a_diving_chain_is_not_a_pipe() -> None:
    zigzag = [_target("L0", 0, 3.0, 0.6), _target("L1", 1, 4.2, 0.6), _target("L2", 2, 3.0, 0.6)]
    diving = [_target("L0", 0, 3.0, 0.3), _target("L1", 1, 3.0, 0.5), _target("L2", 2, 3.0, 0.75)]
    assert utility_runs(zigzag, ["L0", "L1", "L2"], np.array([0.0, 1.0])) == []
    assert utility_runs(diving, ["L0", "L1", "L2"], np.array([0.0, 1.0])) == []


def test_a_chain_along_its_own_lines_is_not_a_crossing_pipe() -> None:
    along = [_target(f"L{k}", 0.1 * k, 3.0 + k, 0.6) for k in range(3)]  # runs north, like the lines
    assert utility_runs(along, ["L0", "L1", "L2"], np.array([0.0, 1.0])) == []


def test_grid_lines_split_into_families_by_heading() -> None:
    def line(job: str, east: float, north: float) -> PlacedLine:
        return PlacedLine(job, np.array([0.0, 1.0]), np.array([LAT0, LAT0]), np.array([LON0, LON0]), 0.05, 0.025, (east, north))
    families = line_families([line("N1", 0, 1), line("E1", 1, 0), line("N2", 0.05, -1), line("E2", -1, 0.1)])
    assert sorted(sorted(line.job for line in fam) for fam in families) == [["E1", "E2"], ["N1", "N2"]]


def _line_with_gps(east_m: float, pipe_north_m: float | None) -> list[tuple[str, bytes]]:
    """A MALÅ line walked north at `east_m`, 8 m long, with a GPS log and optionally a pipe."""
    n_traces, n_samples, spacing, dt = 320, 128, 0.025, 0.2
    traces = np.random.default_rng(int(east_m * 10)).normal(size=(n_traces, n_samples)) * 3
    t = np.arange(n_samples) * dt
    traces += 2000 * np.exp(-((t - 1.0) / 0.3) ** 2)  # the direct wave: time zero at 1 ns
    if pipe_north_m is not None:
        apex = pipe_north_m / spacing
        for x in range(n_traces):
            arrival = 1.0 + np.sqrt(8.0**2 + (2 * (x - apex) * spacing / 0.1) ** 2)  # 8 ns below time zero
            a = (np.pi * 0.4 * (t - arrival)) ** 2
            spreading = (8.0 / (arrival - 1.0)) ** 2  # energy spreads: limbs weaken away from the apex
            traces[x] += 400 * spreading * (1 - 2 * a) * np.exp(-a)
    header = b"SAMPLES:128\r\nFREQUENCY:5000\r\nDISTANCE INTERVAL:0.025\r\n"
    gps = "4\n" + "".join(
        f"{k:05d},{trace:05d},{lat:.10f},{lon:.10f},900,0.8,1.5,19,0.0000\n"
        for k, trace in enumerate(range(0, n_traces, 8))
        for lat, lon in [_latlon(east_m, trace * spacing)])
    return [("l.rd3", traces.astype("<i2").tobytes()), ("l.rad", header), ("l.gps", gps.encode())]


def test_a_site_puts_its_lines_targets_and_pipe_on_the_map(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    dataset = tmp_path / "Lines"
    dataset.mkdir()
    for k in range(3):
        imports.import_line(dataset, f"Site_L{k}", _line_with_gps(east_m=1.0 * k, pipe_north_m=4.0))
    collection = site_map.site_geojson(dataset, "Site")
    kinds = [f["properties"]["kind"] for f in collection["features"]]
    assert kinds.count("survey_line") == 3
    targets = [f for f in collection["features"] if f["properties"]["kind"] == "target"]
    assert all(t["properties"]["horizontal_error_m"] > 0 and t["properties"]["depth_error_m"] > 0 for t in targets)
    runs = [f for f in collection["features"] if f["properties"]["kind"] == "utility_run"]
    pipe = [r for r in runs if all(abs((lat - LAT0) * 110_574.0 - 4.0) < 0.2 for _, lat in r["geometry"]["coordinates"])]
    assert len(pipe) == 1 and pipe[0]["properties"]["lines"] == 3  # the pipe is placed where it is
    assert pipe[0]["properties"]["depth_m"] == pytest.approx(0.4, abs=0.1)  # 8 ns at 0.1 m/ns
    evidence = collection["metadata"]["runs_evidence"]
    assert set(evidence) == {"runs", "chance_mean", "p_chance", "verdict"}
    assert all(r["properties"]["evidence"] == evidence["verdict"] for r in runs)


# ---------------------------------------------------------------- header dielectric of 1.00 (air), 2026-10-07

from core.contracts import ScanFrame
from studio import session


@pytest.mark.parametrize(("header", "usable"), [(1.0, None), (1.9, None), (None, None), (2.0, 2.0), (9.0, 9.0)])
def test_a_header_dielectric_below_any_soil_is_treated_as_unset(header: float | None, usable: float | None) -> None:
    frame = ScanFrame(source_type="t", provenance={}, traces=np.zeros((4, 8)), sample_interval_ns=0.1,
                      trace_spacing_m=0.025, dielectric_assumed=header)
    assert session.usable_dielectric(frame) == usable
    assert (session.describe_channel(frame, "RAD").max_depth_m is None) == (usable is None)  # no air-speed depth axis


def test_a_site_without_a_usable_header_measures_its_own_soil(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    dataset = tmp_path / "Lines"
    dataset.mkdir()
    for k in range(3):  # synthetic lines at 0.1 m/ns (dielectric 9), no dielectric in their headers
        imports.import_line(dataset, f"Site_L{k}", _line_with_gps(east_m=1.0 * k, pipe_north_m=4.0))
    value, basis = site_map.site_dielectric(dataset, "Site")
    assert "header unusable" in basis
    meta = site_map.site_geojson(dataset, "Site")["metadata"]["dielectric"]
    assert meta["value"] == pytest.approx(value, abs=0.01) and meta["lines_with_unusable_header"]


@pytest.mark.parametrize(("headers", "expected"), [((9.0, 9.0, 9.0), 9.0), ((9.0, 1.0, 9.0), None), ((9.0, 6.0, 9.0), None),
                                                  ((None, None, None), None)])
def test_a_line_trusts_its_header_only_when_every_channel_agrees(monkeypatch: pytest.MonkeyPatch,
                                                                 headers: tuple, expected: float | None) -> None:
    # one ground under all channels: a disagreeing or air channel means no header value is trusted for the line
    channels = [type("C", (), {"extension": ext})() for ext in ("RAD", "RA1", "RA2")]
    frames = {ext: ScanFrame(source_type="t", provenance={}, traces=np.zeros((4, 8)), sample_interval_ns=0.1,
                             trace_spacing_m=0.025, dielectric_assumed=h) for ext, h in zip(("RAD", "RA1", "RA2"), headers)}
    monkeypatch.setattr(session, "available_channels", lambda _job: channels)
    monkeypatch.setattr(session, "load_frame", lambda _job, ext: frames[ext])
    assert session.line_dielectric(Path("x")) == expected
