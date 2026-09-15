"""Tests for simulate/labels.py — boxes measured from the scattered field."""

from __future__ import annotations

import numpy as np
import pytest

from simulate import instrument, labels, scenes
from simulate.scenes import DisturbedZone, Medium, Pipe, Scene, Slab, Stone, Void, two_way_time_ns

SHAPE = (instrument.SAMPLES_PER_TRACE, instrument.TRACES_PER_FRAME)
SPACING = instrument.TRACE_SPACING_M


def _scene(**overrides) -> Scene:
    base = {
        "scene_id": "scene_t", "seed": 1, "soil": Medium(9.0, 0.002), "layers": (), "pipes": (), "slabs": (),
        "zones": (), "voids": (), "stones": (), "frequency_mhz": 466.0, "antenna_offset_m": 0.1,
    }
    base.update(overrides)
    return Scene(**base)


def _draw_hyperbola(field: np.ndarray, scene: Scene, pipe: Pipe, reach_m: float = 0.6, amplitude: float = 1.0) -> None:
    curve = labels._point_curve(scene, pipe, SHAPE[1])
    for trace in range(SHAPE[1]):
        row = round(curve[trace])
        if abs(trace * SPACING - pipe.x_m) <= reach_m and 0 <= row < SHAPE[0] - 3:
            field[row : row + 3, trace] += amplitude * np.array([1.0, -1.0, 0.5])


def _sample_of(depth_m: float, scene: Scene) -> int:
    return round(instrument.DIRECT_WAVE_PEAK_SAMPLE + two_way_time_ns(depth_m, scene.soil, ()) / instrument.SAMPLE_INTERVAL_NS)


def test_a_visible_pipe_gets_one_box_around_its_hyperbola() -> None:
    pipe = Pipe(5.0, 0.5, 0.05, "metal")
    scene = _scene(pipes=(pipe,))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, pipe)
    result = labels.label_scene(scene, field, sigma=0.01)
    [box] = result.boxes
    assert box.shape_class == "point_reflector"
    centre_m = (box.trace_start + box.trace_end) / 2 * SPACING
    assert centre_m == pytest.approx(5.0, abs=0.1)
    assert box.sample_start <= _sample_of(0.5, scene) <= box.sample_end
    assert result.unlabelled == ()


def test_a_pipe_below_the_noise_gets_no_box_and_says_why() -> None:
    # Labelling a target the instrument could not have shown teaches hallucination.
    pipe = Pipe(5.0, 0.5, 0.05, "metal")
    scene = _scene(pipes=(pipe,))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, pipe, amplitude=0.01)
    result = labels.label_scene(scene, field, sigma=1.0)
    assert result.boxes == ()
    [reason] = result.unlabelled
    assert "below the 4 sigma" in reason


def test_neighbouring_pipes_each_keep_their_own_box() -> None:
    # Their limbs overlap; without splitting pixels between the two curves, each box
    # would swallow half of the other hyperbola.
    left, right = Pipe(4.0, 0.5, 0.05, "metal"), Pipe(4.6, 0.5, 0.05, "metal")
    scene = _scene(pipes=(left, right))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, left)
    _draw_hyperbola(field, scene, right)
    boxes = labels.label_scene(scene, field, sigma=0.01).boxes
    centres = sorted((b.trace_start + b.trace_end) / 2 * SPACING for b in boxes)
    assert centres == pytest.approx([4.0, 4.6], abs=0.15)


def test_a_slab_gets_a_linear_box_spanning_its_length() -> None:
    slab = Slab(2.0, 5.0, 0.4, 0.1, "concrete")
    scene = _scene(slabs=(slab,))
    field = np.zeros(SHAPE)
    row = _sample_of(0.4, scene)
    field[row : row + 3, int(2.0 / SPACING) : int(5.0 / SPACING)] = [[1.0], [-1.0], [0.5]]
    [box] = labels.label_scene(scene, field, sigma=0.01).boxes
    assert box.shape_class == "linear_reflector"
    assert box.trace_start == pytest.approx(2.0 / SPACING, abs=3)
    assert box.trace_end == pytest.approx(5.0 / SPACING, abs=3)


