"""Tests for the not-yet-implemented plugin sources: they must satisfy the ABC,
report honest conservative capabilities, and fail loudly (not silently) when
frames() is actually called before real device specs exist.
"""

from __future__ import annotations

import pytest

from core.config import DirectDeviceSourceConfig, EdgeGatewaySourceConfig
from sources.direct_device import DirectDeviceSource
from sources.edge_gateway import EdgeGatewaySource


def test_edge_gateway_capabilities_are_conservative() -> None:
    source = EdgeGatewaySource(
        EdgeGatewaySourceConfig(host="127.0.0.1", port=1883, protocol="mqtt", topic="gpr/frames")
    )
    caps = source.capabilities()
    assert caps.has_calibrated_depth is False
    assert caps.has_real_position is False
    assert caps.has_true_amplitude is False


def test_edge_gateway_frames_raises_not_implemented_with_context() -> None:
    source = EdgeGatewaySource(
        EdgeGatewaySourceConfig(host="127.0.0.1", port=1883, protocol="mqtt", topic="gpr/frames")
    )
    with pytest.raises(NotImplementedError, match="mqtt"):
        next(source.frames())


def test_direct_device_capabilities_are_conservative() -> None:
    source = DirectDeviceSource(
        DirectDeviceSourceConfig(export_directory=".tmp/export", poll_interval_s=1.0)
    )
    caps = source.capabilities()
    assert caps.has_calibrated_depth is False
    assert caps.has_real_position is False
    assert caps.has_true_amplitude is False


def test_direct_device_frames_raises_not_implemented_with_context() -> None:
    source = DirectDeviceSource(
        DirectDeviceSourceConfig(export_directory=".tmp/export", poll_interval_s=1.0)
    )
    with pytest.raises(NotImplementedError, match="export"):
        next(source.frames())
