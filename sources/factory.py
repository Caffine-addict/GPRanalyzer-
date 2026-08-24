"""Builds the configured ScanSource from typed config — switching sources is a config change, not a code change."""

from __future__ import annotations

from core.config import SourceConfig
from sources.base import ScanSource
from sources.direct_device import DirectDeviceSource
from sources.edge_gateway import EdgeGatewaySource
from sources.replay import ReplaySource

_BUILDERS = {
    "replay": lambda cfg: ReplaySource(cfg.replay),
    "edge_gateway": lambda cfg: EdgeGatewaySource(cfg.edge_gateway),
    "direct_device": lambda cfg: DirectDeviceSource(cfg.direct_device),
}


def create_source(config: SourceConfig) -> ScanSource:
    try:
        builder = _BUILDERS[config.type]
    except KeyError:
        raise ValueError(
            f"unknown source.type: {config.type!r} (expected one of {sorted(_BUILDERS)})"
        ) from None
    return builder(config)
