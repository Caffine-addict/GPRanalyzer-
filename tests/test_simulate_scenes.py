"""Tests for simulate/scenes.py — random scenes, their geometry, and their labels' shape classes."""

from __future__ import annotations

import itertools

import pytest

from simulate import instrument, scenes
from simulate.scenes import (
    DisturbedZone,
    Layer,
    Medium,
    Pipe,
    Scene,
    Slab,
    Stone,
    Void,
    max_visible_depth_m,
    random_scene,
    shape_class_of,
    two_way_time_ns,
)

MANY = [random_scene(index, 11) for index in range(300)]


def test_the_same_index_and_seed_always_give_the_same_scene() -> None:
    assert random_scene(5, 3) == random_scene(5, 3)
    assert random_scene(5, 3) != random_scene(6, 3)
    assert random_scene(5, 3) != random_scene(5, 4)


def test_scenes_survive_a_round_trip_through_json_ready_dicts() -> None:
    for scene in MANY[:50]:
        assert Scene.from_dict(scene.to_dict()) == scene


def test_every_object_sits_on_the_line_and_within_what_the_record_can_show() -> None:
    for scene in MANY:
        deepest = max_visible_depth_m(scene.soil, scene.layers)
        for pipe in scene.pipes:
            assert 0 < pipe.x_m < scenes.LINE_LENGTH_M
            assert scenes._MIN_DEPTH_M <= pipe.depth_m <= deepest
        for obj in (*scene.slabs, *scene.zones, *scene.voids):
            assert 0 <= obj.x_start_m < obj.x_end_m <= scenes.LINE_LENGTH_M


def test_solid_objects_never_overlap_each_other() -> None:
    # Later gprMax commands overwrite earlier ones; an overlap would silently delete part
    # of an object that its label still claims is there.
    for scene in MANY:
        solids = [scenes._footprint(obj) for obj in (*scene.pipes, *scene.slabs, *scene.voids, *scene.stones)]
        for a, b in itertools.combinations(solids, 2):
            assert not scenes._overlaps(a, b, clearance=0.0)


def test_point_reflectors_are_spaced_so_their_hyperbolas_stay_apart() -> None:
    # Replaces a flat 0.5 m rule that applied to pipes only: stones ignored it and each
    # other, leaving a median minimum spacing of 0.26 m and overlapping boxes. How wide a
    # hyperbola is scales with depth, so the spacing has to as well.
    for scene in MANY:
        points: list[Pipe | Stone] = [*scene.pipes, *scene.stones]
        for a, b in itertools.combinations(points, 2):
            needed = scenes.hyperbola_half_aperture_m(a.depth_m) + scenes.hyperbola_half_aperture_m(b.depth_m)
            assert abs(a.x_m - b.x_m) >= needed


def test_a_scene_holds_at_most_one_area_feature() -> None:
    # A void inside a trench under a slab gives three overlapping area boxes that no
    # detector could be expected to tell apart.
    for scene in MANY:
        assert len(scene.slabs) + len(scene.zones) + len(scene.voids) <= 1


