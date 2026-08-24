"""Tests for the source factory — selecting a source must be a config change, not a code change."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.config import load_config
from sources.direct_device import DirectDeviceSource
from sources.edge_gateway import EdgeGatewaySource
from sources.factory import create_source
from sources.replay import ReplaySource

REAL_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def test_factory_builds_replay_source_from_real_config() -> None:
    cfg = load_config(REAL_CONFIG)
    source = create_source(cfg.source)
    assert isinstance(source, ReplaySource)


def test_factory_builds_edge_gateway_source() -> None:
    cfg = load_config(REAL_CONFIG)
    source = create_source(replace(cfg.source, type="edge_gateway"))
    assert isinstance(source, EdgeGatewaySource)


def test_factory_builds_direct_device_source() -> None:
    cfg = load_config(REAL_CONFIG)
    source = create_source(replace(cfg.source, type="direct_device"))
    assert isinstance(source, DirectDeviceSource)


def test_factory_rejects_unknown_source_type() -> None:
    cfg = load_config(REAL_CONFIG)
    with pytest.raises(ValueError, match="unknown source.type"):
        create_source(replace(cfg.source, type="carrier_pigeon"))
