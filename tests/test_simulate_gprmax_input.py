"""Tests for simulate/gprmax_input.py — scene -> gprMax input text."""

from __future__ import annotations

import re

import pytest

from simulate import gprmax_input, instrument
from simulate.gprmax_input import (
    along_line_to_x,
    background_input,
    grid_for,
    ricker_peak_ns,
    scene_input,
)
from simulate.scenes import (
    LINE_LENGTH_M,
    DisturbedZone,
    Layer,
    Medium,
    Pipe,
    Scene,
    Slab,
    Stone,
    Void,
)


def _scene(**overrides) -> Scene:
    base = {
        "scene_id": "scene_t", "seed": 1, "soil": Medium(9.0, 0.002), "layers": (), "pipes": (), "slabs": (),
        "zones": (), "voids": (), "stones": (), "frequency_mhz": 466.0, "antenna_offset_m": 0.1,
    }
    base.update(overrides)
    return Scene(**base)


def _numbers(line: str) -> list[float]:
    return [float(v) for v in re.findall(r"-?\d+\.\d+(?:e-?\d+)?|-?\d+", line.split(":", 1)[1])]


def _lines(text: str, command: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(f"#{command}:")]


def test_ricker_peak_is_gprmax_s_built_in_delay() -> None:
    assert ricker_peak_ns(466.0) == pytest.approx(2 ** 0.5 / 466e6 * 1e9)


def test_the_window_covers_the_pulse_delay_plus_the_whole_instrument_record() -> None:
    scene = _scene()
    grid = grid_for(scene)
    record_ns = (instrument.SAMPLES_PER_TRACE - instrument.DIRECT_WAVE_PEAK_SAMPLE) * instrument.SAMPLE_INTERVAL_NS
    assert grid.time_window_ns >= ricker_peak_ns(scene.frequency_mhz) + record_ns


def test_the_model_spans_the_whole_line_with_margins() -> None:
    grid = grid_for(_scene())
    assert grid.width_m == pytest.approx(2 * gprmax_input._SIDE_MARGIN_M + 0.1 + LINE_LENGTH_M, abs=1e-3)
    assert grid.n_traces == instrument.TRACES_PER_FRAME


def test_faster_ground_needs_a_deeper_model() -> None:
    assert grid_for(_scene(soil=Medium(5.0, 0.002))).height_m > grid_for(_scene(soil=Medium(14.0, 0.002))).height_m


def test_soil_depth_is_exactly_a_round_trip_at_the_fastest_medium() -> None:
    # The ordering check above only pins direction, not magnitude — it can't tell a
    # missing "/2" (one-way instead of two-way depth) apart from the correct formula,
    # since a deeper model for faster ground holds either way. height_m has to be
    # exactly deep enough that a two-way echo off its floor lands at time_window_ns,
    # independently re-derived here from the physical round-trip relationship
    # (depth = velocity * time / 2 for a two-way path), not copied from grid_for.
    scene = _scene()  # single soil medium, no layers, so "fastest" is soil.velocity_m_per_ns
    grid = grid_for(scene)
    soil_depth_m = grid.height_m - gprmax_input._AIR_GAP_M - gprmax_input._BOTTOM_MARGIN_M
    round_trip_ns = 2 * soil_depth_m / scene.soil.velocity_m_per_ns
    assert round_trip_ns == pytest.approx(grid.time_window_ns, rel=1e-4)


def test_cost_is_cells_times_iterations_times_traces() -> None:
    grid = grid_for(_scene())
    assert grid.cell_updates == grid.cells * grid.iterations * instrument.TRACES_PER_FRAME
    assert grid.iterations > 0


def test_scene_input_declares_a_2d_model_one_cell_thick() -> None:
    text = scene_input(_scene(), grid_for(_scene()))
    domain = _numbers(_lines(text, "domain")[0])
    assert domain[2] == pytest.approx(gprmax_input.DEFAULT_DX_M)


def test_scene_input_steps_the_antenna_one_trace_spacing_per_run() -> None:
    text = scene_input(_scene(), grid_for(_scene()))
    assert _numbers(_lines(text, "src_steps")[0])[0] == pytest.approx(instrument.TRACE_SPACING_M)
    assert _numbers(_lines(text, "rx_steps")[0])[0] == pytest.approx(instrument.TRACE_SPACING_M)


def test_the_first_trace_midpoint_is_along_line_zero() -> None:
    scene = _scene(antenna_offset_m=0.12)
    text = scene_input(scene, grid_for(scene))
    tx = _numbers(_lines(text, "hertzian_dipole")[0])[0]
    rx = _numbers(_lines(text, "rx")[0])[0]
    assert (tx + rx) / 2 == pytest.approx(along_line_to_x(scene, 0.0))
    assert rx - tx == pytest.approx(0.12)


def test_the_pulse_uses_the_scene_frequency() -> None:
    text = scene_input(_scene(frequency_mhz=500.0), grid_for(_scene(frequency_mhz=500.0)))
    assert "ricker 1 5.000000e+08" in _lines(text, "waveform")[0]


