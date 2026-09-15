"""Typed config loading. Fails loudly on any missing key — no defaults scattered through the code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when config.yaml is missing, unparseable, or missing a required key."""


def _require(d: dict[str, Any], key: str, path: str) -> Any:
    # A present-but-null value (e.g. "conf_threshold:" with nothing after the
    # colon) is treated the same as a missing key — both are a config author
    # failing to provide a real value. Falsy-but-real values (False, 0, "")
    # are untouched, since the check is `is None`, not truthiness.
    if key not in d or d[key] is None:
        raise ConfigError(f"missing required config key: {path}.{key}")
    return d[key]


def _require_mapping(d: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = _require(d, key, path)
    if not isinstance(value, dict):
        raise ConfigError(
            f"config key {path}.{key} must be a mapping, got {type(value).__name__}"
        )
    return value


@dataclass(frozen=True)
class DetectionConfig:
    weights_path: str
    conf_threshold: float
    iou_threshold: float
    classes: tuple[str, ...]  # what the detector's weights may output (an allowlist)
    taxonomy: tuple[str, ...]  # what the pipeline reports; shapes are refined into these


@dataclass(frozen=True)
class EnhancementConfig:
    bilateral_d: int
    bilateral_sigma: float
    clahe_clip_limit_max: float
    clahe_clip_divisor: float
    clahe_tile_grid_size: int
    nlmeans_enabled: bool
    nlmeans_h: float
    nlmeans_template_window: int
    nlmeans_search_window: int


@dataclass(frozen=True)
class RiskConfig:
    weights: dict[str, float]
    default_weight: float
    low_max: float
    medium_max: float
    utility_classes: tuple[str, ...]
    high_confidence_elongated_threshold: float
    high_confidence_elongated_min_count: int


@dataclass(frozen=True)
class EvidenceConfig:
    assumed_max_depth_m: float


@dataclass(frozen=True)
class ReasoningConfig:
    provider: str
    model: str
    fallback_model: str
    temperature: float
    max_tokens: int
    timeout_s: float
    strict_json: bool
    prompt_version: str


@dataclass(frozen=True)
class ReplaySourceConfig:
    directory: str
    playback_rate_hz: float
    step_mode: bool


@dataclass(frozen=True)
class EdgeGatewaySourceConfig:
    host: str
    port: int
    protocol: str
    topic: str


@dataclass(frozen=True)
class DirectDeviceSourceConfig:
    export_directory: str
    poll_interval_s: float


@dataclass(frozen=True)
class SourceConfig:
    type: str
    replay: ReplaySourceConfig
    edge_gateway: EdgeGatewaySourceConfig
    direct_device: DirectDeviceSourceConfig


@dataclass(frozen=True)
class LatencyConfig:
    fast_path_target_ms: float
    reasoning_target_ms: float


@dataclass(frozen=True)
class StoreConfig:
    backend: str
    path: str


@dataclass(frozen=True)
class ApiConfig:
    # No auth exists anywhere in this API (an accepted internal-tool trust
    # model — see CLAUDE.md), which makes this allowlist the only thing
    # standing between an unrelated webpage the operator's browser happens
    # to visit and this API: a wildcard origin would let any such page read
    # survey/finding data or start/stop surveys through the operator's own
    # browser as a network pivot, even with no direct network access of its
    # own. Keep this to actual known dashboard origins, not "*".
    cors_origins: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    detection: DetectionConfig
    enhancement: EnhancementConfig
    risk: RiskConfig
    evidence: EvidenceConfig
    reasoning: ReasoningConfig
    source: SourceConfig
    latency: LatencyConfig
    store: StoreConfig
    api: ApiConfig


def _class_names(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(name, str) and name for name in value):
        raise ConfigError(f"detection.{key} must be a non-empty list of class names")
    if len(set(value)) != len(value):
        raise ConfigError(f"detection.{key} lists a class more than once")
    return tuple(value)


def _build_detection(d: dict[str, Any]) -> DetectionConfig:
    return DetectionConfig(
        weights_path=_require(d, "weights_path", "detection"),
        conf_threshold=_require(d, "conf_threshold", "detection"),
        iou_threshold=_require(d, "iou_threshold", "detection"),
        classes=_class_names(_require(d, "classes", "detection"), "classes"),
        taxonomy=_class_names(_require(d, "taxonomy", "detection"), "taxonomy"),
    )


def _build_enhancement(d: dict[str, Any]) -> EnhancementConfig:
    return EnhancementConfig(
        bilateral_d=_require(d, "bilateral_d", "enhancement"),
        bilateral_sigma=_require(d, "bilateral_sigma", "enhancement"),
        clahe_clip_limit_max=_require(d, "clahe_clip_limit_max", "enhancement"),
        clahe_clip_divisor=_require(d, "clahe_clip_divisor", "enhancement"),
        clahe_tile_grid_size=_require(d, "clahe_tile_grid_size", "enhancement"),
        nlmeans_enabled=_require(d, "nlmeans_enabled", "enhancement"),
        nlmeans_h=_require(d, "nlmeans_h", "enhancement"),
        nlmeans_template_window=_require(d, "nlmeans_template_window", "enhancement"),
        nlmeans_search_window=_require(d, "nlmeans_search_window", "enhancement"),
    )


def _validate_weight(name: str, value: Any, path: str) -> None:
    if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ConfigError(
            f"{path} must be a number in [0, 1] — risk/score.py divides by total "
            f"detection count, so a weight outside this range could push "
            f"risk_score outside the [0, 1] range Finding requires, got {value!r}"
        )


def _build_risk(d: dict[str, Any]) -> RiskConfig:
    thresholds = _require_mapping(d, "thresholds", "risk")
    weights = _require_mapping(d, "weights", "risk")
    escalation = _require_mapping(d, "escalation", "risk")
    low_max = _require(thresholds, "low_max", "risk.thresholds")
    medium_max = _require(thresholds, "medium_max", "risk.thresholds")
    if not low_max < medium_max:
        raise ConfigError(
            f"risk.thresholds.low_max ({low_max}) must be less than "
            f"risk.thresholds.medium_max ({medium_max})"
        )

    default_weight = _require(d, "default_weight", "risk")
    _validate_weight("default_weight", default_weight, "risk.default_weight")
    for class_name, weight in weights.items():
        _validate_weight(class_name, weight, f"risk.weights.{class_name}")

    high_confidence_elongated_min_count = _require(
        escalation, "high_confidence_elongated_min_count", "risk.escalation"
    )
    if high_confidence_elongated_min_count < 1:
        raise ConfigError(
            "risk.escalation.high_confidence_elongated_min_count must be >= 1 "
            f"(0 would make the escalation rule fire with zero detections), "
            f"got {high_confidence_elongated_min_count}"
        )

    return RiskConfig(
        weights=dict(weights),
        default_weight=default_weight,
        low_max=low_max,
        medium_max=medium_max,
        utility_classes=tuple(_require(escalation, "utility_classes", "risk.escalation")),
        high_confidence_elongated_threshold=_require(
            escalation, "high_confidence_elongated_threshold", "risk.escalation"
        ),
        high_confidence_elongated_min_count=high_confidence_elongated_min_count,
    )


def _build_evidence(d: dict[str, Any]) -> EvidenceConfig:
    return EvidenceConfig(
        assumed_max_depth_m=_require(d, "assumed_max_depth_m", "evidence"),
    )


def _build_reasoning(d: dict[str, Any]) -> ReasoningConfig:
    return ReasoningConfig(
        provider=_require(d, "provider", "reasoning"),
        model=_require(d, "model", "reasoning"),
        fallback_model=_require(d, "fallback_model", "reasoning"),
        temperature=_require(d, "temperature", "reasoning"),
        max_tokens=_require(d, "max_tokens", "reasoning"),
        timeout_s=_require(d, "timeout_s", "reasoning"),
        strict_json=_require(d, "strict_json", "reasoning"),
        prompt_version=_require(d, "prompt_version", "reasoning"),
    )


def _build_source(d: dict[str, Any]) -> SourceConfig:
    replay_d = _require_mapping(d, "replay", "source")
    gateway_d = _require_mapping(d, "edge_gateway", "source")
    direct_d = _require_mapping(d, "direct_device", "source")
    return SourceConfig(
        type=_require(d, "type", "source"),
        replay=ReplaySourceConfig(
            directory=_require(replay_d, "directory", "source.replay"),
            playback_rate_hz=_require(replay_d, "playback_rate_hz", "source.replay"),
            step_mode=_require(replay_d, "step_mode", "source.replay"),
        ),
        edge_gateway=EdgeGatewaySourceConfig(
            host=_require(gateway_d, "host", "source.edge_gateway"),
            port=_require(gateway_d, "port", "source.edge_gateway"),
            protocol=_require(gateway_d, "protocol", "source.edge_gateway"),
            topic=_require(gateway_d, "topic", "source.edge_gateway"),
        ),
        direct_device=DirectDeviceSourceConfig(
            export_directory=_require(direct_d, "export_directory", "source.direct_device"),
            poll_interval_s=_require(direct_d, "poll_interval_s", "source.direct_device"),
        ),
    )


def _build_latency(d: dict[str, Any]) -> LatencyConfig:
    return LatencyConfig(
        fast_path_target_ms=_require(d, "fast_path_target_ms", "latency"),
        reasoning_target_ms=_require(d, "reasoning_target_ms", "latency"),
    )


def _build_store(d: dict[str, Any]) -> StoreConfig:
    return StoreConfig(
        backend=_require(d, "backend", "store"),
        path=_require(d, "path", "store"),
    )


def _build_api(d: dict[str, Any]) -> ApiConfig:
    return ApiConfig(cors_origins=tuple(_require(d, "cors_origins", "api")))


def load_config(path: str | Path = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")

    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"config file did not parse to a mapping: {p}")

    return Config(
        detection=_build_detection(_require_mapping(raw, "detection", "root")),
        enhancement=_build_enhancement(_require_mapping(raw, "enhancement", "root")),
        risk=_build_risk(_require_mapping(raw, "risk", "root")),
        evidence=_build_evidence(_require_mapping(raw, "evidence", "root")),
        reasoning=_build_reasoning(_require_mapping(raw, "reasoning", "root")),
        source=_build_source(_require_mapping(raw, "source", "root")),
        latency=_build_latency(_require_mapping(raw, "latency", "root")),
        store=_build_store(_require_mapping(raw, "store", "root")),
        api=_build_api(_require_mapping(raw, "api", "root")),
    )
