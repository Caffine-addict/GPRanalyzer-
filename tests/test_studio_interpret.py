"""Tests for studio/interpret.py — a picked target turned into evidence and a taxonomy class.

The honesty properties are the point of this module, so most of these tests are about
provenance rather than values: a depth is only "calibrated" when a velocity was actually
measured, and the interpreter's free-text label never becomes a taxonomy class.
"""

from __future__ import annotations

import numpy as np
import pytest

from studio import interpret
from studio.picks import Pick
from studio.session import ChannelInfo

TAXONOMY = (
    "cavities",
    "elongated_linear_target",
    "intersecting_linear_and_point_reflector",
    "strong_high_contrast_reflector",
    "multiple_point_reflectors",
    "low_snr_point_reflector",
    "cluttered_multi_target",
    "disturbed_zone",
    "clear_point_reflector",
)

N_TRACES, N_SAMPLES = 384, 256


def _info(**overrides) -> ChannelInfo:
    base: dict = {
        "extension": "RAD", "label": "Ch 0 — shallow", "n_traces": N_TRACES, "n_samples": N_SAMPLES,
        "trace_spacing_m": 0.025, "sample_interval_ns": 0.1, "dielectric_assumed": 9.0,
        "line_length_m": 9.6, "time_window_ns": 25.6, "max_depth_m": 1.28,
    }
    base.update(overrides)
    return ChannelInfo(**base)


def _pick(**overrides) -> Pick:
    base: dict = {
        "id": "p1", "channel": "RAD", "trace": 150.0, "sample": 90.0, "time_ns": 9.0,
        "depth_m": 0.45, "velocity_m_per_ns": 0.1011, "velocity_source": "fitted",
        "dielectric": 8.79, "label": "suspected service duct", "note": "",
        "fit_r2": 0.98, "created_at": "2026-09-12T00:00:00Z",
    }
    base.update(overrides)
    return Pick(**base)


def _traces(peak: float = 60.0, at_trace: int = 150, at_sample: int = 90) -> np.ndarray:
    """Quiet ground with one localised echo, so the refiner has something real to measure."""
    rng = np.random.default_rng(7)
    traces = rng.normal(0.0, 1.0, size=(N_TRACES, N_SAMPLES))
    wavelet = peak * np.array([1.0, -1.0, 0.5, -0.2])
    for trace in range(at_trace - 8, at_trace + 9):
        traces[trace, at_sample : at_sample + 4] += wavelet
    return traces


def _one(pick: Pick, traces: np.ndarray | None = None, others: tuple[Pick, ...] = ()) -> interpret.PickEvidence:
    picks = (pick, *others)
    return interpret.evidence_for_line(picks, _info(), _traces() if traces is None else traces, TAXONOMY)[pick.id]


def test_a_measured_velocity_makes_the_depth_calibrated() -> None:
    # The whole point of fitting a hyperbola: the velocity came from this target itself,
    # so the depth is a measurement rather than an assumption carried from the header.
    result = _one(_pick(velocity_source="fitted", fit_r2=0.98))
    assert result.evidence.depth_confidence == "calibrated"
    assert result.evidence.depth_m == pytest.approx(0.45)


@pytest.mark.parametrize("source", ["assumed", "manual"])
def test_an_unmeasured_velocity_leaves_the_depth_estimated(source: str) -> None:
    result = _one(_pick(velocity_source=source, fit_r2=None))
    assert result.evidence.depth_confidence == "estimated"


def test_position_along_the_line_is_calibrated_because_a_wheel_encoder_measured_it() -> None:
    result = _one(_pick(trace=150.0))
    assert result.evidence.position_confidence == "calibrated"
    assert result.evidence.position_m == pytest.approx(150.0 * 0.025)


def test_amplitude_is_never_reported_as_calibrated() -> None:
    # No calibrated amplitude exists on this instrument (company question #2), so an
    # amplitude read off the traces is a relative proxy and must say so.
    result = _one(_pick())
    assert result.evidence.amplitude_confidence == "estimated"
    assert result.evidence.amplitude is not None


def test_the_taxonomy_class_is_measured_not_taken_from_the_free_text_label() -> None:
    # A pick's label is whatever the interpreter typed. Letting it become a taxonomy class
    # would turn a hypothesis into a classification without any evidence behind it.
    result = _one(_pick(label="definitely a cavity"))
    assert result.evidence.detection_class in TAXONOMY
    assert result.evidence.detection_class != "cavities"
    assert result.taxonomy_class == result.evidence.detection_class


def test_a_target_standing_clear_of_the_ground_is_classed_clear() -> None:
    result = _one(_pick(), traces=_traces(peak=60.0))
    assert result.taxonomy_class == "clear_point_reflector"
    assert "amplitude" in result.class_rule


def test_a_target_barely_above_the_ground_is_classed_low_snr() -> None:
    result = _one(_pick(), traces=_traces(peak=0.4))
    assert result.taxonomy_class == "low_snr_point_reflector"


def test_other_picks_on_the_line_become_neighbours() -> None:
    other = _pick(id="p2", trace=300.0, label="second target")
    result = _one(_pick(), others=(other,))
    assert len(result.evidence.neighbours) == 1
    assert result.evidence.neighbours[0] in TAXONOMY


def test_the_box_measured_is_the_targets_own_diffraction_aperture() -> None:
    # Same aperture the synthetic labeller uses, so a real pick and a simulated label
    # describe a target the same way.
    from detect.measure import hyperbola_half_aperture_m

    pick = _pick(depth_m=0.45)
    result = _one(pick)
    expected_px = 2 * hyperbola_half_aperture_m(0.45) / 0.025
    assert result.evidence.hyperbola_width_px == pytest.approx(expected_px, abs=2.0)


def test_a_pick_on_another_channel_is_not_evidence_for_this_one() -> None:
    # Channels have different time axes; mixing them would put a target at a wrong depth.
    result = interpret.evidence_for_line((_pick(channel="RA1"),), _info(), _traces(), TAXONOMY)
    assert result == {}
