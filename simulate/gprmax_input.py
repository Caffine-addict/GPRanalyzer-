"""Scene -> gprMax input files. Plain text only; gprMax itself is never imported (GPL-3.0).

Each scene produces two inputs:

- the **scene**, run once per trace as a B-scan (the antenna steps 25 mm between runs);
- its **background** — the same ground and layers with no objects — run for one trace.

Horizontally layered ground looks identical from every antenna position, so a single
background trace stands in for all 384. Subtracting it from the scene leaves only what
the buried objects scattered back, which is what `simulate/labels.py` measures boxes
from. Simulating the background at every position would double the cost to reproduce
the same trace 384 times.

Units: gprMax works in metres, seconds and hertz; the scene is in metres, nanoseconds
and megahertz. Conversions happen here and nowhere else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from simulate import instrument
from simulate.scenes import LINE_LENGTH_M, Pipe, Scene

DEFAULT_DX_M = 0.006  # lambda/14 in eps-14 soil at twice the centre frequency
_AIR_GAP_M = 0.15  # free space above the surface: room for the air wave and the top PML
_SIDE_MARGIN_M = 0.3  # keeps the moving antenna well clear of the side PML (10 cells)
_BOTTOM_MARGIN_M = 0.1
_WINDOW_MARGIN_NS = 1.0
_C_M_PER_S = 299_792_458.0

_PEC = "pec"  # gprMax built-in: perfect electric conductor
_AIR = "free_space"  # gprMax built-in

# Utility materials: textbook values at a few hundred MHz, not site measurements.
_UTILITY_MATERIALS = {
    "plastic": (3.0, 0.0),
    "water": (80.0, 0.01),
    "concrete": (6.0, 0.01),
}


@dataclass(frozen=True)
class Grid:
    dx_m: float
    width_m: float
    height_m: float
    surface_y_m: float  # gprMax's y axis points up; ground surface sits here
    time_window_ns: float
    n_traces: int

    @property
    def cells(self) -> int:
        return math.ceil(self.width_m / self.dx_m) * math.ceil(self.height_m / self.dx_m)

    @property
    def iterations(self) -> int:
        # 2-D Courant limit — what gprMax uses for a model one cell thick.
        dt_s = 1.0 / (_C_M_PER_S * math.sqrt(2.0 / self.dx_m**2))
        return math.ceil(self.time_window_ns * 1e-9 / dt_s)

    @property
    def cell_updates(self) -> int:
        """Total solver work for the B-scan — what the run time scales with."""
        return self.cells * self.iterations * self.n_traces


def ricker_peak_ns(frequency_mhz: float) -> float:
    """When gprMax's Ricker pulse peaks: its built-in delay chi = sqrt(2)/f."""
    return math.sqrt(2.0) / (frequency_mhz * 1e6) * 1e9


def along_line_to_x(scene: Scene, along_m: float) -> float:
    """Model x of an along-line position. Along-line 0 is the first trace's antenna midpoint."""
    return _SIDE_MARGIN_M + scene.antenna_offset_m / 2 + along_m


def grid_for(scene: Scene, dx_m: float = DEFAULT_DX_M) -> Grid:
    """Model size: the line plus margins, and only as deep as anything can echo back from."""
    window_ns = (
        ricker_peak_ns(scene.frequency_mhz)
        + (instrument.SAMPLES_PER_TRACE - instrument.DIRECT_WAVE_PEAK_SAMPLE) * instrument.SAMPLE_INTERVAL_NS
        + _WINDOW_MARGIN_NS
    )
    fastest = max(medium.velocity_m_per_ns for medium in (scene.soil, *(layer.medium for layer in scene.layers)))
    # Deeper than this, nothing the pulse reaches can make it back before the window
    # closes, so simulating it would cost time and change no recorded sample.
    soil_depth = fastest * window_ns / 2 + _BOTTOM_MARGIN_M
    width = 2 * _SIDE_MARGIN_M + scene.antenna_offset_m + LINE_LENGTH_M
    return Grid(
        dx_m=dx_m,
        width_m=round(width, 4),
        height_m=round(soil_depth + _AIR_GAP_M, 4),
        surface_y_m=round(soil_depth, 4),
        time_window_ns=round(window_ns, 4),
        n_traces=instrument.TRACES_PER_FRAME,
    )


