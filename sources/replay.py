"""ReplaySource — plays back a directory of scan files at survey speed.

This is the development driver for the whole project: everything through
Session 9 is built and tested against this source, with no hardware
dependency at all.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from core.config import ReplaySourceConfig
from core.contracts import ScanFrame, SourceCapabilities
from parsers.base import get_parser, registered_extensions
from sources.base import ScanSource


class ReplaySource(ScanSource):
    """Plays back a directory of scan files in filename order at a configured rate.

    Position is synthesized as a monotonic per-frame counter — a replayed
    file set has no real survey position — marked position_source="synthetic"
    so downstream code (and capabilities()) are honest about it.

    step_mode=True disables the inter-frame sleep so tests can drain frames()
    instantly; callers wanting genuine single-step control just call next()
    on the iterator returned by frames() instead of consuming it in a loop.
    """

    def __init__(self, config: ReplaySourceConfig) -> None:
        self._directory = Path(config.directory)
        self._playback_rate_hz = config.playback_rate_hz
        self._step_mode = config.step_mode

    def _frame_paths(self) -> list[Path]:
        if not self._directory.exists():
            raise FileNotFoundError(f"replay directory not found: {self._directory}")
        # Derived from the parser registry rather than a second, independent extension list —
        # a format that can be parsed (parsers/image.py, parsers/spr.py, ...) is automatically
        # replayable; nothing here needs editing when a new parser registers.
        supported = registered_extensions()
        paths = sorted(
            p
            for p in self._directory.iterdir()
            if p.is_file() and p.suffix.lower().lstrip(".") in supported
        )
        if not paths:
            raise FileNotFoundError(f"no supported scan files found in: {self._directory}")
        return paths

    def frames(self) -> Iterator[ScanFrame]:
        interval_s = 0.0 if self._step_mode else (1.0 / self._playback_rate_hz)
        for position, path in enumerate(self._frame_paths()):
            parser = get_parser(path)
            frame = parser(path)
            yield replace(frame, position=float(position), position_source="synthetic")
            if interval_s > 0:
                time.sleep(interval_s)

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            has_calibrated_depth=False,
            has_real_position=False,
            has_true_amplitude=False,
            latency_class="batch",
        )
