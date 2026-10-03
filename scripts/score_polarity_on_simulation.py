"""Score echo-polarity rules against physics on UNIQUE simulated scenes (reproducible).

Usage: .venv/bin/python scripts/score_polarity_on_simulation.py sim_runs/<batch> [...]

Scenes are deduplicated by content and noise is seeded per scene, so the counts do not depend on
which run folders repeat a scene or on directory order (both inflated an earlier tally).
"""
import hashlib
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from detect.expert_checks import apex_polarity
from detect.measure import echo_matches_direct_wave_polarity
from simulate import convert, labels
from simulate.dataset import load_simulated_frames
from simulate.instrument import SAMPLE_INTERVAL_NS

EPS = {"plastic_air": 1.0, "plastic_water": 80.0, "concrete": 6.0}

def expected(scene, kind, idx):
    soil = scene.soil.eps_r
    if kind == "Pipe":
        p = scene.pipes[idx]
        return False if p.kind == "metal" else EPS[p.kind] < soil
    if kind == "Stone": return scene.stones[idx].eps_r < soil
    if kind == "Void": return True
    if kind == "Slab":
        s = scene.slabs[idx]; return False if s.kind == "metal" else 6.0 < soil
    if kind == "DisturbedZone": return False  # wetter ground: higher permittivity
    return None

seen = set(); res = {"box": Counter(), "apex": Counter()}; strong = {"box": Counter(), "apex": Counter()}
for runs in sys.argv[1:]:
    frames, _ = load_simulated_frames(Path(runs))
    if not frames: continue
    gain, _ = convert.time_gain_for([f for _, f in frames])
    for scene, frame in frames:
        h = hashlib.md5(repr(scene).encode()).hexdigest()
        if h in seen: continue
        seen.add(h); rng = np.random.default_rng(int(h[:8], 16))
        g = convert.apply_gain(frame, gain); sigma = convert.noise_sigma(g.total)
        lab = labels.label_scene(scene, g.scattered, sigma); traces = convert.add_noise(g.total, sigma, rng).T
        for box in lab.boxes:
            kind, _, idx = box.source.partition("#"); exp = expected(scene, kind, int(idx or 0))
            if exp is None: continue
            calls = {"box": echo_matches_direct_wave_polarity(traces, slice(box.sample_start, box.sample_end + 1), slice(box.trace_start, box.trace_end + 1), SAMPLE_INTERVAL_NS),
                     "apex": apex_polarity(traces, (box.trace_start + box.trace_end) / 2, box.sample_start, SAMPLE_INTERVAL_NS)}
            is_strong = kind in ("Void", "Slab", "DisturbedZone") or (kind == "Pipe" and scene.pipes[int(idx)].kind in ("metal", "plastic_water"))
            for m, c in calls.items():
                outcome = "unreadable" if c is None else ("right" if c == exp else "wrong")
                res[m][outcome] += 1
                if is_strong: strong[m][outcome] += 1
print("unique scenes:", len(seen))
for m in ("box", "apex"):
    r, s = res[m], strong[m]
    print(f"{m:5s} all objects: right {r['right']}, wrong {r['wrong']}, unreadable {r['unreadable']} "
          f"(right {r['right']}/{r['right']+r['wrong']} readable) | strong contrast only: right {s['right']}, wrong {s['wrong']}, unreadable {s['unreadable']}")
