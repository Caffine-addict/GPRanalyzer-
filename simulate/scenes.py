"""Random, physically plausible survey scenes, with every buried object known exactly.

A scene is everything gprMax needs to simulate one 9.6 m survey line: the ground, any
layering, and the buried objects. Because every object is placed here, *what* each one is
and *where* it sits is known without anyone labelling anything — that is the point of
simulating. Where each object ends up in the B-scan is then measured from the simulated
field (`simulate/labels.py`), not predicted from geometry.

Ranges are chosen around this site rather than GPR in general:
- soil permittivity 5-14, bracketing the file header's 9.0 and the fitted 8.8;
- objects only as deep as the 25.6 ns shallow-channel window can actually show;
- the antenna's measured 466 MHz, varied +/-10% so the detector does not learn one pulse.

The model is 2-D. A pipe is a cylinder running *across* the survey line, which is what
draws a hyperbola; a slab is a flat feature running *along* it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from detect.measure import hyperbola_half_aperture_m
from detect.shapes import DISTURBED_OR_VOID, LINEAR_REFLECTOR, POINT_REFLECTOR
from simulate import instrument
from studio.processing import SPEED_OF_LIGHT_M_PER_NS

# First trace to last trace, along the line.
LINE_LENGTH_M = (instrument.TRACES_PER_FRAME - 1) * instrument.TRACE_SPACING_M

PIPE_KINDS = ("metal", "plastic_air", "plastic_water", "concrete")
SLAB_KINDS = ("concrete", "metal")

# Latest two-way time an object can sit at and still show its apex plus ~3 ns of limb in
# the record: the window, minus the samples before the direct wave, minus that limb.
_USABLE_TWO_WAY_NS = (
    instrument.SAMPLES_PER_TRACE - instrument.DIRECT_WAVE_PEAK_SAMPLE - 30
) * instrument.SAMPLE_INTERVAL_NS
_MIN_DEPTH_M = 0.15
_EDGE_CLEARANCE_M = 0.4  # off the line ends, so a hyperbola keeps both limbs
_OBJECT_CLEARANCE_M = 0.05
_EMPTY_SCENE_FRACTION = 0.08  # background-only frames: the detector must learn "nothing here"
_PLACEMENT_ATTEMPTS = 60

# One area feature per scene at most. A void inside a trench under a slab gave three
# overlapping area boxes that no detector could be expected to tell apart; these weights
# keep each feature about as common as it was when the three were drawn independently.
_AREA_FEATURE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("none", 0.40),
    ("slab", 0.24),
    ("zone", 0.21),
    ("void", 0.15),
)

# The diffraction aperture does two jobs here: it spaces point reflectors far enough apart
# that their hyperbolas stay separable, and it bounds each one's label prior in
# `simulate/labels.py`. Holding both to the same number is what makes "one object, one box"
# true by construction rather than by luck. Defined in `detect/measure.py` because the
# Studio needs the same number to box a picked apex, and importing the other way round
# would be a cycle.


@dataclass(frozen=True)
class Medium:
    eps_r: float
    sigma: float  # S/m

    def __post_init__(self) -> None:
        if self.eps_r < 1.0:
            raise ValueError(f"relative permittivity cannot be below 1, got {self.eps_r}")
        if self.sigma < 0.0:
            raise ValueError(f"conductivity cannot be negative, got {self.sigma}")

    @property
    def velocity_m_per_ns(self) -> float:
        return SPEED_OF_LIGHT_M_PER_NS / math.sqrt(self.eps_r)


@dataclass(frozen=True)
class Layer:
    """Everything below `depth_m` has this medium, down to the next layer."""

    depth_m: float
    medium: Medium


@dataclass(frozen=True)
class Pipe:
    """A cylinder crossing the line. `depth_m` is to its top — what a locator reports."""

    x_m: float
    depth_m: float
    radius_m: float
    kind: str

    def __post_init__(self) -> None:
        _require_positive(radius_m=self.radius_m)
        _require_non_negative(depth_m=self.depth_m)
        if self.kind not in PIPE_KINDS:
            raise ValueError(f"unknown pipe kind {self.kind!r}")


@dataclass(frozen=True)
class Slab:
    """A flat feature running along the line: a duct run, a slab, a buried plate."""

    x_start_m: float
    x_end_m: float
    depth_m: float
    thickness_m: float
    kind: str

    def __post_init__(self) -> None:
        _require_span(self.x_start_m, self.x_end_m)
        _require_positive(thickness_m=self.thickness_m)
        if self.kind not in SLAB_KINDS:
            raise ValueError(f"unknown slab kind {self.kind!r}")


@dataclass(frozen=True)
class DisturbedZone:
    """Broken-up, heterogeneous ground — a backfilled trench, made up of mixed wet soil."""

    x_start_m: float
    x_end_m: float
    top_m: float
    bottom_m: float
    water_min: float  # volumetric water fraction range for gprMax's Peplinski soil model
    water_max: float
    seed: int

    def __post_init__(self) -> None:
        _require_span(self.x_start_m, self.x_end_m)
        _require_span(self.top_m, self.bottom_m)
        if not 0.0 < self.water_min < self.water_max < 1.0:
            raise ValueError(f"invalid water fraction range {self.water_min}-{self.water_max}")


@dataclass(frozen=True)
class Void:
    """An air-filled cavity."""

    x_start_m: float
    x_end_m: float
    top_m: float
    bottom_m: float

    def __post_init__(self) -> None:
        _require_span(self.x_start_m, self.x_end_m)
        _require_span(self.top_m, self.bottom_m)


@dataclass(frozen=True)
class Stone:
    """A rock or lump of rubble: a point reflector, like a small pipe.

    A single 2-D line cannot tell a 7 cm stone from a 7 cm cable; both draw the same
    hyperbola. So a stone is labelled `point_reflector` whenever it shows above the noise
    (`simulate/labels.py` decides that), and only the faint ones stay unlabelled clutter.
    Labelling none of them would teach the detector to ignore small hyperbolas, and then
    it would miss small cables.
    """

    x_m: float
    depth_m: float
    radius_m: float
    eps_r: float


LabelledObject = Pipe | Stone | Slab | DisturbedZone | Void


def shape_class_of(obj: LabelledObject) -> str:
    """The detector class an object is labelled as."""
    if isinstance(obj, Pipe | Stone):
        return POINT_REFLECTOR
    if isinstance(obj, Slab):
        return LINEAR_REFLECTOR
    if isinstance(obj, DisturbedZone | Void):
        return DISTURBED_OR_VOID
    raise TypeError(f"not a labelled object: {obj!r}")


@dataclass(frozen=True)
class Scene:
    scene_id: str
    seed: int
    soil: Medium
    layers: tuple[Layer, ...]
    pipes: tuple[Pipe, ...]
    slabs: tuple[Slab, ...]
    zones: tuple[DisturbedZone, ...]
    voids: tuple[Void, ...]
    stones: tuple[Stone, ...]
    frequency_mhz: float
    antenna_offset_m: float

    @property
    def labelled_objects(self) -> tuple[LabelledObject, ...]:
        # Point reflectors first (pipes, then stones), then areas: simulate/labels.py
        # builds its priors in exactly this order.
        return (*self.pipes, *self.stones, *self.slabs, *self.zones, *self.voids)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scene:
        """Rebuild a scene from `to_dict()` output, re-running every field's validation."""
        return cls(
            scene_id=str(raw["scene_id"]),
            seed=int(raw["seed"]),
            soil=Medium(**raw["soil"]),
            layers=tuple(Layer(depth_m=lyr["depth_m"], medium=Medium(**lyr["medium"])) for lyr in raw["layers"]),
            pipes=tuple(Pipe(**p) for p in raw["pipes"]),
            slabs=tuple(Slab(**s) for s in raw["slabs"]),
            zones=tuple(DisturbedZone(**z) for z in raw["zones"]),
            voids=tuple(Void(**v) for v in raw["voids"]),
            stones=tuple(Stone(**st) for st in raw["stones"]),
            frequency_mhz=float(raw["frequency_mhz"]),
            antenna_offset_m=float(raw["antenna_offset_m"]),
        )


