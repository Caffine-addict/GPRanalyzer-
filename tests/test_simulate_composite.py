"""simulate/composite.py: apex labels sit on the pasted apexes, and test sites never become training data."""

from __future__ import annotations

import numpy as np

from simulate import composite as c
from simulate import instrument


def _ground(rng: np.random.Generator) -> c.Background:
    traces = rng.normal(0, 1, (c.WINDOW, instrument.SAMPLES_PER_TRACE))
    traces[:, instrument.DIRECT_WAVE_PEAK_SAMPLE] += 40  # a direct wave, as every real trace has
    return c.Background("Set1", "Set1_LINE001", 0, traces)


def test_every_label_is_on_the_apex_of_its_pasted_hyperbola() -> None:
    rng = np.random.default_rng(3)
    for _ in range(20):
        patch = c.analytic_patch(rng, [c.ricker(rng)])
        _traces, visible = c.composite(_ground(rng), patch, snr=8.0)
        assert visible  # the strongest one is at snr 8, well above VISIBLE_SNR
        for t, s in visible:
            assert c.EDGE <= t < c.WINDOW - c.EDGE and s >= c.ECHO_START
            # the apex is where the echo arrives earliest: no trace of its hyperbola peaks above it
            column = np.abs(patch.field[t])
            assert abs(int(np.argmax(column[max(0, s - 6): s + 7])) + max(0, s - 6) - s) <= 4


def test_test_sites_are_excluded_by_construction() -> None:
    assert {"Set3", "Set4", "Set5"} <= c.TEST_SITES and c.VAL_SITE not in c.TEST_SITES
    assert c.site_of("Set3_Line02") == "Set3" and c.site_of("Job_0696") == "Job"


def test_to_image_keeps_traces_as_columns_and_time_as_rows() -> None:
    image = c.to_image(np.random.default_rng(0).normal(0, 1, (c.WINDOW, instrument.SAMPLES_PER_TRACE)))
    assert image.shape == (instrument.SAMPLES_PER_TRACE, c.WINDOW) and image.dtype == np.uint8


def test_yolo_label_is_the_fixed_box_centred_on_the_apex() -> None:
    cls, x, y, w, h = c.yolo_line((64, 128), c.WINDOW, 256).split()
    assert cls == "0" and float(x) == 0.5 and float(y) == 0.5
    assert np.isclose(float(w), c.BOX_TRACES / c.WINDOW) and np.isclose(float(h), c.BOX_SAMPLES / 256)
