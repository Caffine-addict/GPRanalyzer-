"""Where each buried object actually shows up in the simulated B-scan — measured, not guessed.

Geometry says roughly where to look: a pipe's or stone's hyperbola, a slab's band, a zone's region
(the *prior*). The scattered field says exactly what is there. An object's box is the
extent of its scattered energy inside its prior.

Why not just draw the box from geometry? Because how far a hyperbola's limbs reach, how
long a void rings, how visible a deep plastic pipe is at all — those come out of the
physics, not out of a formula written in advance. A geometric box would teach the
detector our formula.

An object whose energy never clears the noise gets **no box**. Labelling a target the
instrument could not have shown would train the detector to hallucinate targets.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from detect.measure import hyperbola_half_aperture_m
from detect.shapes import SHAPE_CLASSES
from simulate import instrument
from simulate.scenes import (
    DisturbedZone,
    LabelledObject,
    Pipe,
    Scene,
    Slab,
    Stone,
    Void,
    shape_class_of,
    two_way_time_ns,
)
from studio.processing import SPEED_OF_LIGHT_M_PER_NS

_VISIBILITY_SIGMA = 4.0  # an object's peak must clear this many noise standard deviations
_FOOTPRINT_FRACTION = 0.15  # pixels at >= 15% of the object's own peak count as its footprint
_WAVELET_PERIODS = 1.5  # prior band height around a predicted arrival
_ENVELOPE_SAMPLES = 7  # smooths the wavelet's zero crossings out of the energy map
_TRIM_PERCENTILE = 1.0  # ignore the outermost 1% of footprint pixels: stray speckle, not extent
_MIN_BOX_SAMPLES = 6
_MIN_BOX_TRACES = 6
_DOMINANCE_RATIO = 3.0  # a point reflector this many times stronger claims its whole band
_EDGE_ALLOWANCE_M = 0.4  # diffraction off a slab or zone edge reaches past the object itself

# Propagation speed inside a pipe's fill, for how long it can ring after the top reflection.
_FILL_VELOCITY_M_PER_NS = {"metal": None, "concrete": 0.122, "plastic_air": 0.2998, "plastic_water": 0.0335}


@dataclass(frozen=True)
class LabelBox:
    shape_class: str
    source: str  # the scene object it measures, e.g. "Void#0" — provenance, and what validation keys on
    trace_start: int  # inclusive, native trace index
    trace_end: int
    sample_start: int  # inclusive, native sample index
    sample_end: int

    def to_yolo(self, n_traces: int, n_samples: int) -> str:
        """One YOLO label line, normalised to the rendered image (distance across, time down)."""
        class_id = SHAPE_CLASSES.index(self.shape_class)
        x0, x1 = self.trace_start / n_traces, (self.trace_end + 1) / n_traces
        y0, y1 = self.sample_start / n_samples, (self.sample_end + 1) / n_samples
        return f"{class_id} {(x0 + x1) / 2:.6f} {(y0 + y1) / 2:.6f} {x1 - x0:.6f} {y1 - y0:.6f}"


@dataclass(frozen=True)
class LabelResult:
    boxes: tuple[LabelBox, ...]
    unlabelled: tuple[str, ...]  # objects that got no box, each with the reason


def _sample_at(two_way_ns: np.ndarray | float) -> np.ndarray | float:
    return instrument.DIRECT_WAVE_PEAK_SAMPLE + np.asarray(two_way_ns) / instrument.SAMPLE_INTERVAL_NS


def _trace_at(along_m: float) -> float:
    return along_m / instrument.TRACE_SPACING_M


def _band_samples(scene: Scene) -> float:
    return _WAVELET_PERIODS * (1000.0 / scene.frequency_mhz) / instrument.SAMPLE_INTERVAL_NS


PointObject = Pipe | Stone


def _point_curve(scene: Scene, obj: PointObject, n_traces: int) -> np.ndarray:
    """Predicted sample of a point reflector's hyperbola at every trace."""
    apex_ns = two_way_time_ns(obj.depth_m, scene.soil, scene.layers)
    velocity = 2 * obj.depth_m / apex_ns if apex_ns > 0 else scene.soil.velocity_m_per_ns
    offsets = np.arange(n_traces) * instrument.TRACE_SPACING_M - obj.x_m
    return np.asarray(_sample_at(2 * np.sqrt(offsets**2 + obj.depth_m**2) / velocity))


