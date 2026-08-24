"""Tests for the ScanSource ABC itself."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest

from core.contracts import ScanFrame, SourceCapabilities
from sources.base import ScanSource


def test_scan_source_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        ScanSource()  # type: ignore[abstract]


class _MinimalSource(ScanSource):
    def frames(self) -> Iterator[ScanFrame]:
        yield ScanFrame(source_type="test", provenance={}, image=np.zeros((1, 1)))

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            has_calibrated_depth=True,
            has_real_position=True,
            has_true_amplitude=True,
            latency_class="realtime",
        )


def test_latency_class_property_delegates_to_capabilities() -> None:
    source = _MinimalSource()
    assert source.latency_class == "realtime"


def test_minimal_source_yields_frames() -> None:
    source = _MinimalSource()
    frames = list(source.frames())
    assert len(frames) == 1
