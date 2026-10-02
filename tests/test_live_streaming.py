"""Live streaming, end to end: path intake -> chunked replay -> classical detector -> settle/dedup.

Each test pins one rule that the live path depends on. The rules' *tuning* (margins, window
counts) was measured on the real lines by scripts/compare_live_batch.py; these tests pin the
behaviour, so a change to the rule fails here and a change to the numbers is re-measured there.
"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from api.schemas import frame_to_dict, location_to_dict
from core.config import ConfigError, ReplaySourceConfig, load_config
from core.contracts import Detection, ScanFrame, SourceCapabilities, TraceBox
from evidence.extract import extract_evidence
from parsers.base import register
from parsers.image import parse_image
from pipeline import orchestrator as orch
from pipeline.classical_detector import MIN_CONTEXT_TRACES, ClassicalDetector
from render.bscan import traces_to_image
from sources.intake import IntakeError, collect_scan_files
from sources.replay import ReplaySource
from tests.test_studio_velocity import _synthetic_hyperbola_traces

REPO = Path(__file__).resolve().parent.parent
_SPACING = 0.025
_CAPS = SourceCapabilities(has_calibrated_depth=False, has_real_position=False, has_true_amplitude=False, latency_class="batch")


def _frame(traces: np.ndarray, **kw: object) -> ScanFrame:
    base: dict[str, object] = {
        "source_type": "test", "provenance": {"path": "line.fake"}, "traces": traces,
        "sample_interval_ns": 0.1, "trace_spacing_m": _SPACING,
    }
    return ScanFrame(**{**base, **kw})  # type: ignore[arg-type]


# --- the detector ---------------------------------------------------------------------------


def test_the_classical_detector_boxes_a_synthetic_hyperbola_with_its_fit_quality() -> None:
    traces = _synthetic_hyperbola_traces(velocity=0.1, apex_trace=100, apex_time_ns=8.0)
    image = traces_to_image(traces)
    detections = ClassicalDetector().detect(image, _frame(traces))
    assert len(detections) == 1
    x1, _, x2, _ = detections[0].bbox_xyxy
    apex_px = 100 * image.shape[1] / traces.shape[0]
    assert x1 < apex_px < x2  # rescaled into image pixels, around the apex
    assert detections[0].confidence > 0.85  # the fit's R^2


def test_pure_noise_yields_no_credible_detection() -> None:
    noise = np.random.default_rng(3).normal(size=(200, 256))
    assert ClassicalDetector().detect(traces_to_image(noise), _frame(noise)) == []


def test_a_partial_line_is_not_scanned_before_it_has_enough_context() -> None:
    traces = _synthetic_hyperbola_traces(velocity=0.1, apex_trace=20, apex_time_ns=8.0, n_traces=MIN_CONTEXT_TRACES - 1)
    partial = _frame(traces, trace_offset=0, line_complete=False)
    assert ClassicalDetector().detect(traces_to_image(traces), partial) == []
    # The same short line, complete, is scanned: completeness, not length, is what gates it.
    complete = _frame(traces, trace_offset=0, line_complete=True)
    image = traces_to_image(traces)
    assert ClassicalDetector().detect(image, complete) == ClassicalDetector().detect(image, _frame(traces))


def test_an_image_frame_is_searched_as_pseudo_traces() -> None:
    traces = _synthetic_hyperbola_traces(velocity=0.1, apex_trace=100, apex_time_ns=8.0)
    image = traces_to_image(traces)
    frame = ScanFrame(source_type="image_file", provenance={"path": "x.png"}, image=image)
    assert len(ClassicalDetector().detect(image, frame)) >= 1


# --- replay chunking ------------------------------------------------------------------------


@register("fakeline")
def _parse_fake_line(path: Path) -> ScanFrame:
    n = int(path.read_text())
    return _frame(np.arange(n * 8, dtype=np.float32).reshape(n, 8), provenance={"path": str(path)})


def _replay(path: Path, chunk: int, window: int = 0) -> list[ScanFrame]:
    cfg = ReplaySourceConfig(path=str(path), playback_rate_hz=1000.0, step_mode=True, chunk_traces=chunk, window_traces=window)
    return list(ReplaySource(cfg).frames())


def test_a_traced_line_streams_as_growing_windows_that_end_complete(tmp_path: Path) -> None:
    (tmp_path / "a.fakeline").write_text("40")
    frames = _replay(tmp_path, chunk=16)
    assert [f.traces.shape[0] for f in frames if f.traces is not None] == [16, 32, 40]
    assert [f.trace_offset for f in frames] == [0, 0, 0]
    assert [f.line_complete for f in frames] == [False, False, True]
    assert all(f.position == 0.0 and f.position_source == "wheel_encoder" for f in frames)


def test_a_window_cap_slides_the_window_and_moves_its_position(tmp_path: Path) -> None:
    (tmp_path / "a.fakeline").write_text("40")
    frames = _replay(tmp_path, chunk=16, window=20)
    assert [(f.trace_offset, f.traces.shape[0]) for f in frames if f.traces is not None] == [(0, 16), (12, 20), (20, 20)]
    assert frames[1].position == pytest.approx(12 * _SPACING)
    # The window is the line's own traces, not a copy with shifted content.
    assert frames[1].traces is not None and frames[1].traces[0, 0] == 12 * 8


def test_chunking_off_sends_the_whole_line_once_unstreamed(tmp_path: Path) -> None:
    (tmp_path / "a.fakeline").write_text("40")
    [frame] = _replay(tmp_path, chunk=0)
    assert frame.trace_offset is None and frame.line_complete


# --- settle, persistence, dedup (the orchestrator's streaming rule) -------------------------


def _bare_orchestrator() -> orch.Orchestrator:
    o = orch.Orchestrator.__new__(orch.Orchestrator)
    o._line_key, o._reported, o._candidates = None, [], []
    return o


def _window(end: int, *, complete: bool = False, path: str = "line") -> ScanFrame:
    # Image shape == (n_samples, n_traces), so bbox pixels are trace/sample indices directly.
    return _frame(np.zeros((end, 100), dtype=np.float32), trace_offset=0, line_complete=complete, provenance={"path": path})


def _det(t0: int, t1: int) -> Detection:
    return Detection(class_name="point_reflector", confidence=0.9, bbox_xyxy=(float(t0), 10.0, float(t1), 40.0))


def _settle(o: orch.Orchestrator, frame: ScanFrame, dets: list[Detection]) -> list[TraceBox | None]:
    assert frame.traces is not None
    image_shape = (frame.traces.shape[1], frame.traces.shape[0])
    return [box for _, box in o._settled_and_new(frame, image_shape, dets)]


def test_a_streamed_target_is_reported_once_after_it_persists_and_settles() -> None:
    o = _bare_orchestrator()
    target = _det(10, 30)
    margin_end = 30 + orch.SETTLE_MARGIN_TRACES
    reported = []
    for i in range(orch.CONFIRM_WINDOWS + 3):
        reported += _settle(o, _window(margin_end + 16 * i), [target])
    assert len(reported) == 1
    assert reported[0] == TraceBox(10, 30, 10, 40)


def test_a_box_near_the_leading_edge_is_held_until_settled() -> None:
    o = _bare_orchestrator()
    for _ in range(orch.CONFIRM_WINDOWS + 1):
        assert _settle(o, _window(30 + orch.SETTLE_MARGIN_TRACES - 1), [_det(10, 30)]) == []


def test_a_box_seen_in_one_window_only_is_never_reported() -> None:
    o = _bare_orchestrator()
    assert _settle(o, _window(200), [_det(10, 30)]) == []
    for i in range(1, orch.CONFIRM_WINDOWS + 1):
        assert _settle(o, _window(200 + 16 * i), []) == []


def test_the_final_window_reports_what_it_finds_without_a_streak() -> None:
    o = _bare_orchestrator()
    assert len(_settle(o, _window(200, complete=True), [_det(150, 190)])) == 1


def test_a_new_line_starts_with_nothing_reported() -> None:
    o = _bare_orchestrator()
    assert len(_settle(o, _window(200, complete=True, path="a"), [_det(10, 30)])) == 1
    assert len(_settle(o, _window(200, complete=True, path="b"), [_det(10, 30)])) == 1


def test_same_target_tolerates_small_edge_shifts_but_not_a_different_depth() -> None:
    assert orch._same_target(TraceBox(10, 30, 10, 40), TraceBox(13, 34, 12, 42))
    assert not orch._same_target(TraceBox(10, 30, 10, 40), TraceBox(10, 30, 50, 80))
    assert not orch._same_target(TraceBox(10, 30, 10, 40), TraceBox(26, 60, 10, 40))


def test_an_image_finding_is_located_in_pixels() -> None:
    o = _bare_orchestrator()
    frame = ScanFrame(source_type="image_file", provenance={"path": "x.png"}, image=np.zeros((50, 80), np.uint8))
    [(_, box)] = o._settled_and_new(frame, (50, 80), [_det(10, 30)])
    assert box == TraceBox(10, 30, 10, 40)


# --- evidence position along the line -------------------------------------------------------


def test_each_finding_reports_its_own_position_not_the_frame_start() -> None:
    from core.config import EvidenceConfig

    frame = _frame(np.zeros((100, 64), np.float32), position=2.0, position_source="wheel_encoder")
    det = Detection(class_name="point_reflector", confidence=0.9, bbox_xyxy=(40.0, 0.0, 60.0, 10.0))
    ev = extract_evidence(det, frame, _CAPS, EvidenceConfig(assumed_max_depth_m=5.0), image_shape=(64, 100))
    assert ev.position_m == pytest.approx(2.0 + 50 * _SPACING)
    assert ev.position_confidence == "estimated"


# --- path intake ----------------------------------------------------------------------------


def _write_png(path: Path) -> None:
    Image.fromarray(np.full((32, 32), 90, np.uint8)).save(path)


def test_a_folder_yields_its_scans_and_names_everything_it_skipped(tmp_path: Path) -> None:
    _write_png(tmp_path / "b.png")
    (tmp_path / "report.pdf").write_bytes(b"%PDF")
    (tmp_path / "__MACOSX").mkdir()
    _write_png(tmp_path / "__MACOSX" / "._b.png")
    intake = collect_scan_files(tmp_path, extract_root=tmp_path / "x")
    assert intake.files == (tmp_path / "b.png",)
    assert [(p.name, "pdf" in r) for p, r in intake.skipped] == [("report.pdf", True)]


@pytest.mark.skipif(shutil.which("bsdtar") is None and shutil.which("tar") is None, reason="no archive tool")
def test_an_archive_is_extracted_once_and_its_scans_found(tmp_path: Path) -> None:
    _write_png(tmp_path / "scan.png")
    archive = tmp_path / "survey.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(tmp_path / "scan.png", "Radargrams/scan.png")
        zf.writestr("notes.docx", b"x")
    root = tmp_path / "extract"
    first = collect_scan_files(archive, extract_root=root)
    assert [p.name for p in first.files] == ["scan.png"]
    assert [p.name for p, _ in first.skipped] == ["notes.docx"]
    assert collect_scan_files(archive, extract_root=root).files == first.files  # cache hit, same folder
    assert len(list(root.iterdir())) == 1


def test_a_corrupt_archive_is_a_clear_error_and_leaves_nothing_behind(tmp_path: Path) -> None:
    bad = tmp_path / "broken.rar"
    bad.write_bytes(b"not a rar")
    root = tmp_path / "extract"
    with pytest.raises(IntakeError, match="could not extract broken.rar"):
        collect_scan_files(bad, extract_root=root)
    assert not root.exists() or not any(root.iterdir())


def test_a_symlink_in_an_archive_is_never_followed_to_a_file_outside_it(tmp_path: Path) -> None:
    """An archive entry `data.png -> <secret>` extracts as that symlink (bsdtar allows it);
    collecting it would parse the secret. It must come back skipped, not as a scan."""
    import tarfile

    secret = tmp_path / "secret.png"
    _write_png(secret)
    link = tmp_path / "staging" / "data.png"
    link.parent.mkdir()
    link.symlink_to(secret)
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as tf:
        tf.add(link, arcname="data.png")
    intake = collect_scan_files(archive, extract_root=tmp_path / "x")
    assert intake.files == ()
    assert [(p.name, r) for p, r in intake.skipped] == [("data.png", "symbolic link — not followed")]


def test_an_archive_that_would_expand_past_the_limit_is_refused_unextracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sources.intake as intake_mod

    _write_png(tmp_path / "scan.png")
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(tmp_path / "scan.png", "scan.png")
    monkeypatch.setattr(intake_mod, "MAX_EXTRACT_BYTES", 10)
    root = tmp_path / "x"
    with pytest.raises(IntakeError, match="over the"):
        collect_scan_files(archive, extract_root=root)
    assert not root.exists()


def test_two_extractions_of_one_archive_at_once_both_succeed(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    _write_png(tmp_path / "scan.png")
    archive = tmp_path / "survey.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(tmp_path / "scan.png", "scan.png")
    root = tmp_path / "x"
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: collect_scan_files(archive, extract_root=root), range(4)))
    assert {r.files for r in results} == {results[0].files} and len(results[0].files) == 1
    assert [p.name for p in root.iterdir()] == [results[0].files[0].parent.name]  # no partials left


def test_a_missing_path_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(IntakeError, match="not found"):
        collect_scan_files(tmp_path / "nope")


# --- screenshot cleanup ---------------------------------------------------------------------


def test_a_screenshot_is_cropped_to_its_plot_and_red_ink_becomes_marks(tmp_path: Path) -> None:
    rgb = np.full((120, 160, 3), 255, np.uint8)  # white page with axis margins
    rgb[20:110, 30:150] = 110  # grey plot area
    red = (220, 20, 20)  # a 3 px stroke closing a box, the way an interpreter circles a target
    rgb[40:60, 50:53] = rgb[40:43, 50:80] = rgb[57:60, 50:80] = rgb[40:60, 77:80] = red
    path = tmp_path / "shot.png"
    Image.fromarray(rgb).save(path)
    frame = parse_image(path)
    assert frame.provenance["plot_crop"] == [20, 110, 30, 150]
    assert frame.image is not None and frame.image.shape == (90, 120)
    assert frame.provenance["annotator_marks"] == [[20, 20, 30, 20]]  # in cropped pixels
    assert abs(int(frame.image[30, 21]) - 110) < 15  # the ink itself is painted out


def test_a_plain_image_passes_through_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "plain.png"
    Image.fromarray(np.full((40, 50), 100, np.uint8)).save(path)
    frame = parse_image(path)
    assert frame.image is not None and frame.image.shape == (40, 50)
    assert "plot_crop" not in frame.provenance and "annotator_marks" not in frame.provenance


# --- config ---------------------------------------------------------------------------------


def _config_with(tmp_path: Path, edit) -> Path:  # type: ignore[no-untyped-def]
    raw = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    edit(raw)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_the_classical_backend_refuses_a_prompt_that_calls_r2_a_detector_confidence(tmp_path: Path) -> None:
    path = _config_with(tmp_path, lambda r: r["reasoning"].update(prompt_version="v1_finding"))
    with pytest.raises(ConfigError, match="requires reasoning.prompt_version 'v1_candidate'"):
        load_config(path)


def test_an_unknown_backend_and_a_negative_chunk_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="detection.backend"):
        load_config(_config_with(tmp_path, lambda r: r["detection"].update(backend="magic")))
    with pytest.raises(ConfigError, match="chunk_traces must be a non-negative integer"):
        load_config(_config_with(tmp_path, lambda r: r["source"]["replay"].update(chunk_traces=-1)))


# --- what the dashboard receives ------------------------------------------------------------


def test_a_frame_event_carries_a_native_resolution_strip_placed_on_its_line() -> None:
    traces = np.random.default_rng(1).normal(size=(48, 64)).astype(np.float32)
    event = frame_to_dict(_frame(traces, trace_offset=16, line_complete=False, provenance={"path": "/d/Single-01.RA1"}))
    assert (event["width"], event["height"], event["trace_offset"]) == (48, 64, 16)
    assert event["line_name"] == "Single-01.RA1" and event["line_complete"] is False
    import base64

    assert len(base64.b64decode(event["pixels"])) == 48 * 64


def test_location_serialises_or_stays_null() -> None:
    assert location_to_dict(None) is None
    assert location_to_dict(TraceBox(1, 5, 2, 9)) == {"trace_start": 1, "trace_end": 5, "sample_start": 2, "sample_end": 9}


# --- gaps closed after review ---------------------------------------------------------------


def test_a_findings_location_survives_a_round_trip_through_the_store(tmp_path: Path) -> None:
    from core.contracts import Evidence, Finding
    from store.duckdb_store import DuckDBStore

    ev = Evidence(
        detection_class="clear_point_reflector", detection_confidence=0.9, depth_m=None, depth_confidence="unavailable",
        position_m=None, position_confidence="unavailable", amplitude=None, amplitude_confidence="unavailable",
        hyperbola_width_px=10.0,
    )
    store = DuckDBStore(tmp_path / "s.duckdb")
    try:
        frame_id = store.save_frame("s", "l", _frame(np.zeros((4, 4), np.float32)))
        with_box = Finding(evidence=ev, risk_level="LOW", risk_score=0.1, location=TraceBox(3, 9, 20, 40))
        store.save_finding("s", "l", frame_id, with_box)
        store.save_finding("s", "l", frame_id, Finding(evidence=ev, risk_level="LOW", risk_score=0.1))
        assert [f.location for f in store.get_findings_by_survey("s")] == [TraceBox(3, 9, 20, 40), None]
    finally:
        store.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [({"SPR_SHAFT_INTERVAL": "0.025"}, 0.025), ({"SPR_SHAFT_INTERVAL": "0"}, None),
     ({"SPR_SHAFT_INTERVAL": "-1"}, None), ({"SPR_SHAFT_INTERVAL": "nope"}, None), ({}, None)],
)
def test_spr_trace_spacing_is_the_encoder_interval_or_none_never_a_guess(raw: dict[str, str], expected: float | None) -> None:
    from parsers.spr import _trace_spacing_m

    assert _trace_spacing_m(raw) == expected


def test_a_calibrated_position_is_also_offset_to_the_findings_own_box() -> None:
    from core.config import EvidenceConfig

    caps = replace(_CAPS, has_real_position=True)
    frame = _frame(np.zeros((100, 64), np.float32), position=2.0, position_source="wheel_encoder")
    det = Detection(class_name="point_reflector", confidence=0.9, bbox_xyxy=(40.0, 0.0, 60.0, 10.0))
    ev = extract_evidence(det, frame, caps, EvidenceConfig(assumed_max_depth_m=5.0), image_shape=(64, 100))
    assert (ev.position_m, ev.position_confidence) == (pytest.approx(2.0 + 50 * _SPACING), "calibrated")


def test_the_display_strip_removes_the_flat_band_and_centres_on_mid_grey() -> None:
    from render.bscan import display_strip

    traces = np.tile(np.linspace(0, 50, 16, dtype=np.float32), (8, 1))  # same trace everywhere: all "direct wave"
    traces[3, 10] += 40.0  # one real reflection
    strip = display_strip(_frame(traces))
    assert strip.shape == (16, 8)  # rows = time, cols = traces
    assert abs(int(strip[0, 0]) - 128) <= 6  # the flat band is gone: mid-grey
    assert strip[10, 3] == 255  # the reflection saturates bright
    assert (display_strip(_frame(np.zeros((8, 16), np.float32))) == 128).all()  # a dead signal stays grey
