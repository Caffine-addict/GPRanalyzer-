"""The classical detector, adapted to the orchestrator's DetectorLike seam.

`detect.classical.find_candidate_boxes` proposes regions; each is kept only if a hyperbola can be
fitted to it and that fit survives `studio.velocity.fit_rejection_reason` — the same "credible"
rule the batch reports use, so a live finding and a batch credible target mean the same thing.
The confidence reported is the fit's R^2: curve-fit quality, nothing more. That is exactly what
the `v1_candidate` prompt tells the model it is, which is why core/config.py refuses this backend
with any other prompt.

Lives in pipeline/ rather than detect/ because the credibility rule lives in studio/velocity.py;
the composition layer may import both, detect/ should not import studio/.
"""

from __future__ import annotations

import numpy as np

from core.contracts import Detection, ScanFrame
from detect.classical import find_candidate_boxes
from detect.shapes import POINT_REFLECTOR
from studio.velocity import fit_region, fit_rejection_reason

# Below this many traces the detector's per-row background (a median across traces) is not yet
# a background, so a partial line is not scanned until it has this much context. A complete
# line is always scanned, however short.
MIN_CONTEXT_TRACES = 64

_MIN_FIT_SPAN = 3  # fit_region's own floor on a region's size


class ClassicalDetector:
    def detect(self, image: np.ndarray, frame: ScanFrame) -> list[Detection]:
        """Credible hyperbolas in `frame`, as boxes in `image`'s pixel space.

        `image` is what the orchestrator rendered or received for this frame; boxes are found in
        the frame's native data and rescaled onto it, so refine/evidence map them back exactly.
        """
        native, sample_interval_ns, spacing_m = _native_data(frame)
        n_traces, n_samples = native.shape
        if not frame.line_complete and n_traces < MIN_CONTEXT_TRACES:
            return []

        height, width = image.shape[:2]
        detections = []
        for x, y, w, h in find_candidate_boxes(native, sample_interval_ns):
            r2 = _credible_fit_r2(native, (x, y, w, h), sample_interval_ns, spacing_m)
            if r2 is None:
                continue
            bbox = (x * width / n_traces, y * height / n_samples, (x + w) * width / n_traces, (y + h) * height / n_samples)
            detections.append(Detection(class_name=POINT_REFLECTOR, confidence=r2, bbox_xyxy=bbox))
        return detections


def _native_data(frame: ScanFrame) -> tuple[np.ndarray, float | None, float | None]:
    """(n_traces, n_samples) array to search, its sample interval, and its trace spacing.

    An image-only frame is searched as pseudo-traces (columns across, rows down) with no
    physical scale: boxes are still located, but no velocity can be measured off pixels.
    """
    if frame.traces is not None:
        return frame.traces, frame.sample_interval_ns, frame.trace_spacing_m
    assert frame.image is not None, "ScanFrame guarantees traces or image"
    return np.ascontiguousarray(frame.image.T).astype(np.float32), None, None


def _credible_fit_r2(
    native: np.ndarray,
    box: tuple[int, int, int, int],
    sample_interval_ns: float | None,
    spacing_m: float | None,
) -> float | None:
    """The box's hyperbola-fit R^2, or None if no credible hyperbola is there."""
    x, y, w, h = box
    # Unit scales when there are none: R^2 and the inlier count do not depend on units, and the
    # physical rejection rules (velocity, direct-wave time) are skipped below for that reason.
    if sample_interval_ns is not None and spacing_m is not None:
        dt, dx, physical = sample_interval_ns, spacing_m, True
    else:
        dt, dx, physical = 1.0, 1.0, False
    fit = fit_region(
        native,
        trace_start=x,
        sample_start=y,
        trace_span=max(w, _MIN_FIT_SPAN),
        sample_span=max(h, _MIN_FIT_SPAN),
        trace_spacing_m=dx,
        sample_interval_ns=dt,
    )
    if fit is None:
        return None
    if physical and fit_rejection_reason(fit, n_samples=native.shape[1], sample_interval_ns=dt) is not None:
        return None
    return float(min(max(fit.r2, 0.0), 1.0))
