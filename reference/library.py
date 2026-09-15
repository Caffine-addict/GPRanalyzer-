"""Read-only access to the confirmed-hyperbola reference library.

This is the only *confirmed* GPR imagery in the repo. Everything under
`Dataset/DSU_GPR_Files/` is raw and unlabelled; these crops came off signed-off
survey deliverables where a human surveyor marked each one on a utility plan.
That makes them useful for two things and not a third:

  yes - a side-by-side visual benchmark in the Studio viewer ("does what I'm
        looking at resemble a confirmed hyperbola?")
  yes - a sanity check for scripts/diagnose_candidates.py's fit quality — a
        detector that can't fit these can't fit anything
  no  - training data. 16 JPEG-compressed screen crops with no class labels
        and no source traces is not a training set, and calling it one would
        be exactly the kind of overclaim this project's contracts forbid.

Crops are lazily decoded and cached; the library is small enough to hold in
memory but there's no reason to pay for it before something asks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

_LIBRARY_ROOT = Path(__file__).resolve().parent / "hyperbolas"
_MANIFEST_PATH = _LIBRARY_ROOT / "manifest.json"


class ReferenceLibraryError(RuntimeError):
    """The library is missing or malformed — never silently degraded to empty."""


@dataclass(frozen=True)
class ReferenceCrop:
    """One confirmed hyperbola, as it appeared on the deliverable sheet."""

    id: str
    sheet: str
    callout: int
    path: Path
    confirmed_by: str
    label_class: str | None  # None = not transcribed from the CAD call-outs, not "unknown"
    label_depth_m: float | None


@lru_cache(maxsize=1)
def load_manifest() -> tuple[ReferenceCrop, ...]:
    """Every crop in the library, in sheet then call-out order."""
    if not _MANIFEST_PATH.exists():
        raise ReferenceLibraryError(
            f"reference manifest missing at {_MANIFEST_PATH} — "
            "run scripts/extract_reference_hyperbolas.py to build it"
        )
    try:
        raw = json.loads(_MANIFEST_PATH.read_text())
        entries = raw["crops"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReferenceLibraryError(f"malformed reference manifest {_MANIFEST_PATH}: {exc}") from exc

    crops = []
    for entry in entries:
        try:
            crops.append(
                ReferenceCrop(
                    id=entry["id"],
                    sheet=entry["sheet"],
                    callout=int(entry["callout"]),
                    path=_LIBRARY_ROOT / entry["file"],
                    confirmed_by=entry["confirmed_by"],
                    label_class=entry.get("label_class"),
                    label_depth_m=entry.get("label_depth_m"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReferenceLibraryError(f"malformed manifest entry {entry!r}: {exc}") from exc
    return tuple(sorted(crops, key=lambda c: (c.sheet, c.callout)))


def get_crop(crop_id: str) -> ReferenceCrop:
    """Look up one crop by id, e.g. "sheet1-03"."""
    for crop in load_manifest():
        if crop.id == crop_id:
            return crop
    raise KeyError(f"unknown reference crop id: {crop_id!r}")


@lru_cache(maxsize=64)
def load_image(crop_id: str) -> np.ndarray:
    """Decode one crop as a grayscale array."""
    crop = get_crop(crop_id)
    image = cv2.imread(str(crop.path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ReferenceLibraryError(f"crop file unreadable: {crop.path}")
    return image


def sheet_path(sheet_name: str) -> Path:
    """The full deliverable sheet a crop came from — the plan plus the crop strip."""
    known = {crop.sheet for crop in load_manifest()}
    if sheet_name not in known:
        raise KeyError(f"unknown reference sheet: {sheet_name!r}")
    path = _LIBRARY_ROOT / "sheets" / sheet_name
    if not path.exists():
        raise ReferenceLibraryError(f"sheet file missing: {path}")
    return path