def test_a_void_gets_a_disturbed_or_void_box() -> None:
    void = Void(6.0, 7.0, 0.3, 0.5)
    scene = _scene(voids=(void,))
    field = np.zeros(SHAPE)
    row = _sample_of(0.3, scene)
    field[row : row + 3, int(6.0 / SPACING) : int(7.0 / SPACING)] = [[1.0], [-1.0], [0.5]]
    [box] = labels.label_scene(scene, field, sigma=0.01).boxes
    assert box.shape_class == "disturbed_or_void"


def test_energy_outside_every_prior_is_not_labelled() -> None:
    # Energy with no scene object anywhere near it must not become anybody's box.
    pipe = Pipe(2.0, 0.5, 0.05, "metal")
    scene = _scene(pipes=(pipe,))
    field = np.zeros(SHAPE)
    field[200:203, 300:305] = 5.0
    result = labels.label_scene(scene, field, sigma=0.01)
    assert result.boxes == ()


def test_tiny_footprints_are_widened_to_a_trainable_size() -> None:
    box = labels._measure_box(np.pad(np.ones((1, 1)), 20), np.ones((41, 41), dtype=bool), 0.0, "point_reflector", "Pipe#0")
    assert isinstance(box, labels.LabelBox)
    assert box.trace_end - box.trace_start + 1 >= labels._MIN_BOX_TRACES
    assert box.sample_end - box.sample_start + 1 >= labels._MIN_BOX_SAMPLES


def test_yolo_line_is_class_id_then_normalised_centre_and_size() -> None:
    box = labels.LabelBox("linear_reflector", "Slab#0", trace_start=0, trace_end=383, sample_start=64, sample_end=127)
    class_id, cx, cy, w, h = box.to_yolo(384, 256).split()
    assert class_id == "1"
    assert (float(cx), float(cy), float(w), float(h)) == pytest.approx((0.5, 0.375, 1.0, 0.25))


def test_each_box_names_the_object_it_measures() -> None:
    left, right = Pipe(2.0, 0.5, 0.05, "metal"), Pipe(6.0, 0.5, 0.05, "metal")
    scene = _scene(pipes=(left, right), voids=(Void(8.0, 9.0, 0.3, 0.5),))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, left)
    _draw_hyperbola(field, scene, right)
    row = _sample_of(0.3, scene)
    field[row : row + 3, int(8.0 / SPACING) : int(9.0 / SPACING)] = [[1.0], [-1.0], [0.5]]
    assert [b.source for b in labels.label_scene(scene, field, sigma=0.01).boxes] == ["Pipe#0", "Pipe#1", "Void#0"]


def test_a_pipe_inside_a_trench_does_not_shrink_the_trenchs_box_onto_itself() -> None:
    # The pipe echoes ten times harder than the backfill scatters. Measured naively, it
    # sets the trench's footprint threshold and the trench box collapses onto the pipe.
    zone = DisturbedZone(2.0, 5.0, 0.3, 0.9, 0.05, 0.15, 7)
    pipe = Pipe(3.5, 0.5, 0.05, "metal")
    scene = _scene(zones=(zone,), pipes=(pipe,))
    field = np.zeros(SHAPE)
    top, bottom = _sample_of(0.3, scene), _sample_of(0.9, scene)
    first, last = int(2.0 / SPACING), int(5.0 / SPACING)
    field[top:bottom, first:last] = np.random.default_rng(3).normal(0.0, 0.3, size=(bottom - top, last - first))
    _draw_hyperbola(field, scene, pipe, amplitude=3.0)
    boxes = {b.source: b for b in labels.label_scene(scene, field, sigma=0.01).boxes}
    trench = boxes["DisturbedZone#0"]
    assert trench.trace_start == pytest.approx(first, abs=6)
    assert trench.trace_end == pytest.approx(last, abs=6)
    assert (boxes["Pipe#0"].trace_start + boxes["Pipe#0"].trace_end) / 2 * SPACING == pytest.approx(3.5, abs=0.1)


