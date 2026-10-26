"""Measured dielectric per line, and how neighbouring lines group into sections."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from studio import ground
from studio.velocity import SEMBLANCE_PERMITTIVITIES

GRID = np.asarray(SEMBLANCE_PERMITTIVITIES)


def _arch(eps: float) -> np.ndarray:
    """A clear semblance curve peaking at `eps`."""
    return np.exp(-(((GRID - eps) / 0.8) ** 2))


def _scene(n: int) -> dict[str, Any]:
    """`n` parallel 10 m lines running north, 1 m apart."""
    return {"lines": [{"job": f"S_L{i}", "path": [[0.0, float(i), 0.0], [10.0, float(i), 10.0]]} for i in range(n)]}


def _sections(per_line: list[list[float]], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    curves = {f"S_L{i}": [_arch(e) for e in arches] for i, arches in enumerate(per_line)}
    monkeypatch.setattr(ground, "_curves", lambda path: curves[path])
    return ground.sections(_scene(len(per_line)), {job: job for job in curves})


def test_lines_that_agree_are_one_section(monkeypatch: pytest.MonkeyPatch) -> None:
    found = _sections([[6, 6, 6]] * 4, monkeypatch)
    assert len(found["sections"]) == 1 and len(found["sections"][0]["lines"]) == 4
    assert found["sections"][0]["dielectric"] == pytest.approx(6, abs=0.4)
    assert not found["sections"][0]["wet"]


def test_two_lines_agreeing_on_a_different_value_start_a_new_section(monkeypatch: pytest.MonkeyPatch) -> None:
    found = _sections([[6, 6, 6], [6, 6, 6], [12, 12, 12], [12, 12, 12]], monkeypatch)
    assert [len(z["lines"]) for z in found["sections"]] == [2, 2]
    assert sorted(z["wet"] for z in found["sections"]) == [False, True]  # 12 is above site_map.WET_SOIL_EPS


def test_one_line_disagreeing_alone_stays_in_its_section_as_an_outlier(monkeypatch: pytest.MonkeyPatch) -> None:
    found = _sections([[6, 6, 6], [10, 10, 10], [6, 6, 6], [6, 6, 6]], monkeypatch)
    assert len(found["sections"]) == 1
    assert found["sections"][0]["outliers"] == ["S_L1"]


def test_a_line_with_too_few_clear_arches_is_not_measured_and_never_filled_in(monkeypatch: pytest.MonkeyPatch) -> None:
    found = _sections([[6, 6, 6], [6, 6], [6, 6, 6]], monkeypatch)
    by_job = {line["job"]: line for line in found["lines"]}
    assert by_job["S_L1"]["dielectric"] is None and by_job["S_L1"]["clear_arches"] == 2
    assert "S_L1" not in found["sections"][0]["lines"]
