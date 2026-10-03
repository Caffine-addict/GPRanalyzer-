"""Tests for the scoring in scripts/validate_on_twente.py."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_on_twente import _envelope, recall

TRUTH = [{"distance_m": 1.0, "depth_m": 0.8}, {"distance_m": 2.5, "depth_m": 1.2}, {"distance_m": 4.0, "depth_m": 0.5}]


def test_a_line_walked_backwards_from_an_unknown_start_still_finds_every_utility() -> None:
    length = 6.0
    # walked from the far end, starting 0.7 m before the trench's zero
    apexes = [{"x_m": length - (u["distance_m"] + 0.7), "depth_m": u["depth_m"]} for u in TRUTH]
    assert recall(TRUTH, length, apexes) == 1.0


def test_a_wrong_depth_or_an_empty_line_finds_nothing() -> None:
    assert recall(TRUTH, 6.0, []) == 0.0
    deep = [{"x_m": u["distance_m"], "depth_m": u["depth_m"] + 2.0} for u in TRUTH]
    assert recall(TRUTH, 6.0, deep) == 0.0


def test_one_apex_cannot_count_for_two_utilities() -> None:
    close = [{"distance_m": 1.0, "depth_m": 0.8}, {"distance_m": 1.1, "depth_m": 0.8}]
    assert recall(close, 6.0, [{"x_m": 1.05, "depth_m": 0.8}]) == 0.5


def test_envelope_of_a_tone_is_its_amplitude() -> None:
    t = np.arange(512)
    tone = 3.0 * np.cos(2 * np.pi * 32 * t / 512)[:, None]
    assert _envelope(tone)[50:-50] == pytest.approx(3.0, rel=1e-6)