def test_removing_the_pipe_band_exclusion_actually_collapses_the_trench_onto_the_pipe() -> None:
    # The test above uses random backfill noise (std 0.3) that, empirically, still
    # spans the full trench width even with `& ~point_bands` removed from label_scene
    # — so it does not actually prove the exclusion is load-bearing. This test uses a
    # flat, deterministic backfill signal spanning the whole trench plus a much
    # stronger, spatially narrow pipe echo inside it: with the exclusion, the trench's
    # own footprint threshold is set by its own (weak) peak, and the box still spans
    # the full trench; without it, the threshold is set by the pipe's much higher
    # peak and every backfill pixel falls under 15% of that — collapsing the box onto
    # the pipe's own footprint, exactly as the module's docstring warns.
    zone = DisturbedZone(2.0, 5.0, 0.3, 0.9, 0.05, 0.15, 7)
    pipe = Pipe(3.5, 0.5, 0.05, "metal")
    scene = _scene(zones=(zone,), pipes=(pipe,))
    field = np.zeros(SHAPE)
    top, bottom = _sample_of(0.3, scene), _sample_of(0.9, scene)
    first, last = int(2.0 / SPACING), int(5.0 / SPACING)
    for row in range(top, bottom - 2, 3):
        field[row : row + 3, first:last] += 0.4 * np.array([1.0, -1.0, 0.5])[:, None]
    _draw_hyperbola(field, scene, pipe, amplitude=8.0)
    boxes = {b.source: b for b in labels.label_scene(scene, field, sigma=0.01).boxes}
    trench = boxes["DisturbedZone#0"]
    assert trench.trace_start == pytest.approx(first, abs=6)
    assert trench.trace_end == pytest.approx(last, abs=6)
    # Sanity: the trench box must be wider than the pipe's own narrow footprint —
    # a collapsed box (the bug) would instead exactly equal the pipe's box.
    assert (trench.trace_start, trench.trace_end) != (boxes["Pipe#0"].trace_start, boxes["Pipe#0"].trace_end)


def test_a_visible_stone_is_labelled_as_a_point_reflector() -> None:
    stone = Stone(6.0, 0.5, 0.035, 6.0)
    scene = _scene(stones=(stone,))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, stone, reach_m=0.5)
    [box] = labels.label_scene(scene, field, sigma=0.01).boxes
    assert (box.shape_class, box.source) == ("point_reflector", "Stone#0")
    assert (box.trace_start + box.trace_end) / 2 * SPACING == pytest.approx(6.0, abs=0.1)


def test_a_stone_too_faint_to_see_stays_unlabelled_clutter() -> None:
    stone = Stone(6.0, 0.5, 0.015, 5.5)
    scene = _scene(stones=(stone,))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, stone, reach_m=0.3, amplitude=0.01)
    result = labels.label_scene(scene, field, sigma=1.0)
    assert result.boxes == ()
    assert result.unlabelled[0].startswith("Stone")


def test_a_stone_inside_a_trench_does_not_shrink_the_trenchs_box_onto_itself() -> None:
    zone = DisturbedZone(2.0, 5.0, 0.3, 0.9, 0.05, 0.15, 7)
    stone = Stone(3.5, 0.5, 0.035, 6.0)
    scene = _scene(zones=(zone,), stones=(stone,))
    field = np.zeros(SHAPE)
    top, bottom = _sample_of(0.3, scene), _sample_of(0.9, scene)
    first, last = int(2.0 / SPACING), int(5.0 / SPACING)
    for row in range(top, bottom - 2, 3):
        field[row : row + 3, first:last] += 0.4 * np.array([1.0, -1.0, 0.5])[:, None]
    _draw_hyperbola(field, scene, stone, amplitude=8.0)
    trench = {b.source: b for b in labels.label_scene(scene, field, sigma=0.01).boxes}["DisturbedZone#0"]
    assert trench.trace_start == pytest.approx(first, abs=6)
    assert trench.trace_end == pytest.approx(last, abs=6)


def test_a_faint_stone_near_a_strong_pipe_keeps_a_box_of_its_own() -> None:
    # Split by nearness alone, the pipe's limb — far brighter than the stone's own apex —
    # falls to the stone where the two curves cross, sets the stone's 15%-of-peak
    # threshold, and the stone's box moves onto the pipe's limb.
    pipe = Pipe(5.0, 0.5, 0.05, "metal")
    stone = Stone(4.3, 0.35, 0.03, 6.0)
    scene = _scene(pipes=(pipe,), stones=(stone,))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, pipe, reach_m=1.0, amplitude=5.0)
    _draw_hyperbola(field, scene, stone, reach_m=0.3, amplitude=0.5)
    boxes = {b.source: b for b in labels.label_scene(scene, field, sigma=0.01).boxes}
    stone_box = boxes["Stone#0"]
    assert (stone_box.trace_start + stone_box.trace_end) / 2 * SPACING == pytest.approx(4.3, abs=0.15)
    assert stone_box.trace_end * SPACING < 4.75