def _ring_samples(obj: PointObject) -> float:
    """How long the object keeps echoing after its top reflection, in samples."""
    fill_velocity: float | None
    if isinstance(obj, Stone):
        fill_velocity = SPEED_OF_LIGHT_M_PER_NS / float(np.sqrt(obj.eps_r))
    else:
        fill_velocity = _FILL_VELOCITY_M_PER_NS[obj.kind]
    if fill_velocity is None:  # metal: everything reflects off the top
        return 0.0
    return (2 * 2 * obj.radius_m / fill_velocity) / instrument.SAMPLE_INTERVAL_NS


def _point_bands(scene: Scene, shape: tuple[int, int]) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    """Point reflectors' hyperbola bands (pipes first, then stones): own, split, and all echo.

    Two different questions need two different masks, and conflating them is a bug that
    collapses trench boxes onto the pipe inside them.

    **Where may this object's own box extend?** Its band down the record — the predicted
    curve, a wavelet thick, plus however long the object rings — cut to its diffraction
    aperture along the line (`scenes.hyperbola_half_aperture_m`). Without the aperture the
    band chased the limbs to the edge of the frame and on down to the record floor, and any
    energy along that tail stretched the box with it: on real simulator output, boxes up to
    2.95 m wide and 218 of 256 samples tall, covering most of the frame and saying nothing
    about where the target was. In the split version, a pixel two bands both claim belongs
    to whichever curve it is nearer, so a neighbour's limb cannot inflate either one.

    **Where could any point reflector's echo be?** The same bands with *no* aperture cut,
    unioned — returned as `echo`. Areas subtract this. A hyperbola outshines a trench's
    scatter by an order of magnitude, so any fragment of one left inside a trench's prior
    sets the trench's 15%-of-peak threshold and its box collapses onto the pipe. Bounding
    that mask by the aperture leaves exactly such fragments just outside it.
    """
    n_samples, n_traces = shape
    rows = np.arange(n_samples)[:, None]
    along_m = np.arange(n_traces)[None, :] * instrument.TRACE_SPACING_M
    band = _band_samples(scene)
    points: tuple[PointObject, ...] = (*scene.pipes, *scene.stones)
    curves = [_point_curve(scene, obj, n_traces) for obj in points]
    whole_curves = [
        (rows >= curve[None, :] - band / 2) & (rows <= curve[None, :] + band + _ring_samples(obj))
        for obj, curve in zip(points, curves, strict=True)
    ]
    echo = np.any(np.stack(whole_curves), axis=0) if whole_curves else np.zeros(shape, dtype=bool)
    bands = [
        whole & (np.abs(along_m - obj.x_m) <= hyperbola_half_aperture_m(obj.depth_m))
        for obj, whole in zip(points, whole_curves, strict=True)
    ]
    if len(curves) < 2:
        return bands, bands, echo
    nearest = np.argmin(np.stack([np.abs(rows - curve[None, :]) for curve in curves]), axis=0)
    return bands, [whole & (nearest == index) for index, whole in enumerate(bands)], echo


def _own_strengths(bands: list[np.ndarray], split: list[np.ndarray], energy: np.ndarray) -> list[float]:
    """Each point reflector's peak, read where it is unambiguously itself.

    That is the part of its band no other band overlaps. The overlap is exactly where a
    neighbour's echo would inflate the reading — reading strength there once made a faint
    stone look as bright as the pipe beside it.
    """
    strengths = []
    for index, whole in enumerate(bands):
        others = [band for other, band in enumerate(bands) if other != index]
        clear = whole & ~np.any(np.stack(others), axis=0) if others else whole
        region = clear if clear.any() else split[index]
        strengths.append(float(np.where(region, energy, 0.0).max()))
    return strengths


