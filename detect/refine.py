"""Shape detections -> taxonomy classes, decided from measurements.

The detector answers "what shape is this?" (`detect/shapes.py`). Risk scoring, the
reasoning prompt and the reports all speak the project's 9-class taxonomy. This module
bridges the two with things that can be measured, rather than distinctions a detector
would have to learn from labels that do not exist:

    point_reflector   -> intersecting_linear_and_point_reflector   sits on a linear reflector
                      -> multiple_point_reflectors                 3+ at a regular spacing (rebar, duct banks)
                      -> cluttered_multi_target                    crowded in with 2+ others, irregularly
                      -> clear_point_reflector                     stands well out of the ground
                      -> low_snr_point_reflector                   barely does — or cannot be measured
    linear_reflector  -> elongated_linear_target
    disturbed_or_void -> cavities                                  top echo has an air gap's polarity
                      -> disturbed_zone                            otherwise, or polarity unreadable

Rules the mapping holds to:

- **A class is only as specific as its evidence.** Amplitude and polarity need the raw
  traces. On an image-only frame they cannot be measured, so a point target is reported
  as low-SNR and an area as `disturbed_zone`. Low-SNR is deliberately the fallback: the
  risk model weights it above "clear", so an unmeasurable target errs toward a second
  look rather than being waved through.
- **Taxonomy detections pass straight through.** A detector trained on the taxonomy
  itself — once the company's labelled ground truth exists — needs no refinement, and
  the pipeline should not care which kind of detector it is running.
- **`strong_high_contrast_reflector` is never produced.** Telling it apart needs a
  calibrated amplitude the system does not have yet (docs/COMPANY_QUESTIONS.md #2).

The cavity rule (polarity) is standard interpretation practice but **not yet validated on
this instrument**; the synthetic scenes, which contain both voids and trenches with known
answers, are how it gets validated before anyone relies on a `cavities` call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import numpy as np

from core.contracts import Detection
from detect.measure import (
    STRONG_AMPLITUDE_THRESHOLD,
    compute_normalized_envelope,
    echo_matches_direct_wave_polarity,
)
from detect.shapes import DISTURBED_OR_VOID, LINEAR_REFLECTOR, POINT_REFLECTOR

logger = logging.getLogger(__name__)

CLEAR_POINT = "clear_point_reflector"
LOW_SNR_POINT = "low_snr_point_reflector"
MULTIPLE_POINTS = "multiple_point_reflectors"
CLUTTERED = "cluttered_multi_target"
INTERSECTING = "intersecting_linear_and_point_reflector"
ELONGATED = "elongated_linear_target"
CAVITIES = "cavities"
DISTURBED = "disturbed_zone"

# Every class this module can produce; config.yaml's detection.taxonomy must contain them all.
REFINED_CLASSES = frozenset({CLEAR_POINT, LOW_SNR_POINT, MULTIPLE_POINTS, CLUTTERED, INTERSECTING, ELONGATED, CAVITIES, DISTURBED})

_MIN_GROUP = 3
_REGULAR_SPACING_CV = 0.25  # gap spread / mean gap below this reads as deliberate spacing
_SAME_DEPTH_FRACTION = 0.10  # a regular row sits within 10% of the image height
_CROWD_WIDTH_FRACTION = 0.15  # "crowded": 2+ other points within 15% of the image width


@dataclass(frozen=True)
class Refinement:
    detection: Detection  # carrying its taxonomy class
    shape: str | None  # the shape it was refined from; None if it arrived as a taxonomy class
    rule: str  # why it got this class — logged, and what a reviewer checks


def _centre(detection: Detection) -> tuple[float, float]:
    x1, y1, x2, y2 = detection.bbox_xyxy
    return (x1 + x2) / 2, (y1 + y2) / 2


def _overlaps(a: Detection, b: Detection) -> bool:
    ax1, ay1, ax2, ay2 = a.bbox_xyxy
    bx1, by1, bx2, by2 = b.bbox_xyxy
    return ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2


def _is_regular_row(points: list[Detection], image_shape: tuple[int, ...]) -> bool:
    if len(points) < _MIN_GROUP:
        return False
    xs = np.sort([_centre(p)[0] for p in points])
    ys = [_centre(p)[1] for p in points]
    gaps = np.diff(xs)
    if gaps.mean() <= 0:
        return False
    same_depth = (max(ys) - min(ys)) / image_shape[0] < _SAME_DEPTH_FRACTION
    return bool(gaps.std() / gaps.mean() < _REGULAR_SPACING_CV and same_depth)


def _is_crowded(point: Detection, points: list[Detection], image_shape: tuple[int, ...]) -> bool:
    x = _centre(point)[0]
    reach = _CROWD_WIDTH_FRACTION * image_shape[1]
    neighbours = sum(1 for other in points if other is not point and abs(_centre(other)[0] - x) <= reach)
    return neighbours >= _MIN_GROUP - 1


def box_in_traces(
    bbox_xyxy: tuple[float, float, float, float], image_shape: tuple[int, ...], traces_shape: tuple[int, int]
) -> tuple[slice, slice]:
    """(sample slice, trace slice) a box covers, for an image rendered by render.bscan.traces_to_image.

    That renderer draws distance across and time down, stretching n_traces over the
    image width and n_samples over its height, so the mapping is a pure rescale.
    """
    height, width = image_shape[:2]
    n_traces, n_samples = traces_shape
    x1, y1, x2, y2 = bbox_xyxy
    t0 = max(0, int(np.floor(x1 / width * n_traces)))
    t1 = min(n_traces, max(t0 + 1, int(np.ceil(x2 / width * n_traces))))
    s0 = max(0, int(np.floor(y1 / height * n_samples)))
    s1 = min(n_samples, max(s0 + 1, int(np.ceil(y2 / height * n_samples))))
    return slice(s0, s1), slice(t0, t1)


def _point_by_amplitude(detection: Detection, image_shape: tuple[int, ...], traces: np.ndarray | None, envelope: np.ndarray | None) -> tuple[str, str]:
    if traces is None or envelope is None:
        return LOW_SNR_POINT, "amplitude not measurable without raw traces — reported as the low-SNR class"
    rows, cols = box_in_traces(detection.bbox_xyxy, image_shape, traces.shape)
    peak = float(envelope[rows, cols].max())
    if peak >= STRONG_AMPLITUDE_THRESHOLD:
        return CLEAR_POINT, f"peak normalised amplitude {peak:.1f} >= {STRONG_AMPLITUDE_THRESHOLD}"
    return LOW_SNR_POINT, f"peak normalised amplitude {peak:.1f} < {STRONG_AMPLITUDE_THRESHOLD}"


def _area(
    detection: Detection, image_shape: tuple[int, ...], traces: np.ndarray | None, sample_interval_ns: float | None
) -> tuple[str, str]:
    if traces is None or sample_interval_ns is None:
        return DISTURBED, "polarity not measurable without raw traces"
    rows, cols = box_in_traces(detection.bbox_xyxy, image_shape, traces.shape)
    matches = echo_matches_direct_wave_polarity(traces, rows, cols, sample_interval_ns)
    if matches is None:
        return DISTURBED, "top echo too weak to read its polarity"
    if matches:
        return CAVITIES, "top echo has the direct wave's polarity: a drop in permittivity, as into air (rule not yet validated)"
    return DISTURBED, "top echo polarity reversed from the direct wave: not an air-filled gap"


def refine_detections(
    detections: list[Detection],
    *,
    taxonomy: tuple[str, ...],
    image_shape: tuple[int, ...],
    traces: np.ndarray | None,
    sample_interval_ns: float | None = None,
) -> list[Refinement]:
    """Give every detection a taxonomy class.

    `traces` are the frame's raw traces, (n_traces, n_samples), and only when
    `image_shape` is the image `render.bscan.traces_to_image` made from them — pass None
    otherwise, since box coordinates then have no known mapping onto samples. `sample_interval_ns`
    is required whenever `traces` is given: the direct-wave/ringing band excluded before
    measuring amplitude or polarity is a fixed *time* (DIRECT_WAVE_WINDOW_NS in detect/measure.py),
    not a fraction of the record, so converting it to a sample count needs the real sample rate.
    """
    if traces is not None and sample_interval_ns is None:
        raise ValueError("sample_interval_ns is required whenever traces is given")
    points = [d for d in detections if d.class_name == POINT_REFLECTOR]
    linears = [d for d in detections if d.class_name == LINEAR_REFLECTOR]
    regular_row = _is_regular_row(points, image_shape)
    envelope = (
        compute_normalized_envelope(traces, sample_interval_ns)
        if traces is not None and sample_interval_ns is not None and points
        else None
    )

    refinements: list[Refinement] = []
    for detection in detections:
        shape = detection.class_name
        if shape in taxonomy:
            refinements.append(Refinement(detection, None, "already a taxonomy class"))
            continue
        if shape == POINT_REFLECTOR:
            if any(_overlaps(detection, linear) for linear in linears):
                target, rule = INTERSECTING, "overlaps a linear reflector"
            elif regular_row:
                target, rule = MULTIPLE_POINTS, f"one of {len(points)} point reflectors at a regular spacing"
            elif _is_crowded(detection, points, image_shape):
                target, rule = CLUTTERED, "crowded in with other point reflectors at irregular spacing"
            else:
                target, rule = _point_by_amplitude(detection, image_shape, traces, envelope)
        elif shape == LINEAR_REFLECTOR:
            target, rule = ELONGATED, "linear reflector"
        elif shape == DISTURBED_OR_VOID:
            target, rule = _area(detection, image_shape, traces, sample_interval_ns)
        else:
            logger.warning("refine.drop reason=unknown_class class_name=%r", shape)
            continue

        if target not in taxonomy:
            # A config whose taxonomy lacks a class this mapping produces is a deployment
            # mistake; drop loudly rather than emit a class risk scoring has never seen.
            logger.error("refine.drop reason=class_not_in_taxonomy target=%r shape=%r", target, shape)
            continue
        refinements.append(Refinement(replace(detection, class_name=target), shape, rule))
        logger.info("refine.class shape=%s class=%s rule=%s", shape, target, rule)
    return refinements