def _require_positive(**values: float) -> None:
    for name, value in values.items():
        if value <= 0:
            raise ValueError(f"{name} must be positive, got {value}")


def _require_non_negative(**values: float) -> None:
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} cannot be negative, got {value}")


def _require_span(start: float, end: float) -> None:
    if end <= start:
        raise ValueError(f"span end ({end}) must be past its start ({start})")


def two_way_time_ns(depth_m: float, soil: Medium, layers: tuple[Layer, ...]) -> float:
    """Vertical two-way travel time from the surface down to `depth_m` through the layering."""
    tops = [(0.0, soil), *((layer.depth_m, layer.medium) for layer in sorted(layers, key=lambda lyr: lyr.depth_m))]
    total = 0.0
    for index, (top, medium) in enumerate(tops):
        if depth_m <= top:
            break
        bottom = tops[index + 1][0] if index + 1 < len(tops) else math.inf
        total += 2.0 * (min(depth_m, bottom) - top) / medium.velocity_m_per_ns
    return total


def max_visible_depth_m(soil: Medium, layers: tuple[Layer, ...]) -> float:
    """Deepest point whose reflection still lands inside the usable part of the record."""
    depth = _MIN_DEPTH_M
    while two_way_time_ns(depth + 0.01, soil, layers) <= _USABLE_TWO_WAY_NS:
        depth += 0.01
    return round(depth, 2)


