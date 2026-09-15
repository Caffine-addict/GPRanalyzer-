"""Check a measurement rule against simulated scenes whose answers are known.

The `cavities` call in `detect/refine.py` rests on one physical claim: a reflection off
an air gap keeps the direct wave's polarity, while one off wetter ground, water or metal
flips it. That is textbook, but it has not been checked on this instrument, and a
`cavities` finding is the class the risk model weights highest. Simulated scenes are the
one place both answers are known — we placed every void and every trench — so this module
runs the exact rule on them and counts how often it is right.

The rule is tested *where the object actually is* (its label box), not where a detector
happens to draw one, so a miss here is the rule's fault and not the detector's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from detect.measure import echo_matches_direct_wave_polarity
from simulate import convert, labels
from simulate.dataset import load_simulated_frames
from simulate.instrument import SAMPLE_INTERVAL_NS


@dataclass(frozen=True)
class CavityRuleReport:
    voids_called_cavity: int
    voids_total: int
    zones_called_cavity: int  # false alarms: broken-up ground reported as an air void
    zones_total: int
    undecided: int  # boxes where the rule found no coherent echo and declined to call it
    skipped: tuple[str, ...]


def cavity_rule_report(runs_dir: Path, *, seed: int = 0) -> CavityRuleReport:
    """Run the cavity polarity rule on every visible void and trench under `runs_dir`."""
    frames, skipped = load_simulated_frames(runs_dir)
    if not frames:
        raise ValueError(f"no usable simulated scenes under {runs_dir}")

    gain, _ = convert.time_gain_for([frame for _, frame in frames])
    rng = np.random.default_rng(seed)
    voids_hit = voids_total = zones_hit = zones_total = undecided = 0
    for scene, frame in frames:
        gained = convert.apply_gain(frame, gain)
        sigma = convert.noise_sigma(gained.total)
        result = labels.label_scene(scene, gained.scattered, sigma)
        traces = convert.add_noise(gained.total, sigma, rng).T  # (n_traces, n_samples), as the pipeline holds them
        for box in result.boxes:
            kind = box.source.split("#")[0]
            if kind not in ("Void", "DisturbedZone"):
                continue
            call = echo_matches_direct_wave_polarity(
                traces,
                slice(box.sample_start, box.sample_end + 1),
                slice(box.trace_start, box.trace_end + 1),
                SAMPLE_INTERVAL_NS,
            )
            undecided += call is None
            if kind == "Void":
                voids_total += 1
                voids_hit += call is True
            else:
                zones_total += 1
                zones_hit += call is True
    return CavityRuleReport(voids_hit, voids_total, zones_hit, zones_total, undecided, tuple(skipped))