def _header(title: str, grid: Grid) -> list[str]:
    d = grid.dx_m
    return [
        f"#title: {title}",
        f"#domain: {grid.width_m:.4f} {grid.height_m:.4f} {d:.4f}",
        f"#dx_dy_dz: {d:.4f} {d:.4f} {d:.4f}",
        f"#time_window: {grid.time_window_ns * 1e-9:.6e}",
    ]


def _pipe_cylinders(pipe: Pipe, dx_m: float) -> list[tuple[float, str]]:
    """The (radius, material) cylinders a pipe is built from, outermost first."""
    if pipe.kind == "metal":
        return [(pipe.radius_m, _PEC)]
    if pipe.kind == "concrete":
        return [(pipe.radius_m, "concrete")]
    wall = max(2 * dx_m, 0.12 * pipe.radius_m)
    bore = pipe.radius_m - wall
    if bore < dx_m:
        # The bore is smaller than one grid cell, so this grid cannot represent a hollow
        # pipe; a solid plastic rod is the honest approximation, not a one-cell hole.
        return [(pipe.radius_m, "plastic")]
    return [(pipe.radius_m, "plastic"), (bore, _AIR if pipe.kind == "plastic_air" else "water")]


def _utility_materials_used(scene: Scene, dx_m: float) -> list[str]:
    used = {material for pipe in scene.pipes for _, material in _pipe_cylinders(pipe, dx_m)}
    used |= {"concrete" for slab in scene.slabs if slab.kind == "concrete"}
    return [name for name in _UTILITY_MATERIALS if name in used]


def _materials(scene: Scene, dx_m: float, *, with_objects: bool) -> list[str]:
    """Material declarations — only for what this model actually contains.

    gprMax checks numerical dispersion for every *declared* material, used or not, and
    refuses to run if any is under-resolved. Declaring water (eps 80, the shortest
    wavelength by far) unconditionally made every model fail on a coarse grid, the
    object-free background included, and put a dispersion warning on scenes that
    contain no water at all.
    """
    lines = [f"#material: {scene.soil.eps_r:.4f} {scene.soil.sigma:.6f} 1 0 soil"]
    for index, layer in enumerate(sorted(scene.layers, key=lambda lyr: lyr.depth_m)):
        lines.append(f"#material: {layer.medium.eps_r:.4f} {layer.medium.sigma:.6f} 1 0 layer{index}")
    if with_objects:
        for name in _utility_materials_used(scene, dx_m):
            eps_r, sigma = _UTILITY_MATERIALS[name]
            lines.append(f"#material: {eps_r:.4f} {sigma:.6f} 1 0 {name}")
        for index, stone in enumerate(scene.stones):
            lines.append(f"#material: {stone.eps_r:.4f} 0.001000 1 0 stone{index}")
    return lines


def _ground(scene: Scene, grid: Grid) -> list[str]:
    d, w = grid.dx_m, grid.width_m
    lines = [f"#box: 0 0 0 {w:.4f} {grid.surface_y_m:.4f} {d:.4f} soil"]
    # Shallowest first: each deeper layer's box then overwrites everything beneath it.
    for index, layer in enumerate(sorted(scene.layers, key=lambda lyr: lyr.depth_m)):
        lines.append(f"#box: 0 0 0 {w:.4f} {grid.surface_y_m - layer.depth_m:.4f} {d:.4f} layer{index}")
    return lines