def test_scenes_stay_sparse_enough_for_every_target_to_be_labelled_on_its_own() -> None:
    counts = sorted(len(scene.pipes) + len(scene.stones) for scene in MANY)
    assert counts[-1] <= 6
    assert counts[len(counts) // 2] <= 4


def test_some_scenes_are_empty_so_the_detector_learns_nothing_is_there() -> None:
    empty = sum(1 for scene in MANY if not scene.labelled_objects)
    assert 0.03 * len(MANY) <= empty <= 0.15 * len(MANY)


def test_frequency_and_offset_vary_around_the_measured_and_assumed_values() -> None:
    for scene in MANY:
        assert scene.frequency_mhz == pytest.approx(instrument.CENTRE_FREQUENCY_MHZ, rel=0.101)
        assert 0.079 <= scene.antenna_offset_m <= 0.141


def test_every_shape_class_appears_across_a_batch() -> None:
    classes = {shape_class_of(obj) for scene in MANY for obj in scene.labelled_objects}
    assert classes == {"point_reflector", "linear_reflector", "disturbed_or_void"}


def test_shape_classes_follow_what_each_object_looks_like_on_a_b_scan() -> None:
    assert shape_class_of(Pipe(1.0, 0.5, 0.05, "metal")) == "point_reflector"
    assert shape_class_of(Slab(1.0, 3.0, 0.5, 0.1, "concrete")) == "linear_reflector"
    assert shape_class_of(Void(1.0, 2.0, 0.3, 0.5)) == "disturbed_or_void"
    assert shape_class_of(DisturbedZone(1.0, 3.0, 0.1, 0.8, 0.05, 0.15, 7)) == "disturbed_or_void"


def test_a_stone_is_a_point_reflector_like_a_small_pipe() -> None:
    # A single 2-D line cannot tell a 7 cm stone from a 7 cm cable.
    assert shape_class_of(Stone(1.0, 0.3, 0.035, 6.0)) == "point_reflector"


def test_something_that_is_not_a_scene_object_has_no_shape_class() -> None:
    with pytest.raises(TypeError, match="not a labelled object"):
        shape_class_of(Medium(9.0, 0.001))  # type: ignore[arg-type]


def test_empty_scenes_contain_nothing_at_all() -> None:
    # Visible stones are labelled, so a frame meant to teach "nothing here" must have none.
    for scene in MANY:
        if not (scene.pipes or scene.slabs or scene.zones or scene.voids):
            assert scene.stones == ()


def test_labelled_objects_list_point_reflectors_before_areas() -> None:
    # simulate/labels.py builds its priors in this order; a mismatch would pair each
    # object with another object's prior.
    order = ["Pipe", "Stone", "Slab", "DisturbedZone", "Void"]
    for scene in MANY:
        names = [type(obj).__name__ for obj in scene.labelled_objects]
        assert names == sorted(names, key=order.index)
    assert any(scene.stones for scene in MANY)


@pytest.mark.parametrize(
    "build",
    [
        lambda: Medium(0.5, 0.001),
        lambda: Medium(9.0, -0.1),
        lambda: Pipe(1.0, 0.5, 0.0, "metal"),
        lambda: Pipe(1.0, -0.1, 0.05, "metal"),
        lambda: Pipe(1.0, 0.5, 0.05, "unobtainium"),
        lambda: Slab(3.0, 1.0, 0.5, 0.1, "concrete"),
        lambda: Slab(1.0, 3.0, 0.5, 0.1, "wood"),
        lambda: Void(1.0, 2.0, 0.5, 0.3),
        lambda: DisturbedZone(1.0, 3.0, 0.1, 0.8, 0.2, 0.1, 7),
    ],
)
def test_impossible_objects_are_rejected(build) -> None:
    with pytest.raises(ValueError):
        build()


def test_two_way_time_in_uniform_ground_is_twice_depth_over_velocity() -> None:
    soil = Medium(9.0, 0.001)
    assert two_way_time_ns(0.5, soil, ()) == pytest.approx(2 * 0.5 / soil.velocity_m_per_ns)
    assert two_way_time_ns(0.0, soil, ()) == 0.0


def test_two_way_time_adds_up_through_layers() -> None:
    soil, lower = Medium(4.0, 0.001), Medium(16.0, 0.001)
    layers = (Layer(depth_m=0.3, medium=lower),)
    expected = 2 * 0.3 / soil.velocity_m_per_ns + 2 * 0.2 / lower.velocity_m_per_ns
    assert two_way_time_ns(0.5, soil, layers) == pytest.approx(expected)


def test_max_visible_depth_is_the_last_depth_inside_the_usable_record() -> None:
    soil = Medium(9.0, 0.001)
    deepest = max_visible_depth_m(soil, ())
    assert two_way_time_ns(deepest, soil, ()) <= scenes._USABLE_TWO_WAY_NS
    assert two_way_time_ns(deepest + 0.02, soil, ()) > scenes._USABLE_TWO_WAY_NS


def test_slower_ground_shows_shallower() -> None:
    assert max_visible_depth_m(Medium(14.0, 0.001), ()) < max_visible_depth_m(Medium(5.0, 0.001), ())


def test_trenches_keep_clear_of_voids_and_slabs() -> None:
    for scene in MANY:
        for zone in scene.zones:
            for other in (*scene.voids, *scene.slabs):
                assert not scenes._overlaps(scenes._footprint(zone), scenes._footprint(other), clearance=0.0)


def test_pipes_can_lie_inside_a_trench() -> None:
    # A pipe laid in backfill is the commonest real layout; the generator must produce it.
    inside = [
        (scene, pipe)
        for scene in MANY
        for zone in scene.zones
        for pipe in scene.pipes
        if zone.x_start_m < pipe.x_m < zone.x_end_m and zone.top_m < pipe.depth_m < zone.bottom_m
    ]
    assert inside
