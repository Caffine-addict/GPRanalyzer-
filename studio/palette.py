"""Colour lookup tables for radargram display.

Every palette is a 256x3 uint8 RGB table built by interpolating a handful of
control stops, so adding one means adding a stop list and nothing else.

Palette choice is not decoration in GPR work. A radargram is a *signed*
quantity — a reflection off a metal pipe and one off an air void differ mainly
by polarity — so a **diverging** palette that maps zero to a neutral mid-tone
(`seismic`, `grey`) shows polarity directly, while a **sequential** one
(`rainbow`, `amber`) throws polarity away in exchange for more visible
gradations. `is_diverging` records which is which so the renderer can normalise
symmetrically about zero for the ones where zero is meaningful, and callers can
warn when it isn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

_LUT_SIZE = 256


@dataclass(frozen=True)
class Palette:
    name: str
    label: str
    is_diverging: bool
    stops: tuple[tuple[float, tuple[int, int, int]], ...]  # (position 0-1, RGB)


_PALETTES: tuple[Palette, ...] = (
    Palette(
        name="grey",
        label="Greyscale",
        is_diverging=True,
        stops=((0.0, (0, 0, 0)), (0.5, (128, 128, 128)), (1.0, (255, 255, 255))),
    ),
    Palette(
        name="grey_inverse",
        label="Greyscale (inverted)",
        is_diverging=True,
        stops=((0.0, (255, 255, 255)), (0.5, (128, 128, 128)), (1.0, (0, 0, 0))),
    ),
    Palette(
        name="seismic",
        label="Seismic (blue/red)",
        is_diverging=True,
        stops=(
            (0.0, (5, 48, 120)),
            (0.25, (60, 130, 200)),
            (0.5, (247, 247, 247)),
            (0.75, (214, 96, 77)),
            (1.0, (120, 12, 20)),
        ),
    ),
    Palette(
        name="rainbow",
        label="Rainbow",
        is_diverging=False,
        stops=(
            (0.0, (0, 0, 90)),
            (0.2, (0, 90, 220)),
            (0.4, (0, 190, 190)),
            (0.6, (120, 220, 60)),
            (0.8, (250, 200, 0)),
            (1.0, (170, 0, 0)),
        ),
    ),
    Palette(
        name="amber",
        label="Amber",
        is_diverging=False,
        stops=(
            (0.0, (10, 6, 0)),
            (0.35, (110, 45, 5)),
            (0.7, (225, 150, 30)),
            (1.0, (255, 245, 200)),
        ),
    ),
)

_BY_NAME = {palette.name: palette for palette in _PALETTES}

DEFAULT_PALETTE = "grey"


def available() -> tuple[Palette, ...]:
    """Every palette, in menu order."""
    return _PALETTES


def get(name: str) -> Palette:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown palette: {name!r} (have {sorted(_BY_NAME)})") from None


@lru_cache(maxsize=len(_PALETTES))
def lut(name: str) -> np.ndarray:
    """The palette's 256x3 uint8 RGB lookup table."""
    palette = get(name)
    positions = np.array([stop for stop, _ in palette.stops], dtype=np.float64)
    colours = np.array([rgb for _, rgb in palette.stops], dtype=np.float64)
    samples = np.linspace(0.0, 1.0, _LUT_SIZE)
    channels = [np.interp(samples, positions, colours[:, c]) for c in range(3)]
    return np.rint(np.stack(channels, axis=1)).clip(0, 255).astype(np.uint8)
