"""Tests holding detect/shapes.py, detect/refine.py and config.yaml in agreement."""

from __future__ import annotations

from pathlib import Path

from core.config import load_config
from detect.refine import REFINED_CLASSES
from detect.shapes import SHAPE_CLASSES

REAL_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def test_config_accepts_exactly_the_shapes_in_class_id_order() -> None:
    # The order is the YOLO class id: a reordered list would relabel every detection.
    assert load_config(REAL_CONFIG).detection.classes == SHAPE_CLASSES


def test_the_configured_taxonomy_covers_every_class_refinement_can_produce() -> None:
    # A class refinement produces but the taxonomy lacks would be dropped at runtime.
    assert REFINED_CLASSES <= set(load_config(REAL_CONFIG).detection.taxonomy)


def test_shape_names_never_collide_with_taxonomy_names() -> None:
    # Refinement passes taxonomy names straight through; a shared name would let an
    # unrefined shape masquerade as a final class.
    assert not set(SHAPE_CLASSES) & set(load_config(REAL_CONFIG).detection.taxonomy)
