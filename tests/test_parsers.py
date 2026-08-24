"""Tests for the parser registry and the image parser."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import parsers.image  # noqa: F401 - registers the "jpg"/"png"/"bmp" parsers as a side effect
from parsers.base import get_parser, register, registered_extensions


def test_image_parser_registered_for_expected_extensions() -> None:
    exts = registered_extensions()
    for ext in ("jpg", "jpeg", "png", "bmp"):
        assert ext in exts


def test_get_parser_unknown_extension_raises() -> None:
    with pytest.raises(KeyError, match="tiff"):
        get_parser("scan.tiff")


def test_register_accepts_extension_with_or_without_dot() -> None:
    from parsers import base

    calls: list[Path] = []

    @register(".xyz", "abc")
    def _fake_parser(path: Path):
        calls.append(path)
        return "sentinel"

    try:
        assert get_parser("a.xyz") is _fake_parser
        assert get_parser("a.abc") is _fake_parser
    finally:
        base._REGISTRY.pop("xyz", None)
        base._REGISTRY.pop("abc", None)


def test_parse_image_loads_grayscale_scan_frame(tmp_path: Path) -> None:
    img_path = tmp_path / "scan.jpg"
    Image.fromarray(np.full((32, 48), 128, dtype=np.uint8)).save(img_path)

    parser = get_parser(img_path)
    frame = parser(img_path)

    assert frame.image is not None
    assert frame.image.shape == (32, 48)
    assert frame.traces is None
    assert frame.position is None
    assert frame.position_source == "unknown"
    assert frame.source_type == "image_file"
    assert frame.provenance["path"] == str(img_path)