def _yield_to_dominant(bands: list[np.ndarray], split: list[np.ndarray], strengths: list[float]) -> list[np.ndarray]:
    """A faint point reflector gives up the whole band of any far stronger one.

    Splitting by nearness is fair between comparable targets. It is not fair to a faint
    stone near a metal pipe: where the curves cross, the pipe's limb is brighter than the
    stone's own apex, sets the stone's 15%-of-peak threshold, and the stone's box moves
    onto the pipe. So a point reflector at least `_DOMINANCE_RATIO` times stronger claims
    its whole band. The weaker one may then get no box — the honest outcome when it
    cannot be told apart from its neighbour.

    Areas need no rule of their own: a point reflector's band now stops at its diffraction
    aperture, so it cannot reach a slab's or void's echo lying far along its limb. That is
    the case two earlier attempts at an area-dominance rule failed to fix, one by reading
    strength circularly and one by breaking pipes that lie inside trenches.
    """
    priors = []
    for index, prior in enumerate(split):
        for other, whole in enumerate(bands):
            if other != index and strengths[other] >= _DOMINANCE_RATIO * strengths[index]:
                prior = prior & ~whole
        priors.append(prior)
    return priors


def _region_prior(scene: Scene, shape: tuple[int, int], x0: float, x1: float, s0: float, s1: float) -> np.ndarray:
    n_samples, n_traces = shape
    rows = np.arange(n_samples)[:, None]
    cols = np.arange(n_traces)[None, :]
    band = _band_samples(scene)
    t0 = _trace_at(x0 - _EDGE_ALLOWANCE_M)
    t1 = _trace_at(x1 + _EDGE_ALLOWANCE_M)
    return (cols >= t0) & (cols <= t1) & (rows >= s0 - band / 2) & (rows <= s1 + band)


def _object_prior(scene: Scene, obj: LabelledObject, shape: tuple[int, int]) -> np.ndarray:
    soil, layers = scene.soil, scene.layers
    if isinstance(obj, Slab):
        top = float(_sample_at(two_way_time_ns(obj.depth_m, soil, layers)))
        bottom = float(_sample_at(two_way_time_ns(obj.depth_m + obj.thickness_m, soil, layers)))
        return _region_prior(scene, shape, obj.x_start_m, obj.x_end_m, top, bottom)
    if isinstance(obj, Void):
        top = float(_sample_at(two_way_time_ns(obj.top_m, soil, layers)))
        # Inside the void the pulse moves at the speed of light; the base echo comes early.
        bottom = top + (2 * (obj.bottom_m - obj.top_m) / 0.2998) / instrument.SAMPLE_INTERVAL_NS
        return _region_prior(scene, shape, obj.x_start_m, obj.x_end_m, top, bottom)
    if isinstance(obj, DisturbedZone):
        top = float(_sample_at(two_way_time_ns(obj.top_m, soil, layers)))
        bottom = float(_sample_at(two_way_time_ns(obj.bottom_m, soil, layers)))
        return _region_prior(scene, shape, obj.x_start_m, obj.x_end_m, top, bottom)
    raise TypeError(f"no prior for {obj!r}")


def energy_envelope(scattered: np.ndarray) -> np.ndarray:
    """Smoothed amplitude of the scattered field, so a box follows the wavelet, not its zero crossings."""
    kernel = np.ones(_ENVELOPE_SAMPLES) / _ENVELOPE_SAMPLES
    power = np.apply_along_axis(lambda column: np.convolve(column**2, kernel, mode="same"), 0, scattered)
    return np.sqrt(power)


def _measure_box(energy: np.ndarray, prior: np.ndarray, sigma: float, shape_class: str, source: str) -> LabelBox | str:
    inside = np.where(prior, energy, 0.0)
    peak = float(inside.max())
    if sigma > 0 and peak < _VISIBILITY_SIGMA * sigma:
        return f"peak {peak / sigma:.1f} sigma, below the {_VISIBILITY_SIGMA:.0f} sigma visibility bar"
    if peak <= 0:
        return "no scattered energy inside its prior"

    rows, cols = np.nonzero(inside >= _FOOTPRINT_FRACTION * peak)
    lo, hi = _TRIM_PERCENTILE, 100.0 - _TRIM_PERCENTILE
    r0, r1 = (int(v) for v in np.round(np.percentile(rows, [lo, hi])))
    c0, c1 = (int(v) for v in np.round(np.percentile(cols, [lo, hi])))
    n_samples, n_traces = energy.shape
    r0, r1 = _widen(r0, r1, _MIN_BOX_SAMPLES, n_samples)
    c0, c1 = _widen(c0, c1, _MIN_BOX_TRACES, n_traces)
    return LabelBox(shape_class, source, trace_start=c0, trace_end=c1, sample_start=r0, sample_end=r1)


