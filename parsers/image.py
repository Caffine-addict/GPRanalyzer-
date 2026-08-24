"""Loads jpg/png/bmp files into a ScanFrame — image populated, no traces, position unknown."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from core.contracts import ScanFrame
from parsers.base import register


@register("jpg", "jpeg", "png", "bmp")
def parse_image(path: Path) -> ScanFrame:
    path = Path(path)
    with Image.open(path) as im:
        array = np.array(im.convert("L"))  # grayscale, matches B-scan convention

    return ScanFrame(
        source_type="image_file",
        provenance={"path": str(path)},
        image=array,
        traces=None,
        position=None,
        position_source="unknown",
    )
