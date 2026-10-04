"""Tests for the scoring in scripts/validate_on_twente.py."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_on_twente import _count, _envelope, _mapped_found, recall

from survey.mapping import LinePlacement

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


def test_count_matches_each_utility_once_at_the_trench_origin_and_direction() -> None:
    points = [(5.0 - 1.0, 0.8), (5.0 - 2.5, 1.2), (9.5, 0.5)]  # trench runs backwards from 5.0
    assert _count(TRUTH, points, origin=5.0, sign=-1.0) == 2
    assert _count(TRUTH, points, origin=5.0, sign=1.0) == 0


def test_mapped_found_traces_a_utility_crossing_three_parallel_lines() -> None:
    # three lines 1 m apart along +x; the trench's utilities cross all of them; one stray stone
    lines, placements = [], []
    for k in range(3):
        placements.append(LinePlacement(start=np.array([0.0, float(k)]), direction=np.array([1.0, 0.0])))
        xs = [u["distance_m"] + 0.4 for u in TRUTH] + ([3.3] if k == 0 else [])
        zs = [u["depth_m"] for u in TRUTH] + ([0.3] if k == 0 else [])
        lines.append((6.0, xs, zs))
    frame = (placements, np.array([1.0, 0.0]))
    assert _mapped_found(TRUTH, lines, frame) == 9  # 3 utilities x 3 lines; the stone is not traced
    scattered = [(6.0, [x + 1.7 * k for x in xs], zs) for k, (_, xs, zs) in enumerate(lines)]
    assert _mapped_found(TRUTH, scattered, frame) < 9