def test_a_point_reflector_whose_apex_never_shows_gets_no_box() -> None:
    # Seen on real simulator output: a faint stone at 8.37 m picked up a fragment of a
    # neighbour's echo far down its own limb and was boxed at 7.45-7.73 m — nearly a
    # metre from where it lies. A box that misses the apex teaches the wrong place.
    stone = Stone(8.37, 0.56, 0.024, 6.8)
    scene = _scene(stones=(stone,))
    field = np.zeros(SHAPE)
    curve = labels._point_curve(scene, stone, SHAPE[1])
    for trace in range(int(7.45 / SPACING), int(7.73 / SPACING)):
        row = round(curve[trace])
        field[row : row + 3, trace] = [1.0, -1.0, 0.5]
    result = labels.label_scene(scene, field, sigma=0.01)
    assert result.boxes == ()
    # The diffraction aperture now rejects this one first: 7.45-7.73 m is outside the
    # aperture of a stone at 8.37 m, so no energy reaches its prior at all. The apex rule
    # still catches energy that *is* inside the aperture — the next test covers that.
    assert "below the 4 sigma" in result.unlabelled[0]


def test_a_point_box_that_starts_below_its_own_apex_is_rejected() -> None:
    # Seen on real simulator output: a stone 6 cm deep, its apex at sample 31, was boxed
    # on a void's reverberations from sample 54 down.
    stone = Stone(6.8, 0.06, 0.03, 6.75)
    scene = _scene(stones=(stone,))
    field = np.zeros(SHAPE)
    field[60:100:6, int(6.3 / SPACING) : int(6.9 / SPACING)] = 1.0
    result = labels.label_scene(scene, field, sigma=0.01)
    assert result.boxes == ()
    assert "misses its own apex" in result.unlabelled[0]


def test_a_point_reflectors_box_does_not_run_into_a_far_stronger_slab() -> None:
    # Seen on real simulator output: a plastic pipe's box ran 1.24 m right but 0.49 m
    # left — into a slab's echo that happened to lie along its limb.
    pipe = Pipe(4.6, 0.42, 0.05, "plastic_air")
    slab = Slab(5.34, 8.85, 0.95, 0.10, "concrete")
    scene = _scene(pipes=(pipe,), slabs=(slab,))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, pipe, reach_m=0.6, amplitude=0.5)
    row = _sample_of(0.95, scene)
    field[row : row + 3, int(5.34 / SPACING) : int(8.85 / SPACING)] += 3.0 * np.array([1.0, -1.0, 0.5])[:, None]
    boxes = {b.source: b for b in labels.label_scene(scene, field, sigma=0.01).boxes}
    assert boxes["Pipe#0"].trace_end * SPACING <= 5.3
    assert "Slab#0" in boxes


def test_a_point_reflectors_box_stops_at_its_diffraction_aperture() -> None:
    # A hyperbola's limbs run the whole length of the line. Boxing every pixel above 15%
    # of the apex made boxes 2.95 m wide and 218 of 256 samples tall on real simulator
    # output — a box covering most of the frame teaches the detector nothing about where
    # the target is. Only the limb inside the 45-degree diffraction aperture is
    # diagnostic, so that is as far as the prior reaches.
    pipe = Pipe(5.0, 0.6, 0.05, "metal")
    scene = _scene(pipes=(pipe,))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, pipe, reach_m=4.0)  # energy far down both limbs
    [box] = labels.label_scene(scene, field, sigma=0.01).boxes
    half_m = scenes.hyperbola_half_aperture_m(pipe.depth_m)
    width_m = (box.trace_end - box.trace_start + 1) * SPACING
    assert width_m <= 2 * half_m + 3 * SPACING
    assert box.sample_end < SHAPE[0] - 1  # and never runs to the bottom of the record


def test_a_point_reflectors_box_does_not_swallow_the_record_vertically() -> None:
    # The prior follows the hyperbola down; unbounded, it reaches the record floor and
    # any energy along that tail stretches the box with it.
    stone = Stone(5.0, 0.3, 0.035, 6.0)  # shallow: the limb stays in the record far longer
    scene = _scene(stones=(stone,))
    field = np.zeros(SHAPE)
    _draw_hyperbola(field, scene, stone, reach_m=4.0)
    [box] = labels.label_scene(scene, field, sigma=0.01).boxes
    assert box.sample_end - box.sample_start + 1 <= SHAPE[0] // 2
