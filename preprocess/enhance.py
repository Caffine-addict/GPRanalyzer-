"""Image enhancement: bilateral filter, adaptive CLAHE, optional NL-means. All parameters from config."""

from __future__ import annotations

import logging
import time

import cv2
import numpy as np

from core.config import EnhancementConfig

logger = logging.getLogger(__name__)


def enhance(image: np.ndarray, config: EnhancementConfig) -> np.ndarray:
    """Bilateral filter -> adaptive CLAHE -> optional NL-means.

    Returns a new array; never mutates `image`. Expects a single-channel
    (grayscale) uint8 image, matching what parsers/image.py and
    render/bscan.py both produce.
    """
    if image.ndim != 2:
        raise ValueError(f"enhance() expects a single-channel (grayscale) image, got shape {image.shape}")
    if image.dtype != np.uint8:
        # cv2.bilateralFilter/createCLAHE either reject other dtypes with an
        # opaque C++ assertion error or silently misbehave (e.g. float32
        # slips past bilateralFilter but crashes createCLAHE.apply()) —
        # fail clearly here instead, same principle as detect.model's
        # ModelNotFoundError.
        raise ValueError(f"enhance() expects a uint8 image, got dtype {image.dtype}")

    total_start = time.monotonic()
    out = image

    start = time.monotonic()
    out = cv2.bilateralFilter(
        out, d=config.bilateral_d, sigmaColor=config.bilateral_sigma, sigmaSpace=config.bilateral_sigma
    )
    logger.info("enhance.bilateral latency_ms=%.2f", (time.monotonic() - start) * 1000)

    start = time.monotonic()
    std_dev = float(np.std(out))
    # Adaptive clip limit: noisier regions (higher std_dev) get more contrast
    # boost, capped at clahe_clip_limit_max so flat/low-noise frames aren't
    # over-amplified.
    clip_limit = min(config.clahe_clip_limit_max, std_dev / config.clahe_clip_divisor)
    clip_limit = max(clip_limit, 1e-3)  # cv2.createCLAHE requires a positive clip limit
    clahe = cv2.createCLAHE(
        clipLimit=clip_limit,
        tileGridSize=(config.clahe_tile_grid_size, config.clahe_tile_grid_size),
    )
    out = clahe.apply(out)
    logger.info(
        "enhance.clahe clip_limit=%.4f std_dev=%.4f latency_ms=%.2f",
        clip_limit,
        std_dev,
        (time.monotonic() - start) * 1000,
    )

    if config.nlmeans_enabled:
        start = time.monotonic()
        out = cv2.fastNlMeansDenoising(
            out,
            h=config.nlmeans_h,
            templateWindowSize=config.nlmeans_template_window,
            searchWindowSize=config.nlmeans_search_window,
        )
        logger.info("enhance.nlmeans latency_ms=%.2f", (time.monotonic() - start) * 1000)

    logger.info("enhance.total latency_ms=%.2f", (time.monotonic() - total_start) * 1000)
    return out
