"""Reading a survey company's CAD utility map: geometry, labels and safe import."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from studio import site_drawings


def test_a_utility_label_reads_into_type_depth_diameter_and_quality_level() -> None:
    assert site_drawings.utility_label("Drain Pipe D : ~ 1.20 m Ø ~ 304 mm QL-A,D") == {
        "kind": "Drain Pipe", "quality_level": "QL-A", "depth_m": 1.2, "diameter_mm": 304}
    assert site_drawings.utility_label("Open Fire Hydrant Ø ~ 200 mm QL-A")["depth_m"] is None
    assert site_drawings.utility_label("EA-PLANT BLDG.") is None


def test_touching_segments_join_into_one_stroke_and_nothing_is_lost() -> None:
    segments = [[[0, 0], [1, 0]], [[1, 0], [1, 1]], [[1, 1], [2, 1]], [[5, 5], [6, 6]]]
    joined = site_drawings.join_segments(segments)
    assert [[0, 0], [1, 0], [1, 1], [2, 1]] in joined and [[5, 5], [6, 6]] in joined and len(joined) == 2


def _dxf(path: Path) -> Path:
    doc = ezdxf.new()
    doc.layers.add("TGPL_Drain Pipe", color=3)
    doc.layers.add("TGPL_GPR_SURVEY_CORRIDOR", color=1)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 5)], dxfattribs={"layer": "TGPL_Drain Pipe"})
    msp.add_circle((3, 3), 1, dxfattribs={"layer": "tree"})
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True, dxfattribs={"layer": "TGPL_GPR_SURVEY_CORRIDOR"})
    msp.add_mtext("Drain Pipe\\PD : ~ 0.50 m\\PØ ~ 254 mm\\PQL-A,D",
                  dxfattribs={"layer": "TGPL_Drain Pipe", "char_height": 0.4, "insert": (2, 1)})
    doc.saveas(path)
    return path


def test_a_drawing_parses_into_layers_lines_and_labels(tmp_path: Path) -> None:
    found = site_drawings.parse(_dxf(tmp_path / "site.dxf"))
    layers = {layer["name"]: layer for layer in found["layers"]}
    assert layers["TGPL_Drain Pipe"]["utility"] and not layers["tree"]["utility"]
    assert layers["TGPL_GPR_SURVEY_CORRIDOR"]["gpr_corridor"]
    assert found["bounds"] == [[0.0, 0.0], [20.0, 10.0]]
    (label,) = found["utilities"]
    assert label["text"].split("\n")[0] == "Drain Pipe"  # line breaks kept for display
    assert label["utility"]["depth_m"] == 0.5 and label["utility"]["quality_level"] == "QL-A"
    assert (label["ax"], label["ay"]) == (0.0, 0.0)  # MTEXT hangs from its top-left by default


def test_import_takes_only_cad_files_never_overwrites_and_finds_by_listing(tmp_path: Path) -> None:
    dataset = tmp_path / "Dataset" / "lines"
    dataset.mkdir(parents=True)
    with pytest.raises(ValueError, match="not a CAD drawing"):
        site_drawings.import_drawing(dataset, "notes.pdf", b"%PDF")
    data = _dxf(tmp_path / "x.dxf").read_bytes()
    assert site_drawings.import_drawing(dataset, "Site A.dxf", data) == "Site A"
    with pytest.raises(ValueError, match="already imported"):
        site_drawings.import_drawing(dataset, "Site A.dxf", data)
    assert [d["name"] for d in site_drawings.listing(dataset)] == ["Site A"]
    assert site_drawings.geometry(dataset, "Site A")["utilities"]
    with pytest.raises(FileNotFoundError):
        site_drawings.path_of(dataset, "../../etc/passwd")


def test_a_label_s_depth_reaches_only_the_line_its_leader_points_at() -> None:
    lines = {"Pipe": [[[0.0, 0.0], [10.0, 0.0]], [[10.0, 0.0], [20.0, 0.0]], [[0.0, 5.0], [10.0, 5.0]]]}
    label = {"layer": "Pipe", "x": 3.0, "y": 3.0, "text": "Drain Pipe\nD : ~ 0.50 m\nQL-A", "height": 0.4, "ax": 0.0,
             "ay": 0.0, "utility": {"kind": "Drain Pipe", "depth_m": 0.5, "quality_level": "QL-A", "diameter_mm": None}}
    leaders = [("Pipe", (3.2, 2.9), (4.0, 0.0))]  # from just under the label to the first pipe
    found = site_drawings.depth_lines(lines, [label], leaders)
    by_start = {tuple(p["points"][0][:2]): p for p in found["placed"]}
    assert by_start[(0.0, 0.0)]["points"][0][2] == 0.5 and not by_start[(0.0, 0.0)]["carried"]
    assert by_start[(10.0, 0.0)]["carried"]  # joined end to end: the same drawn pipe
    assert [u["points"][0] for u in found["unplaced"]] == [[0.0, 5.0]]  # no leader reaches it: no depth given
