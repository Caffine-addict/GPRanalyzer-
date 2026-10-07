"""Tests for detect/migration_picker.py."""

from __future__ import annotations

import numpy as np
import pytest

from detect.migration_picker import pick_targets

DT, SPACING, EPS = 0.1, 0.025, 9.0


def _line(apexes: list[tuple[int, float]], n_traces: int = 240, n_samples: int = 256, noise: float = 0.05) -> np.ndarray:
    """(n_traces, n_samples) with a Ricker hyperbola per (apex trace, apex time ns), plus a flat layer."""
    v = 0.2998 / np.sqrt(EPS)
    t = np.arange(n_samples) * DT
    traces = np.random.default_rng(1).normal(size=(n_traces, n_samples)) * noise
    for apex_trace, apex_ns in apexes:
        for x in range(n_traces):
            arrival = np.sqrt(apex_ns**2 + (2 * (x - apex_trace) * SPACING / v) ** 2)
            a = (np.pi * 0.4 * (t - arrival)) ** 2
            traces[x] += (1 - 2 * a) * np.exp(-a)
    traces += 3.0 * np.exp(-((t - 2.0) / 0.3) ** 2)  # a flat layer every trace shares
    return traces


def test_each_hyperbola_is_picked_at_its_apex() -> None:
    truth = [(60, 8.0), (170, 14.0)]
    picks = pick_targets(_line(truth), sample_interval_ns=DT, trace_spacing_m=SPACING, dielectric=EPS)
    for apex_trace, apex_ns in truth:
        assert any(abs(p.trace - apex_trace) <= 4 and abs(p.sample * DT - apex_ns) <= 0.6 for p in picks), (apex_trace, picks[:4])


def test_picks_come_strongest_first_and_are_capped() -> None:
    picks = pick_targets(_line([(60, 8.0), (170, 14.0)]), sample_interval_ns=DT, trace_spacing_m=SPACING, max_picks=3)
    assert len(picks) <= 3
    assert [p.strength for p in picks] == sorted((p.strength for p in picks), reverse=True)


def test_a_flat_layer_alone_gives_no_confident_target() -> None:
    picks = pick_targets(_line([]), sample_interval_ns=DT, trace_spacing_m=SPACING)
    assert all(abs(p.sample * DT - 2.0) > 0.6 for p in picks)  # the layer is removed, never picked


@pytest.mark.parametrize("bad", [{"sample_interval_ns": 0.0}, {"trace_spacing_m": -1.0}, {"dielectric": 0.5}])
def test_impossible_axes_are_refused(bad: dict) -> None:
    kwargs = {"sample_interval_ns": DT, "trace_spacing_m": SPACING, "dielectric": EPS} | bad
    with pytest.raises(ValueError):
        pick_targets(_line([(60, 8.0)]), **kwargs)


def test_nothing_is_picked_on_the_direct_wave() -> None:
    # A strong direct wave at 2 ns with a kink (what the picker caught on company Set 1), and a pipe
    # at 8 ns. Nothing inside the direct-wave window is picked; the kink, just past it, still is,
    # and is what consumers flag as near the surface (skipping further loses real tagged targets).
    from detect.migration_picker import NEAR_SURFACE_NS, SKIP_BELOW_TIME_ZERO_NS, time_zero_ns

    traces = _line([(120, 8.0)])
    t = np.arange(traces.shape[1]) * DT
    for x in range(traces.shape[0]):
        arrival = 2.0 + (0.3 if 100 <= x <= 110 else 0.0)  # a bump in the surface
        a = (np.pi * 0.4 * (t - arrival)) ** 2
        traces[x] += 30 * (1 - 2 * a) * np.exp(-a)
    zero = time_zero_ns(traces, DT)
    picks = pick_targets(traces, sample_interval_ns=DT, trace_spacing_m=SPACING, dielectric=EPS)
    assert all(p.sample * DT >= zero + SKIP_BELOW_TIME_ZERO_NS - 1e-9 for p in picks)
    kink = [p for p in picks if 100 <= p.trace <= 112 and p.sample * DT < 5]
    assert kink and all(p.sample * DT - zero < NEAR_SURFACE_NS for p in kink)  # flagged, not trusted
    assert any(abs(p.trace - 120) <= 4 and abs(p.sample * DT - 8.0) <= 0.6 for p in picks)  # the pipe is still found
