"""Tests for survey/mapping.py — lines on a map, targets traced across them."""

from __future__ import annotations

import numpy as np
import pytest

from survey.mapping import along_axis, place_line, survey_axis, traced_across_lines

HEADING = np.array([np.cos(np.radians(75)), np.sin(np.radians(75))])


def _track(start: list[float], length: float = 6.0, n: int = 300, lag: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    wheel = np.linspace(0, length, n)
    jitter = np.random.default_rng(1).normal(scale=0.03, size=(n, 2))
    return np.array(start) + np.outer(wheel * lag, HEADING) + jitter, wheel


def test_a_straight_walk_is_placed_by_its_start_and_heading() -> None:
    placement = place_line(*_track([255025.0, 473331.0]))
    assert placement is not None
    assert placement.direction == pytest.approx(HEADING, abs=0.01)
    assert placement.at(6.0) == pytest.approx(np.array([255025.0, 473331.0]) + 6 * HEADING, abs=0.05)


def test_a_lagging_receiver_on_a_short_line_is_still_placed() -> None:
    assert place_line(*_track([0.0, 0.0], length=1.2, n=60, lag=0.7)) is not None


@pytest.mark.parametrize("bad", ["curved", "stuck", "too_few"])
def test_a_track_that_is_not_a_straight_walk_is_refused(bad: str) -> None:
    positions, wheel = _track([0.0, 0.0])
    if bad == "curved":
        positions = positions + np.c_[np.zeros(300), np.sin(wheel) * 1.5]
    elif bad == "stuck":
        positions = np.repeat(positions[:1], 300, axis=0)
    else:
        positions, wheel = positions[:2], wheel[:2]
    assert place_line(positions, wheel) is None


def test_lines_walked_in_opposite_directions_share_one_axis_and_coordinate() -> None:
    forward = place_line(*_track([0.0, 0.0]))
    positions, wheel = _track([1.0, 0.0])
    backward = place_line(positions[::-1], wheel)  # zigzag: the next line walked back
    assert forward is not None and backward is not None
    axis = survey_axis([forward, backward])
    assert axis == pytest.approx(HEADING, abs=0.01)
    # a pipe crossing both lines 2 m from the first line's start has one along-axis coordinate
    on_forward = along_axis(forward, 2.0, axis)
    on_backward = along_axis(backward, 6.0 - 2.0, axis)
    assert on_forward == pytest.approx(on_backward, abs=0.3)  # lines are 1 m apart across the axis


def test_only_picks_that_line_up_on_enough_lines_are_kept() -> None:
    picks = [
        [(2.0, 0.8), (4.0, 1.2)],   # pipe at 2.0 m / 0.8 m deep, and a stone
        [(2.1, 0.85)],
        [(1.95, 0.75), (5.0, 0.4)],
    ]
    keep = traced_across_lines(picks, min_lines=3)
    assert keep == [[True, False], [True], [True, False]]
    # one line agreeing twice with itself does not count as two lines
    assert traced_across_lines([[(1.0, 0.5), (1.1, 0.5)], [(1.0, 0.5)]], min_lines=3) == [[False, False], [False]]
