"""Tests for core/config.py — must fail loudly, never silently default.

A mutation-testing pass found the original suite couldn't detect a silent
default (`d.get(key, fallback)` swapped in for `_require`) or a copy-paste
key-mapping bug, despite 100% coverage — because `_require` is one shared
line hit by *any* missing-key test, not by every key that flows through it.
The parametrized test below exercises every required key path individually;
the "loads all fields" test pins every field's actual value so a
field-mapping bug can't hide behind "the section loaded successfully."
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from core.config import ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = REPO_ROOT / "config.yaml"


def _raw_config() -> dict:
    return yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))


def _delete_path(raw: dict, path: tuple[str, ...]) -> dict:
    result = copy.deepcopy(raw)
    node = result
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return result


def _write(raw: dict, tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return p


def test_loads_all_fields_from_real_config() -> None:
    cfg = load_config(REAL_CONFIG)

    assert cfg.detection.weights_path == "weights/best.pt"
    assert cfg.detection.conf_threshold == 0.25
    assert cfg.detection.iou_threshold == 0.7
    assert cfg.detection.classes == ("point_reflector", "linear_reflector", "disturbed_or_void")
    assert set(cfg.detection.taxonomy) == {
        "cavities",
        "elongated_linear_target",
        "intersecting_linear_and_point_reflector",
        "strong_high_contrast_reflector",
        "multiple_point_reflectors",
        "low_snr_point_reflector",
        "cluttered_multi_target",
        "disturbed_zone",
        "clear_point_reflector",
    }

    assert cfg.enhancement.bilateral_d == 5
    assert cfg.enhancement.bilateral_sigma == 75
    assert cfg.enhancement.clahe_clip_limit_max == 4.0
    assert cfg.enhancement.clahe_clip_divisor == 32
    assert cfg.enhancement.clahe_tile_grid_size == 8
    assert cfg.enhancement.nlmeans_enabled is False
    assert cfg.enhancement.nlmeans_h == 10.0
    assert cfg.enhancement.nlmeans_template_window == 7
    assert cfg.enhancement.nlmeans_search_window == 21

    assert cfg.risk.weights == {
        "elongated_linear_target": 1.0,
        "cavities": 0.9,
        "clear_point_reflector": 0.7,
        "intersecting_linear_and_point_reflector": 0.9,
        "multiple_point_reflectors": 0.6,
        "cluttered_multi_target": 0.5,
        "low_snr_point_reflector": 0.4,
        "disturbed_zone": 0.3,
    }
    assert cfg.risk.default_weight == 0.2
    assert cfg.risk.low_max == 0.35
    assert cfg.risk.medium_max == 0.65
    assert set(cfg.risk.utility_classes) == {
        "elongated_linear_target",
        "intersecting_linear_and_point_reflector",
    }
    assert cfg.risk.high_confidence_elongated_threshold == 0.8
    assert cfg.risk.high_confidence_elongated_min_count == 2

    assert cfg.evidence.assumed_max_depth_m == 2.0

    assert cfg.reasoning.provider == "groq"
    assert cfg.reasoning.model == "openai/gpt-oss-120b"
    assert cfg.reasoning.fallback_model == "openai/gpt-oss-20b"
    assert cfg.reasoning.temperature == 0.2
    assert cfg.reasoning.max_tokens == 1024
    assert cfg.reasoning.timeout_s == 20
    assert cfg.reasoning.strict_json is True
    assert cfg.reasoning.prompt_version == "v1_finding"

    assert cfg.source.type == "replay"
    assert cfg.source.replay.directory == ".tmp/replay_fixtures"
    assert cfg.source.replay.playback_rate_hz == 2.0
    assert cfg.source.replay.step_mode is False
    assert cfg.source.edge_gateway.host == "127.0.0.1"
    assert cfg.source.edge_gateway.port == 1883
    assert cfg.source.edge_gateway.protocol == "mqtt"
    assert cfg.source.edge_gateway.topic == "gpr/frames"
    assert cfg.source.direct_device.export_directory == ".tmp/device_export"
    assert cfg.source.direct_device.poll_interval_s == 1.0

    assert cfg.latency.fast_path_target_ms == 500
    assert cfg.latency.reasoning_target_ms == 5000

    assert cfg.store.backend == "duckdb"
    assert cfg.store.path == "output/gpr.duckdb"

    assert cfg.api.cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")


def test_missing_file_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(REPO_ROOT / "does_not_exist.yaml")


def test_non_mapping_config_raises(tmp_path: Path) -> None:
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="mapping"):
        load_config(bad_config)


def test_null_value_raises_config_error(tmp_path: Path) -> None:
    # Mutation guard: a present-but-empty YAML value (e.g. "conf_threshold:"
    # with nothing after it) parses to None and must not silently pass
    # through as a valid float.
    raw = _raw_config()
    raw["detection"]["conf_threshold"] = None
    with pytest.raises(ConfigError, match="conf_threshold"):
        load_config(_write(raw, tmp_path))


def test_non_mapping_nested_section_raises(tmp_path: Path) -> None:
    # Mutation guard: a nested section given as the wrong YAML type (list
    # instead of mapping) must raise ConfigError, not a raw TypeError from
    # `"key" not in <non-dict>`.
    raw = _raw_config()
    raw["risk"]["thresholds"] = ["not", "a", "mapping"]
    with pytest.raises(ConfigError, match="mapping"):
        load_config(_write(raw, tmp_path))


def test_null_top_level_section_raises(tmp_path: Path) -> None:
    raw = _raw_config()
    raw["detection"] = None
    with pytest.raises(ConfigError, match="detection"):
        load_config(_write(raw, tmp_path))


def test_risk_thresholds_inverted_ordering_raises(tmp_path: Path) -> None:
    raw = _raw_config()
    raw["risk"]["thresholds"]["low_max"] = 0.9
    raw["risk"]["thresholds"]["medium_max"] = 0.1
    with pytest.raises(ConfigError, match="low_max"):
        load_config(_write(raw, tmp_path))


def test_risk_class_weight_above_one_raises(tmp_path: Path) -> None:
    # score_detections divides by total detection count, so a weight > 1.0
    # could push risk_score outside the [0,1] range Finding requires —
    # catch it at config-load time, not wherever a Finding first blows up.
    raw = _raw_config()
    raw["risk"]["weights"]["cavities"] = 1.5
    with pytest.raises(ConfigError, match="risk.weights.cavities"):
        load_config(_write(raw, tmp_path))


def test_risk_class_weight_negative_raises(tmp_path: Path) -> None:
    raw = _raw_config()
    raw["risk"]["weights"]["cavities"] = -0.1
    with pytest.raises(ConfigError, match="risk.weights.cavities"):
        load_config(_write(raw, tmp_path))


def test_risk_default_weight_above_one_raises(tmp_path: Path) -> None:
    raw = _raw_config()
    raw["risk"]["default_weight"] = 1.2
    with pytest.raises(ConfigError, match="default_weight"):
        load_config(_write(raw, tmp_path))


def test_risk_high_confidence_elongated_min_count_zero_raises(tmp_path: Path) -> None:
    # 0 would make the escalation rule fire HIGH even with zero detections.
    raw = _raw_config()
    raw["risk"]["escalation"]["high_confidence_elongated_min_count"] = 0
    with pytest.raises(ConfigError, match="high_confidence_elongated_min_count"):
        load_config(_write(raw, tmp_path))


def test_a_duplicated_detection_class_name_raises(tmp_path: Path) -> None:
    # detect/model.py drops any detection whose class isn't in this list, and the
    # class-id order here has to match the trained checkpoint exactly — a silently
    # accepted duplicate would either hide a copy-paste mistake or leave two class
    # ids meaning the same name.
    raw = _raw_config()
    raw["detection"]["classes"] = [*raw["detection"]["classes"], raw["detection"]["classes"][0]]
    with pytest.raises(ConfigError, match="detection.classes lists a class more than once"):
        load_config(_write(raw, tmp_path))


def test_a_duplicated_taxonomy_class_name_raises(tmp_path: Path) -> None:
    raw = _raw_config()
    raw["detection"]["taxonomy"] = [*raw["detection"]["taxonomy"], raw["detection"]["taxonomy"][0]]
    with pytest.raises(ConfigError, match="detection.taxonomy lists a class more than once"):
        load_config(_write(raw, tmp_path))


# Every required key path in config.yaml, both leaf keys and whole
# subsections. One parametrized test closes what would otherwise be ~40
# near-duplicate tests, and covers every _require/_require_mapping call site
# individually rather than relying on "the shared function got hit once."
_REQUIRED_KEY_PATHS: list[tuple[str, ...]] = [
    ("detection", "weights_path"),
    ("detection", "conf_threshold"),
    ("detection", "iou_threshold"),
    ("detection", "classes"),
    ("enhancement", "bilateral_d"),
    ("enhancement", "bilateral_sigma"),
    ("enhancement", "clahe_clip_limit_max"),
    ("enhancement", "clahe_clip_divisor"),
    ("enhancement", "clahe_tile_grid_size"),
    ("enhancement", "nlmeans_enabled"),
    ("enhancement", "nlmeans_h"),
    ("enhancement", "nlmeans_template_window"),
    ("enhancement", "nlmeans_search_window"),
    ("risk", "weights"),
    ("risk", "default_weight"),
    ("risk", "thresholds"),
    ("risk", "thresholds", "low_max"),
    ("risk", "thresholds", "medium_max"),
    ("risk", "escalation"),
    ("risk", "escalation", "utility_classes"),
    ("risk", "escalation", "high_confidence_elongated_threshold"),
    ("risk", "escalation", "high_confidence_elongated_min_count"),
    ("evidence", "assumed_max_depth_m"),
    ("reasoning", "provider"),
    ("reasoning", "model"),
    ("reasoning", "fallback_model"),
    ("reasoning", "temperature"),
    ("reasoning", "max_tokens"),
    ("reasoning", "timeout_s"),
    ("reasoning", "strict_json"),
    ("reasoning", "prompt_version"),
    ("source", "type"),
    ("source", "replay"),
    ("source", "replay", "directory"),
    ("source", "replay", "playback_rate_hz"),
    ("source", "replay", "step_mode"),
    ("source", "edge_gateway"),
    ("source", "edge_gateway", "host"),
    ("source", "edge_gateway", "port"),
    ("source", "edge_gateway", "protocol"),
    ("source", "edge_gateway", "topic"),
    ("source", "direct_device"),
    ("source", "direct_device", "export_directory"),
    ("source", "direct_device", "poll_interval_s"),
    ("latency", "fast_path_target_ms"),
    ("latency", "reasoning_target_ms"),
    ("store", "backend"),
    ("store", "path"),
    ("api", "cors_origins"),
    ("detection",),
    ("enhancement",),
    ("risk",),
    ("evidence",),
    ("reasoning",),
    ("source",),
    ("latency",),
    ("store",),
    ("api",),
]


@pytest.mark.parametrize("path", _REQUIRED_KEY_PATHS, ids=lambda p: ".".join(p))
def test_missing_key_raises_config_error(path: tuple[str, ...], tmp_path: Path) -> None:
    raw = _delete_path(_raw_config(), path)
    with pytest.raises(ConfigError, match=path[-1]):
        load_config(_write(raw, tmp_path))