def test_background_has_the_ground_but_no_objects_and_no_stepping() -> None:
    scene = _scene(pipes=(Pipe(3.0, 0.5, 0.05, "metal"),), slabs=(Slab(1.0, 3.0, 0.4, 0.1, "concrete"),))
    text = background_input(scene, grid_for(scene))
    assert "#cylinder:" not in text and "src_steps" not in text
    assert _lines(text, "box")[0].endswith("soil")
    assert len(_lines(text, "box")) == 1


def test_pipe_centre_sits_one_radius_below_its_reported_depth() -> None:
    pipe = Pipe(4.0, 0.6, 0.05, "metal")
    scene = _scene(pipes=(pipe,))
    grid = grid_for(scene)
    [cylinder] = _lines(scene_input(scene, grid), "cylinder")
    x, y = _numbers(cylinder)[:2]
    assert x == pytest.approx(along_line_to_x(scene, 4.0), abs=1e-4)
    assert y == pytest.approx(grid.surface_y_m - 0.6 - 0.05, abs=1e-4)
    assert cylinder.endswith(" pec")


@pytest.mark.parametrize(
    ("kind", "radius", "materials"),
    [
        ("metal", 0.05, ["pec"]),
        ("concrete", 0.05, ["concrete"]),
        ("plastic_air", 0.08, ["plastic", "free_space"]),
        ("plastic_water", 0.08, ["plastic", "water"]),
        ("plastic_air", 0.016, ["plastic"]),  # bore under one cell: solid rod, not a one-cell hole
    ],
)
def test_pipe_kinds_become_the_right_material_stack(kind: str, radius: float, materials: list[str]) -> None:
    scene = _scene(pipes=(Pipe(4.0, 0.5, radius, kind),))
    cylinders = _lines(scene_input(scene, grid_for(scene)), "cylinder")
    assert [line.split()[-1] for line in cylinders] == materials


def test_layers_overwrite_downwards_shallowest_first() -> None:
    layers = (Layer(0.8, Medium(12.0, 0.003)), Layer(0.4, Medium(6.0, 0.001)))
    scene = _scene(layers=layers)
    grid = grid_for(scene)
    boxes = _lines(scene_input(scene, grid), "box")
    tops = [_numbers(line)[4] for line in boxes[1:3]]
    assert tops == pytest.approx([grid.surface_y_m - 0.4, grid.surface_y_m - 0.8], abs=1e-4)


def test_zones_voids_slabs_and_stones_are_all_written() -> None:
    scene = _scene(
        zones=(DisturbedZone(1.0, 3.0, 0.1, 0.8, 0.05, 0.15, 42),),
        voids=(Void(5.0, 6.0, 0.3, 0.5),),
        slabs=(Slab(6.5, 9.0, 0.4, 0.1, "metal"),),
        stones=(Stone(2.0, 0.3, 0.02, 6.0),),
    )
    text = scene_input(scene, grid_for(scene))
    assert _lines(text, "soil_peplinski") and _lines(text, "fractal_box")[0].endswith(" 42")
    assert any(line.endswith("free_space") for line in _lines(text, "box"))
    assert any(line.endswith(" pec") for line in _lines(text, "box"))
    assert _lines(text, "cylinder")[-1].endswith("stone0")


def _declared(text: str) -> set[str]:
    return {line.split()[-1] for line in _lines(text, "material")}


def test_the_background_declares_only_the_ground() -> None:
    # gprMax checks dispersion for every declared material: an unused water declaration
    # alone made object-free models fail on a coarse grid.
    scene = _scene(pipes=(Pipe(3.0, 0.5, 0.08, "plastic_water"),), stones=(Stone(2.0, 0.3, 0.02, 6.0),))
    assert _declared(background_input(scene, grid_for(scene))) == {"soil"}


def test_a_scene_declares_only_the_materials_its_objects_use() -> None:
    scene = _scene(pipes=(Pipe(3.0, 0.5, 0.05, "metal"),))
    assert _declared(scene_input(scene, grid_for(scene))) == {"soil"}  # pec is built in


def test_water_is_declared_only_for_a_water_pipe_the_grid_can_hollow_out() -> None:
    wide = _scene(pipes=(Pipe(3.0, 0.5, 0.08, "plastic_water"),))
    narrow = _scene(pipes=(Pipe(3.0, 0.5, 0.016, "plastic_water"),))
    assert _declared(scene_input(wide, grid_for(wide))) == {"soil", "plastic", "water"}
    assert _declared(scene_input(narrow, grid_for(narrow))) == {"soil", "plastic"}


def test_a_concrete_slab_declares_concrete() -> None:
    scene = _scene(slabs=(Slab(1.0, 3.0, 0.4, 0.1, "concrete"),))
    assert "concrete" in _declared(scene_input(scene, grid_for(scene)))
