"""Tests for detect/channel_fusion.py — one line's picks merged across its channels."""

from __future__ import annotations

import numpy as np
import pytest

from detect.channel_fusion import ChannelPicks, fuse, time_zero_ns
from detect.migration_picker import Pick

SPACING = 0.025
# The company's three channels: sampling and each one's own direct-wave arrival.
CHANNELS = {"RAD": (0.1, 1.6), "RA1": (0.2, 4.2), "RA2": (0.4, 6.4)}


def _pick_at(channel: str, trace: int, time_ns: float, strength: float) -> Pick:
    dt, zero = CHANNELS[channel]
    return Pick(trace=trace, sample=round((time_ns + zero) / dt), strength=strength)


def _line(picks: dict[str, list[Pick]]) -> dict[str, ChannelPicks]:
    return {name: ChannelPicks(picks.get(name, []), *CHANNELS[name]) for name in CHANNELS}


def test_one_target_on_three_channels_with_different_time_zeros_is_one_target() -> None:
    picks = {name: [_pick_at(name, 100, 8.0, 5.0)] for name in CHANNELS}
    (target,) = fuse(_line(picks), SPACING)
    assert target.channels == ("RAD", "RA1", "RA2")
    assert target.time_ns == pytest.approx(8.0, abs=0.3)


def test_raw_time_would_split_it_which_is_why_time_zero_matters() -> None:
    picks = {name: [_pick_at(name, 100, 8.0, 5.0)] for name in CHANNELS}
    raw = {name: ChannelPicks(p, CHANNELS[name][0], 0.0) for name, p in picks.items()}  # time zero ignored
    assert len(fuse(raw, SPACING)) == 3


def test_corroborated_targets_rank_before_stronger_single_channel_ones() -> None:
    picks = {"RAD": [_pick_at("RAD", 40, 5.0, 50.0), _pick_at("RAD", 150, 10.0, 4.0)],
             "RA1": [_pick_at("RA1", 151, 10.2, 4.0)]}
    targets = fuse(_line(picks), SPACING)
    assert [t.channels for t in targets] == [("RAD", "RA1"), ("RAD",)]


def test_two_picks_from_one_channel_never_merge_into_one_target() -> None:
    picks = {"RAD": [_pick_at("RAD", 100, 8.0, 5.0), _pick_at("RAD", 102, 8.2, 4.0)]}
    assert len(fuse(_line(picks), SPACING)) == 2


def test_picks_apart_in_position_or_time_stay_apart() -> None:
    picks = {"RAD": [_pick_at("RAD", 100, 8.0, 5.0)], "RA1": [_pick_at("RA1", 140, 8.0, 5.0)],
             "RA2": [_pick_at("RA2", 100, 14.0, 5.0)]}
    assert all(len(t.channels) == 1 for t in fuse(_line(picks), SPACING))


def test_time_zero_is_the_direct_wave() -> None:
    dt = 0.2
    t = np.arange(256) * dt
    traces = np.tile(np.exp(-((t - 4.2) / 0.3) ** 2) * 100 + np.exp(-((t - 20) / 0.3) ** 2) * 10, (30, 1))
    assert time_zero_ns(traces, dt) == pytest.approx(4.2, abs=dt)


def test_a_pick_cannot_chain_through_one_member_to_join_a_target() -> None:
    # RAD at 100, RA2 at 110 (within 0.3 m = 12 traces of RAD), RA1 at 120: near RA2 but 20 traces
    # (0.5 m) from RAD. RA1 must not be counted as corroborating RAD.
    picks = {"RAD": [_pick_at("RAD", 100, 8.0, 9.0)], "RA2": [_pick_at("RA2", 110, 8.0, 8.0)],
             "RA1": [_pick_at("RA1", 120, 8.0, 7.0)]}
    channels_of = sorted(t.channels for t in fuse(_line(picks), SPACING))
    assert ("RAD", "RA1", "RA2") not in channels_of


def test_a_strong_shallow_target_does_not_move_time_zero() -> None:
    dt = 0.2
    t = np.arange(256) * dt
    traces = np.tile(np.exp(-((t - 4.2) / 0.3) ** 2) * 100, (40, 1))
    traces[18:22] += np.exp(-((t - 9.0) / 0.3) ** 2) * 900  # far stronger, but only above a few traces
    assert time_zero_ns(traces, dt) == pytest.approx(4.2, abs=dt)