def _log_uniform(rng: np.random.Generator, lo: float, hi: float) -> float:
    return float(np.exp(rng.uniform(np.log(lo), np.log(hi))))


def _random_layers(rng: np.random.Generator, soil: Medium) -> tuple[Layer, ...]:
    count = int(rng.choice([0, 1, 2], p=[0.45, 0.40, 0.15]))
    layers: list[Layer] = []
    for depth in np.sort(rng.uniform(0.3, 1.2, size=count)):
        if layers and depth - layers[-1].depth_m < 0.1:
            continue  # a sliver thinner than 10 cm adds cost and nothing the grid resolves well
        medium = Medium(
            eps_r=round(float(np.clip(soil.eps_r * rng.uniform(0.7, 1.4), 3.0, 20.0)), 2),
            sigma=round(_log_uniform(rng, 1e-3, 1e-2), 5),
        )
        layers.append(Layer(depth_m=round(float(depth), 3), medium=medium))
    return tuple(layers)


Box = tuple[float, float, float, float]  # x0, x1, top, bottom — along-line metres and depths


def _footprint(obj: Pipe | Slab | Void | DisturbedZone | Stone) -> Box:
    if isinstance(obj, Pipe | Stone):
        return (obj.x_m - obj.radius_m, obj.x_m + obj.radius_m, obj.depth_m, obj.depth_m + 2 * obj.radius_m)
    if isinstance(obj, Slab):
        return (obj.x_start_m, obj.x_end_m, obj.depth_m, obj.depth_m + obj.thickness_m)
    return (obj.x_start_m, obj.x_end_m, obj.top_m, obj.bottom_m)


def _overlaps(a: Box, b: Box, clearance: float = _OBJECT_CLEARANCE_M) -> bool:
    return not (
        a[1] + clearance <= b[0] or b[1] + clearance <= a[0] or a[3] + clearance <= b[2] or b[3] + clearance <= a[2]
    )


def _clear_of(candidate: Box, solids: list[Box]) -> bool:
    return not any(_overlaps(candidate, solid) for solid in solids)


def _points_stay_apart(candidate: Pipe | Stone, placed: Sequence[Pipe | Stone]) -> bool:
    """Whether `candidate`'s diagnostic hyperbola clears every one already placed.

    Depth-scaled, because a hyperbola's width is: a flat rule applied to pipes only left
    stones sitting a median 0.26 m from their neighbours, and their label boxes ran into
    each other. Two targets this far apart have priors that do not touch.
    """
    reach = hyperbola_half_aperture_m(candidate.depth_m)
    return all(abs(candidate.x_m - other.x_m) >= reach + hyperbola_half_aperture_m(other.depth_m) for other in placed)


def _random_x(rng: np.random.Generator, half_width: float) -> float:
    return float(rng.uniform(_EDGE_CLEARANCE_M + half_width, LINE_LENGTH_M - _EDGE_CLEARANCE_M - half_width))


def _place_voids(rng: np.random.Generator, max_depth: float, solids: list[Box]) -> tuple[Void, ...]:
    width = float(rng.uniform(0.3, 1.5))
    top = float(rng.uniform(0.15, max(0.16, max_depth * 0.6)))
    bottom = min(top + float(rng.uniform(0.1, 0.4)), max_depth)
    start = _random_x(rng, width / 2) - width / 2
    void = Void(round(start, 3), round(start + width, 3), round(top, 3), round(bottom, 3))
    solids.append(_footprint(void))
    return (void,)


def _place_zones(rng: np.random.Generator, max_depth: float, solids: list[Box]) -> tuple[DisturbedZone, ...]:
    """A trench of broken-up ground.

    Pipes and stones may sit inside one — a pipe laid in backfill is the commonest real
    layout there is — so a zone deliberately registers in nothing: `solids`, which pipes
    and stones keep out of, stays as it was. Slabs cannot collide with it because a scene
    carries at most one area feature.
    """
    for _ in range(_PLACEMENT_ATTEMPTS):
        width = float(rng.uniform(1.0, 3.0))
        top = float(rng.uniform(0.05, 0.3))
        bottom = min(top + float(rng.uniform(0.4, 0.9)), max_depth)
        start = _random_x(rng, width / 2) - width / 2
        water = float(rng.uniform(0.05, 0.15))
        zone = DisturbedZone(
            round(start, 3), round(start + width, 3), round(top, 3), round(bottom, 3),
            round(water, 3), round(water + 0.1, 3), int(rng.integers(0, 2**31 - 1)),
        )
        if _clear_of(_footprint(zone), solids):
            return (zone,)
    return ()