def _widen(start: int, end: int, minimum: int, limit: int) -> tuple[int, int]:
    """Grow a span symmetrically to `minimum` cells, kept inside [0, limit)."""
    missing = minimum - (end - start + 1)
    if missing > 0:
        start -= missing // 2
        end += missing - missing // 2
    start, end = max(0, start), min(limit - 1, end)
    return start, end


def _require_apex(box: LabelBox, obj: PointObject, scene: Scene) -> LabelBox | str:
    """A point reflector's box has to contain its own apex.

    Its prior band runs out along both limbs, so a faint object can pick up a neighbour's
    echo far down one limb while its own apex never shows. That box would sit up to a
    metre from the object and teach the detector the wrong place — seen on real
    simulator output as a stone at 8.37 m boxed at 7.45-7.73 m, and as a stone 6 cm deep
    boxed on a void's reverberations 20+ samples below its apex. So the box must span the
    apex's trace and start no more than half a band below its row.
    """
    apex_trace = obj.x_m / instrument.TRACE_SPACING_M
    apex_sample = float(_sample_at(two_way_time_ns(obj.depth_m, scene.soil, scene.layers)))
    reaches_apex = box.sample_start <= apex_sample + _band_samples(scene) / 2  # half a band: where the echo begins
    if box.trace_start <= apex_trace <= box.trace_end and reaches_apex:
        return box
    spacing = instrument.TRACE_SPACING_M
    return (
        f"its footprint ({box.trace_start * spacing:.2f}-{box.trace_end * spacing:.2f} m) misses its own apex "
        f"at {obj.x_m:.2f} m — the energy there is a neighbour's"
    )


def label_scene(scene: Scene, scattered: np.ndarray, sigma: float) -> LabelResult:
    """Boxes for every visible labelled object in `scene`.

    `scattered` is the gained, noise-free scattered field on the instrument grid,
    (n_samples, n_traces); `sigma` is the noise that will be added to the frame, which
    sets the visibility bar.
    """
    shape = scattered.shape
    energy = energy_envelope(scattered)
    bands, split, point_echo = _point_bands(scene, shape)
    # A point reflector's hyperbola is far stronger than a trench's scatter or a slab's
    # edge. Left in, it sets the area's 15%-of-peak footprint threshold and the area's box
    # collapses onto it — so an area is measured with every point reflector's echo taken
    # out, aperture or no aperture (see `_point_bands`).
    areas: tuple[Slab | DisturbedZone | Void, ...] = (*scene.slabs, *scene.zones, *scene.voids)
    area_priors = [_object_prior(scene, obj, shape) & ~point_echo for obj in areas]
    point_priors = _yield_to_dominant(bands, split, _own_strengths(bands, split, energy))
    priors = [*point_priors, *area_priors]
    objects = scene.labelled_objects
    boxes: list[LabelBox] = []
    unlabelled: list[str] = []
    seen: Counter[str] = Counter()
    for obj, prior in zip(objects, priors, strict=True):
        kind = type(obj).__name__
        source = f"{kind}#{seen[kind]}"
        seen[kind] += 1
        result = _measure_box(energy, prior, sigma, shape_class_of(obj), source)
        if isinstance(result, LabelBox) and isinstance(obj, Pipe | Stone):
            result = _require_apex(result, obj, scene)
        if isinstance(result, LabelBox):
            boxes.append(result)
        else:
            unlabelled.append(f"{type(obj).__name__} {obj}: {result}")
    return LabelResult(boxes=tuple(boxes), unlabelled=tuple(unlabelled))
