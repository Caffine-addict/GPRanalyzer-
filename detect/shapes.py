"""The three shapes the detector is trained to find — and nothing finer.

A box detector is good at shape and poor at everything else. The project's 9-class
taxonomy mixes shape with things a box cannot see: the spacing between targets
(multiple vs cluttered), a contrast grade (clear vs low-SNR), and what an object is made
of (a void vs a pipe). Training a detector on all nine would need hundreds of labelled
examples per class that do not exist, and would ask it to learn distinctions that are
better measured directly. So the detector finds shapes, and `detect/refine.py` turns each
shape into a taxonomy class from measurements.

The tuple order is the YOLO class-id order. `config.yaml`'s `detection.classes` must list
the same names in the same order — `tests/test_detect_shapes.py` holds the two together.
"""

from __future__ import annotations

# A hyperbola: something crossing the survey line — pipe, cable, duct, rock.
POINT_REFLECTOR = "point_reflector"

# A flat or continuous reflector: something running along the line, a slab, a layer edge.
LINEAR_REFLECTOR = "linear_reflector"

# An area rather than an object: backfilled trench, broken-up ground, an air void.
DISTURBED_OR_VOID = "disturbed_or_void"

SHAPE_CLASSES: tuple[str, ...] = (POINT_REFLECTOR, LINEAR_REFLECTOR, DISTURBED_OR_VOID)
