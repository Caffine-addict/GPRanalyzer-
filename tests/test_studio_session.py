"""Tests for studio/session.py — job discovery, channel loading, axis metadata.

Runs against the real SPR files in `Dataset/DSU_GPR_Files/`, skipping if they
aren't present: this module's whole job is reading that specific format, and a
synthetic stand-in would test the stub rather than the parser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from studio import session

_DATASET = Path("Dataset/DSU_GPR_Files")

pytestmark = pytest.mark.skipif(
    not _DATASET.exists() or not any(_DATASET.iterdir()),
    reason="SPR dataset not present",
)


@pytest.fixture
def job_dir() -> Path:
    return session.resolve_job(session.list_jobs(_DATASET)[0], _DATASET)


def test_list_jobs_finds_the_survey_folders() -> None:
    jobs = session.list_jobs(_DATASET)
    assert jobs
    assert jobs == sorted(jobs)


def test_a_missing_dataset_directory_lists_nothing_rather_than_raising() -> None:
    assert session.list_jobs(Path("Dataset/does-not-exist")) == []


def test_resolve_job_rejects_a_name_that_is_not_in_the_dataset() -> None:
    with pytest.raises(session.JobNotFoundError, match="unknown job"):
        session.resolve_job("Job_9999", _DATASET)


def test_resolve_job_rejects_path_traversal() -> None:
    # Membership is checked against the discovered list, so traversal cannot
    # work by construction rather than by a filter someone must maintain.
    for attempt in ("..", "../..", "/etc"):
        with pytest.raises(session.JobNotFoundError):
            session.resolve_job(attempt, _DATASET)


def test_every_channel_reports_a_usable_geometry(job_dir: Path) -> None:
    channels = session.available_channels(job_dir)
    assert channels
    for channel in channels:
        assert channel.n_traces > 0
        assert channel.n_samples > 0
        assert channel.trace_spacing_m > 0
        assert channel.sample_interval_ns > 0
        assert channel.line_length_m == pytest.approx(channel.n_traces * channel.trace_spacing_m)
        assert channel.time_window_ns == pytest.approx(channel.n_samples * channel.sample_interval_ns)


def test_channels_are_ordered_shallow_to_deep(job_dir: Path) -> None:
    # The three receivers differ by sampling interval — a longer interval buys
    # a deeper window at coarser resolution.
    intervals = [channel.sample_interval_ns for channel in session.available_channels(job_dir)]
    assert intervals == sorted(intervals)


def test_depth_range_is_reported_only_when_a_dielectric_exists(job_dir: Path) -> None:
    for channel in session.available_channels(job_dir):
        if channel.dielectric_assumed:
            assert channel.max_depth_m is not None and channel.max_depth_m > 0
        else:
            assert channel.max_depth_m is None  # never a guessed default


def test_radargram_is_transposed_into_display_orientation(job_dir: Path) -> None:
    frame = session.load_frame(job_dir, "RAD")
    radargram, info = session.load_radargram(job_dir, "RAD")
    assert frame.traces.shape == (info.n_traces, info.n_samples)
    assert radargram.shape == (info.n_samples, info.n_traces)  # rows = time


def test_parses_are_cached_between_calls(job_dir: Path) -> None:
    assert session.load_frame(job_dir, "RAD") is session.load_frame(job_dir, "RAD")


def test_an_unknown_channel_extension_is_rejected(job_dir: Path) -> None:
    with pytest.raises(ValueError, match="unknown channel"):
        session.channel_path(job_dir, "RA9")


def test_gps_track_returns_lat_lon_points(job_dir: Path) -> None:
    track = session.load_gps_track(job_dir)
    for point in track:
        assert set(point) == {"lat", "lon"}
        assert -90 <= point["lat"] <= 90
        assert -180 <= point["lon"] <= 180


def test_a_job_without_a_gps_file_returns_no_points(tmp_path: Path) -> None:
    assert session.load_gps_track(tmp_path) == []


def test_header_carries_the_fields_the_depth_axis_depends_on(job_dir: Path) -> None:
    header = session.job_header(job_dir)
    assert "SPR_SHAFT_INTERVAL" in header
    assert "SPR_MEDIUM_DIELECTRIC" in header


def test_axis_provenance_strings_distinguish_measured_from_inferred() -> None:
    assert "measured" in session.DISTANCE_PROVENANCE
    assert "inferred" in session.DEPTH_PROVENANCE
    assert "unconfirmed" in session.DEPTH_PROVENANCE
