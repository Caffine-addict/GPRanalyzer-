"""The ground's dielectric across a site: measured on each line, grouped into sections.

Each line's value is measured the way Field mode measures a whole site (studio/site_map
.site_dielectric): the semblance of the line's clearest hyperbolas, summed, read at its peak. It
needs MIN_ARCHES clear arches; a line with fewer is "not measured", never filled in from its
neighbours. One line's few arches are noisy, so a new section starts only where two neighbouring
lines agree with each other and differ from the section so far; a lone line that differs stays in
its section, marked as an outlier. On 116 excavated Twente surveys this measurement was off by a median 22%, and it reads
low in wet ground (above site_map.WET_SOIL_EPS), so neighbouring lines are only split into separate
sections when they differ by more than SECTION_SPLIT.

What this cannot say is the soil type. Dielectric mostly tracks water content: dry sand, moist
loam and wet clay can share a value. Naming soils needs trial pits or borehole logs.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from studio import runs, site_map
from studio.processing import SPEED_OF_LIGHT_M_PER_NS
from studio.velocity import is_peaked, stacked_permittivity

MIN_ARCHES = 3  # stacked_permittivity's own floor: fewer clear arches is too few to say
SECTION_SPLIT = 0.25  # relative difference between neighbours that starts a new section (> the 22% error)
TYPICAL_ERROR = 0.22  # median error of this measurement on the excavated Twente surveys


def _curves(job_dir_str: str) -> list[np.ndarray]:
    return [np.array(c) for c in site_map._semblance_curves(job_dir_str, site_map._mtime(Path(job_dir_str)))]


def _measure(curves: Sequence[np.ndarray]) -> tuple[float | None, int]:
    peaked = sum(1 for c in curves if is_peaked(c))
    return stacked_permittivity(curves, min_arches=MIN_ARCHES), peaked


def sections(scene: dict[str, Any], job_dirs: dict[str, str]) -> dict[str, Any]:
    """Each placed line's measured dielectric, and runs of neighbouring lines that agree.

    `scene` is site3d.scene (placed lines with their paths); `job_dirs` maps every line of the site,
    placed or not, to its folder, since a line's dielectric does not need its position.
    """
    curves = {job: _curves(path) for job, path in job_dirs.items()}
    measured: dict[str, float | None] = {}
    clear: dict[str, int] = {}
    for job in job_dirs:
        value, clear[job] = _measure(curves[job])
        measured[job] = None if value is None else round(value, 2)

    def stacked(members: Sequence[str]) -> float | None:
        return stacked_permittivity([c for j in members for c in curves[j]], min_arches=MIN_ARCHES)

    def differs(a: float, b: float) -> bool:
        return abs(a - b) / min(a, b) > SECTION_SPLIT

    found: list[list[str]] = []
    outliers: set[str] = set()
    for family in runs.families([line for line in scene["lines"] if len(line["path"]) >= 2]):
        # Measured lines in order across the site; an unmeasured line neither joins nor splits a section.
        order = [line["job"] for line in family if measured[line["job"]] is not None]
        current: list[str] = []
        for i, job in enumerate(order):
            value = measured[job]
            assert value is not None  # `order` holds measured lines only
            here = stacked(current) if current else None
            if here is None or not differs(value, here):
                current.append(job)
                continue
            # One line's few arches are noisy: a new section needs the next line to agree with it.
            following = measured[order[i + 1]] if i + 1 < len(order) else None
            if following is not None and not differs(following, value) and differs(following, here):
                found.append(current)
                current = [job]
            else:
                current.append(job)
                outliers.add(job)
        if current:
            found.append(current)
    lines = [{"job": job, "dielectric": value, "clear_arches": clear[job], "outlier": job in outliers,
              "wet": value is not None and value > site_map.WET_SOIL_EPS} for job, value in measured.items()]
    zones = []
    for members in found:
        value = stacked_permittivity([c for j in members for c in curves[j]], min_arches=MIN_ARCHES)
        if value is None:
            continue
        values = [v for j in members if (v := measured[j]) is not None]
        zones.append({"lines": members, "dielectric": round(value, 2), "range": [min(values), max(values)],
                      "velocity_m_per_ns": round(SPEED_OF_LIGHT_M_PER_NS / float(np.sqrt(value)), 4),
                      "wet": value > site_map.WET_SOIL_EPS, "outliers": [j for j in members if j in outliers]})
    return {"lines": lines, "sections": zones, "typical_error": TYPICAL_ERROR, "section_split": SECTION_SPLIT,
            "min_arches": MIN_ARCHES,
            "note": ("Measured from each line's own clearest hyperbolas. Typical error about 22% (median on 116 "
                     "excavated surveys), and low in wet ground. Dielectric tracks water content more than soil "
                     "type; naming soils needs trial pits or borehole logs.")}
