"""ReplaySource — plays back recorded scans at survey speed.

This is the development driver for the whole project: everything through
Session 9 is built and tested against this source, with no hardware
dependency at all. The path may be one scan file, a folder, or an archive
(sources/intake.py); a traced line can be streamed in chunks the way a live
device would deliver it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from core.config import ReplaySourceConfig
from core.contracts import ScanFrame, SourceCapabilities
from parsers.base import get_parser
from sources.base import ScanSource
from sources.intake import Intake, IntakeError, collect_scan_files

logger = logging.getLogger(__name__)


class ReplaySource(ScanSource):
    """Plays back scan files in path order at a configured rate.

    With `chunk_traces` > 0, a frame with traces is sent as a sequence of windows ending
    `chunk_traces` further along the line each time, each carrying up to `window_traces` of
    context (0 = everything so far), with `trace_offset`/`line_complete` saying where it sits.
    Frames without traces (images) are always sent whole.

    Position is the wheel-encoder distance along the line when the file records a trace
    spacing (position_source="wheel_encoder" — chainage, not a map coordinate); otherwise a
    per-file counter marked position_source="synthetic", which evidence treats as unavailable.

    step_mode=True disables the inter-frame sleep so tests can drain frames()
    instantly; callers wanting genuine single-step control just call next()
    on the iterator returned by frames() instead of consuming it in a loop.
    """

    def __init__(self, config: ReplaySourceConfig) -> None:
        self._path = Path(config.path)
        self._playback_rate_hz = config.playback_rate_hz
        self._step_mode = config.step_mode
        self._chunk_traces = config.chunk_traces
        self._window_traces = config.window_traces
        self._intake: Intake | None = None

    def intake(self) -> Intake:
        """What the path resolved to: the scan files that will play, and what was skipped."""
        if self._intake is None:
            intake = collect_scan_files(self._path)
            if not intake.files:
                raise IntakeError(f"no supported scan files found in: {self._path}")
            for skipped, reason in intake.skipped:
                logger.info("replay.skipped path=%s reason=%s", skipped, reason)
            self._intake = intake
        return self._intake

    def frames(self) -> Iterator[ScanFrame]:
        interval_s = 0.0 if self._step_mode else (1.0 / self._playback_rate_hz)
        for index, path in enumerate(self.intake().files):
            for frame in self._frames_of(get_parser(path)(path), index):
                yield frame
                if interval_s > 0:
                    time.sleep(interval_s)

    def _frames_of(self, frame: ScanFrame, index: int) -> Iterator[ScanFrame]:
        if frame.traces is None or self._chunk_traces == 0:
            yield _placed(frame, 0, index)
            return
        n_traces = frame.traces.shape[0]
        for end in range(self._chunk_traces, n_traces + self._chunk_traces, self._chunk_traces):
            end = min(end, n_traces)
            start = 0 if self._window_traces == 0 else max(0, end - self._window_traces)
            window = replace(frame, traces=frame.traces[start:end], trace_offset=start, line_complete=end == n_traces)
            yield _placed(window, start, index)

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            has_calibrated_depth=False,
            has_real_position=False,
            has_true_amplitude=False,
            latency_class="batch",
        )


def _placed(frame: ScanFrame, first_trace: int, index: int) -> ScanFrame:
    if frame.trace_spacing_m is not None:
        return replace(frame, position=first_trace * frame.trace_spacing_m, position_source="wheel_encoder")
    return replace(frame, position=float(index), position_source="synthetic")
