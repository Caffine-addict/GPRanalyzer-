"""Tests for preprocess/enhance.py."""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pytest

import preprocess.enhance as enhance_module
from core.config import EnhancementConfig
from preprocess.enhance import enhance


def _config(**overrides: object) -> EnhancementConfig:
    base: dict[str, object] = {
        "bilateral_d": 5,
        "bilateral_sigma": 75,
        "clahe_clip_limit_max": 4.0,
        "clahe_clip_divisor": 32,
        "clahe_tile_grid_size": 8,
        "nlmeans_enabled": False,
        "nlmeans_h": 10.0,
        "nlmeans_template_window": 7,
        "nlmeans_search_window": 21,
    }
    base.update(overrides)
    return EnhancementConfig(**base)  # type: ignore[arg-type]


def _random_image(shape: tuple[int, int] = (64, 64)) -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, size=shape, dtype=np.uint8)


def test_enhance_preserves_shape_and_dtype() -> None:
    image = _random_image()
    out = enhance(image, _config())
    assert out.shape == image.shape
    assert out.dtype == np.uint8


def test_enhance_does_not_mutate_input() -> None:
    image = _random_image()
    original = image.copy()
    enhance(image, _config())
    assert np.array_equal(image, original)


def test_enhance_rejects_non_2d_image() -> None:
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="single-channel"):
        enhance(image, _config())


def test_enhance_handles_flat_image_without_crashing() -> None:
    # std_dev=0 -> the clip_limit formula alone would compute exactly 0.0,
    # which cv2.createCLAHE rejects; this exercises the floor guard.
    image = np.full((32, 32), 128, dtype=np.uint8)
    out = enhance(image, _config())
    assert out.shape == image.shape


def test_enhance_with_nlmeans_enabled_runs_extra_step() -> None:
    image = _random_image()
    out = enhance(image, _config(nlmeans_enabled=True))
    assert out.shape == image.shape


def test_enhance_logs_per_stage_latency(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="preprocess.enhance"):
        enhance(_random_image(), _config(nlmeans_enabled=True))

    messages = [r.getMessage() for r in caplog.records]
    assert any("enhance.bilateral" in m for m in messages)
    assert any("enhance.clahe" in m for m in messages)
    assert any("enhance.nlmeans" in m for m in messages)
    assert any("enhance.total" in m for m in messages)


def test_enhance_skips_nlmeans_log_when_disabled(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="preprocess.enhance"):
        enhance(_random_image(), _config(nlmeans_enabled=False))

    messages = [r.getMessage() for r in caplog.records]
    assert not any("enhance.nlmeans" in m for m in messages)


def test_enhance_clip_limit_is_capped_at_max(caplog: pytest.LogCaptureFixture) -> None:
    rng = np.random.default_rng(3)
    noisy = rng.integers(0, 255, size=(64, 64), dtype=np.uint8)
    # divisor=1 makes std_dev/divisor huge, so the cap is what actually binds.
    cfg = _config(clahe_clip_limit_max=1.5, clahe_clip_divisor=1)

    with caplog.at_level(logging.INFO, logger="preprocess.enhance"):
        enhance(noisy, cfg)

    clahe_messages = [r.getMessage() for r in caplog.records if "enhance.clahe" in r.getMessage()]
    assert clahe_messages
    assert "clip_limit=1.5000" in clahe_messages[0]


def test_enhance_flat_image_uses_floor_not_bare_zero(caplog: pytest.LogCaptureFixture) -> None:
    # std_dev=0 -> the formula alone gives exactly 0.0, which cv2.createCLAHE
    # would treat as "no clipping" (fully saturated output) rather than
    # raising — assert the floor guard's value actually reaches CLAHE, not
    # just that enhance() doesn't crash.
    image = np.full((32, 32), 128, dtype=np.uint8)
    with caplog.at_level(logging.INFO, logger="preprocess.enhance"):
        enhance(image, _config())

    clahe_messages = [r.getMessage() for r in caplog.records if "enhance.clahe" in r.getMessage()]
    assert clahe_messages
    assert "clip_limit=0.0010" in clahe_messages[0]


def test_enhance_clip_limit_matches_std_dev_over_divisor_when_uncapped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    image = _random_image()
    cfg = _config(clahe_clip_limit_max=10.0, clahe_clip_divisor=50)

    # Compute the expected value independently, against the post-bilateral
    # image (what enhance() actually measures std_dev from), not the raw
    # input — and confirm neither the cap nor the floor is binding, so this
    # test is actually exercising the divisor rather than accidentally
    # passing regardless of it.
    filtered = cv2.bilateralFilter(
        image, d=cfg.bilateral_d, sigmaColor=cfg.bilateral_sigma, sigmaSpace=cfg.bilateral_sigma
    )
    expected_std_dev = float(np.std(filtered))
    expected_clip_limit = min(cfg.clahe_clip_limit_max, expected_std_dev / cfg.clahe_clip_divisor)
    expected_clip_limit = max(expected_clip_limit, 1e-3)
    assert 1e-3 < expected_clip_limit < cfg.clahe_clip_limit_max, (
        "test setup invalid: cap or floor is binding, this wouldn't exercise the divisor"
    )

    with caplog.at_level(logging.INFO, logger="preprocess.enhance"):
        enhance(image, cfg)

    clahe_messages = [r.getMessage() for r in caplog.records if "enhance.clahe" in r.getMessage()]
    assert clahe_messages
    assert f"clip_limit={expected_clip_limit:.4f}" in clahe_messages[0]


def test_enhance_bilateral_filter_receives_correct_d_and_sigma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def _fake_bilateral_filter(src, d, sigmaColor, sigmaSpace):
        calls.append({"d": d, "sigmaColor": sigmaColor, "sigmaSpace": sigmaSpace})
        return src

    monkeypatch.setattr(enhance_module.cv2, "bilateralFilter", _fake_bilateral_filter)

    cfg = _config(bilateral_d=5, bilateral_sigma=75)
    enhance(_random_image(), cfg)

    assert calls == [{"d": 5, "sigmaColor": 75, "sigmaSpace": 75}]


def test_enhance_rejects_non_uint8_dtype() -> None:
    image = _random_image().astype(np.float32)
    with pytest.raises(ValueError, match="uint8"):
        enhance(image, _config())