def _pipe(pipe: Pipe, centre_x: float, centre_y: float, dx_m: float) -> list[str]:
    return [
        f"#cylinder: {centre_x:.4f} {centre_y:.4f} 0 {centre_x:.4f} {centre_y:.4f} {dx_m:.4f} {radius:.4f} {material}"
        for radius, material in _pipe_cylinders(pipe, dx_m)
    ]


def _objects(scene: Scene, grid: Grid) -> list[str]:
    d = grid.dx_m

    def x(along: float) -> float:
        return along_line_to_x(scene, along)

    def y(depth: float) -> float:
        return grid.surface_y_m - depth

    lines: list[str] = []
    # Later commands overwrite earlier ones, so broad regions go first and solid objects last.
    for index, zone in enumerate(scene.zones):
        mix = f"zone_soil{index}"
        lines.append(f"#soil_peplinski: 0.5 0.5 2.0 2.66 {zone.water_min:.3f} {zone.water_max:.3f} {mix}")
        lines.append(
            f"#fractal_box: {x(zone.x_start_m):.4f} {y(zone.bottom_m):.4f} 0 "
            f"{x(zone.x_end_m):.4f} {y(zone.top_m):.4f} {d:.4f} 1.5 1 1 1 50 {mix} zone{index} {zone.seed}"
        )
    for void in scene.voids:
        lines.append(
            f"#box: {x(void.x_start_m):.4f} {y(void.bottom_m):.4f} 0 {x(void.x_end_m):.4f} {y(void.top_m):.4f} {d:.4f} {_AIR}"
        )
    for slab in scene.slabs:
        material = _PEC if slab.kind == "metal" else "concrete"
        lines.append(
            f"#box: {x(slab.x_start_m):.4f} {y(slab.depth_m + slab.thickness_m):.4f} 0 "
            f"{x(slab.x_end_m):.4f} {y(slab.depth_m):.4f} {d:.4f} {material}"
        )
    for pipe in scene.pipes:
        lines.extend(_pipe(pipe, x(pipe.x_m), y(pipe.depth_m + pipe.radius_m), d))
    for index, stone in enumerate(scene.stones):
        cx, cy = x(stone.x_m), y(stone.depth_m + stone.radius_m)
        lines.append(f"#cylinder: {cx:.4f} {cy:.4f} 0 {cx:.4f} {cy:.4f} {d:.4f} {stone.radius_m:.4f} stone{index}")
    return lines


def _antenna(scene: Scene, grid: Grid, *, stepped: bool) -> list[str]:
    transmitter_x = _SIDE_MARGIN_M
    receiver_x = _SIDE_MARGIN_M + scene.antenna_offset_m
    lines = [
        f"#waveform: ricker 1 {scene.frequency_mhz * 1e6:.6e} pulse",
        f"#hertzian_dipole: z {transmitter_x:.4f} {grid.surface_y_m:.4f} 0 pulse",
        f"#rx: {receiver_x:.4f} {grid.surface_y_m:.4f} 0",
    ]
    if stepped:
        step = instrument.TRACE_SPACING_M
        lines += [f"#src_steps: {step:.4f} 0 0", f"#rx_steps: {step:.4f} 0 0"]
    return lines


def scene_input(scene: Scene, grid: Grid) -> str:
    """The B-scan input: run it with `-n grid.n_traces --geometry-fixed`."""
    lines = [
        *_header(scene.scene_id, grid),
        *_materials(scene, grid.dx_m, with_objects=True),
        *_ground(scene, grid),
        *_objects(scene, grid),
        *_antenna(scene, grid, stepped=True),
    ]
    return "\n".join(lines) + "\n"


def background_input(scene: Scene, grid: Grid) -> str:
    """The same ground with no objects, for a single trace: run it with `-n 1`."""
    lines = [
        *_header(f"{scene.scene_id}_background", grid),
        *_materials(scene, grid.dx_m, with_objects=False),
        *_ground(scene, grid),
        *_antenna(scene, grid, stepped=False),
    ]
    return "\n".join(lines) + "\n"
