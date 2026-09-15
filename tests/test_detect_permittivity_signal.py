"""Tests for detect/measure.py's permittivity_signal — reading an unusual velocity, not binning it.

This function exists because a hard filter round the site's permittivity rejected 42 of 158 fits
across the four real survey lines, and the ones it rejected were disproportionately the interesting
ones: nine fits landed near air, three of them with 27, 36 and 40 inlier ridge points. A survey that
discards voids for not resembling soil has the wrong objective.
"""

from __future__ import annotations

import pytest

from detect.measure import (
    PERMITTIVITY_AIR_LIKE_BELOW,
    PERMITTIVITY_IMPLAUSIBLE_ABOVE,
    permittivity_signal,
)

SITE = 9.0  # SPR_MEDIUM_DIELECTRIC on all four delivered lines


def test_air_like_permittivity_suggests_a_cavity() -> None:
    # The whole point: eps near 1 means the wave crossed air, which is the same physics
    # detect/refine.py's polarity rule reasons about from the other direction.
    signal = permittivity_signal(1.18, SITE)
    assert signal.label == "air_like"
    assert signal.suggests_class == "cavities"
    assert "air" in signal.note


def test_the_real_void_like_fits_from_the_survey_lines_all_read_as_air() -> None:
    # Measured on Job_0720 and Job_0696: 1.18 at 1.61 m (40 inliers), 1.29 at 0.72 m (36),
    # 1.31 at 1.86 m (27). All three were being thrown away.
    for eps in (1.18, 1.29, 1.31):
        assert permittivity_signal(eps, SITE).label == "air_like"


def test_a_permittivity_beyond_water_is_called_implausible_not_wet() -> None:
    # Water is 81. Past that the curvature is unconstrained and the number describes no material,
    # so it must not be presented as a very wet target.
    signal = permittivity_signal(300.0, SITE)
    assert signal.label == "implausible"
    assert signal.suggests_class is None
    assert "beyond water" in signal.note


def test_a_permittivity_near_the_site_value_reads_as_ordinary_soil() -> None:
    signal = permittivity_signal(9.4, SITE)
    assert signal.label == "soil_like"
    assert signal.suggests_class is None


def test_a_much_higher_permittivity_reads_as_wet() -> None:
    signal = permittivity_signal(40.0, SITE)
    assert signal.label == "wet"
    assert "saturated" in signal.note or "water" in signal.note


def test_a_much_lower_permittivity_reads_as_fast_without_claiming_a_void() -> None:
    # Below the site value but not near air: drier or more voided ground, which is a real
    # observation but not enough to call a cavity.
    signal = permittivity_signal(3.5, SITE)
    assert signal.label == "fast"
    assert signal.suggests_class is None


def test_without_a_site_value_nothing_is_compared_to_it() -> None:
    # A site whose permittivity nobody recorded cannot have fits called wet or dry relative to it.
    # Inventing a default would be inventing the site value.
    signal = permittivity_signal(40.0, None)
    assert signal.label == "soil_like"
    assert "no site value" in signal.note


def test_air_is_still_air_without_a_site_value() -> None:
    # This one needs no comparison: eps near 1 is air on its own terms.
    assert permittivity_signal(1.1, None).label == "air_like"


@pytest.mark.parametrize(
    "eps,expected",
    [
        (PERMITTIVITY_AIR_LIKE_BELOW - 0.01, "air_like"),
        (PERMITTIVITY_AIR_LIKE_BELOW + 0.01, "fast"),
        (PERMITTIVITY_IMPLAUSIBLE_ABOVE - 0.01, "wet"),
        (PERMITTIVITY_IMPLAUSIBLE_ABOVE + 0.01, "implausible"),
    ],
)
def test_the_boundaries_land_where_the_constants_say(eps: float, expected: str) -> None:
    assert permittivity_signal(eps, SITE).label == expected


def test_every_signal_carries_a_note_a_person_can_act_on() -> None:
    for eps in (1.0, 3.0, 9.0, 40.0, 500.0):
        note = permittivity_signal(eps, SITE).note
        assert note and any(ch.isdigit() for ch in note), "the note must quote the value it read"
