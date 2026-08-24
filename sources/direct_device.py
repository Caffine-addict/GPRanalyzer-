"""DirectDeviceSource — for reading straight off the device (serial/USB export, vendor SDK, or a manual export folder).

Same status as sources/edge_gateway.py: structurally complete against the
ScanSource ABC and wired into sources/factory.py (source.type: direct_device),
but frames() raises NotImplementedError until the company confirms how the
device actually exposes data — polling an export folder, a vendor SDK call,
or something else entirely.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.config import DirectDeviceSourceConfig
from core.contracts import ScanFrame, SourceCapabilities
from sources.base import ScanSource


class DirectDeviceSource(ScanSource):
    def __init__(self, config: DirectDeviceSourceConfig) -> None:
        self._config = config

    def frames(self) -> Iterator[ScanFrame]:
        raise NotImplementedError(
            "DirectDeviceSource is not yet implemented: how the device exposes "
            f"data (configured export_directory={self._config.export_directory!r}, "
            f"poll_interval_s={self._config.poll_interval_s}) is a placeholder "
            "until the company confirms the device. Implement against the real "
            "spec when it arrives — see CLAUDE.md."
        )
        yield  # pragma: no cover - unreachable; keeps this a generator function

    def capabilities(self) -> SourceCapabilities:
        # Conservative until the real device proves otherwise.
        return SourceCapabilities(
            has_calibrated_depth=False,
            has_real_position=False,
            has_true_amplitude=False,
            latency_class="near_realtime",
        )