def _place_slabs(rng: np.random.Generator, max_depth: float, solids: list[Box]) -> tuple[Slab, ...]:
    for _ in range(_PLACEMENT_ATTEMPTS):
        length = float(rng.uniform(1.5, 5.0))
        start = _random_x(rng, length / 2) - length / 2
        slab = Slab(
            round(start, 3), round(start + length, 3),
            round(float(rng.uniform(0.2, max(0.21, max_depth * 0.8))), 3),
            round(float(rng.uniform(0.04, 0.12)), 3),
            str(rng.choice(SLAB_KINDS, p=[0.7, 0.3])),
        )
        if _clear_of(_footprint(slab), solids):
            solids.append(_footprint(slab))
            return (slab,)
    return ()


def _place_pipes(rng: np.random.Generator, max_depth: float, solids: list[Box]) -> tuple[Pipe, ...]:
    count = int(rng.choice([1, 2, 3], p=[0.45, 0.35, 0.20]))
    pipes: list[Pipe] = []
    for _ in range(count):
        for _ in range(_PLACEMENT_ATTEMPTS):
            radius = _log_uniform(rng, 0.015, 0.15)
            deepest = max(max_depth - 2 * radius, _MIN_DEPTH_M + 0.01)
            pipe = Pipe(
                round(_random_x(rng, radius), 3),
                round(float(rng.uniform(_MIN_DEPTH_M, deepest)), 3),
                round(radius, 4),
                str(rng.choice(PIPE_KINDS, p=[0.35, 0.25, 0.20, 0.20])),
            )
            if _points_stay_apart(pipe, pipes) and _clear_of(_footprint(pipe), solids):
                pipes.append(pipe)
                solids.append(_footprint(pipe))
                break
    return tuple(pipes)


def _place_stones(
    rng: np.random.Generator, max_depth: float, solids: list[Box], points: Sequence[Pipe | Stone]
) -> tuple[Stone, ...]:
    stones: list[Stone] = []
    for _ in range(int(rng.integers(0, 4))):
        for _ in range(_PLACEMENT_ATTEMPTS):
            radius = float(rng.uniform(0.012, 0.04))
            stone = Stone(
                round(float(rng.uniform(0.1, LINE_LENGTH_M - 0.1)), 3),
                round(float(rng.uniform(0.05, max(0.06, max_depth - 2 * radius))), 3),
                round(radius, 4),
                round(float(rng.uniform(4.0, 8.0)), 2),
            )
            if _points_stay_apart(stone, [*points, *stones]) and _clear_of(_footprint(stone), solids):
                stones.append(stone)
                solids.append(_footprint(stone))  # the next stone must clear this one too
                break
    return tuple(stones)


def random_scene(scene_index: int, base_seed: int) -> Scene:
    """One reproducible random scene. The same (index, base_seed) always gives the same scene."""
    seed = base_seed * 1_000_003 + scene_index
    rng = np.random.default_rng(seed)

    soil = Medium(eps_r=round(float(rng.uniform(5.0, 14.0)), 2), sigma=round(_log_uniform(rng, 1e-3, 1e-2), 5))
    layers = _random_layers(rng, soil)
    max_depth = max_visible_depth_m(soil, layers)
    frequency = round(instrument.CENTRE_FREQUENCY_MHZ * float(rng.uniform(0.9, 1.1)), 1)
    offset = round(instrument.ASSUMED_ANTENNA_OFFSET_M * float(rng.uniform(0.8, 1.4)), 3)

    solids: list[Box] = []  # nothing may overlap these
    empty = rng.random() < _EMPTY_SCENE_FRACTION
    kinds, weights = zip(*_AREA_FEATURE_WEIGHTS, strict=True)
    feature = "none" if empty else str(rng.choice(kinds, p=weights))
    voids = _place_voids(rng, max_depth, solids) if feature == "void" else ()
    zones = _place_zones(rng, max_depth, solids) if feature == "zone" else ()
    slabs = _place_slabs(rng, max_depth, solids) if feature == "slab" else ()
    pipes = () if empty else _place_pipes(rng, max_depth, solids)
    # An empty frame has to be empty: stones are labelled when visible, so it gets none.
    stones = () if empty else _place_stones(rng, max_depth, solids, pipes)

    return Scene(
        scene_id=f"scene_{scene_index:05d}",
        seed=seed,
        soil=soil,
        layers=layers,
        pipes=pipes,
        slabs=slabs,
        zones=zones,
        voids=voids,
        stones=stones,
        frequency_mhz=frequency,
        antenna_offset_m=offset,
    )
