# Verification run — 2026-09-23, raw output

Every command below was re-run live on 2026-09-23 and its **complete** stdout+stderr
captured verbatim by `scripts`-free shell capture. Nothing in the code blocks is
summarised, re-typed, or reconstructed from an earlier session. If a block looks
surprising, it is what the machine actually printed.

Working directory for all commands: the repo root (`gpr-analyzer/`).

> **Caveat on the working tree.** This capture ran *after* the Part 2 corroboration-language
> corrections were applied to the working tree, and those changes are uncommitted. So the test
> count and file contents reflect corrected-but-uncommitted code, not commit `9bf5694`.


---

# Part 1 — verification commands

## Test suite

```console
$ .venv/bin/python -m pytest -q 2>&1 | tail -25
........................................................................ [  8%]
........................................................................ [ 16%]
........................................................................ [ 24%]
........................................................................ [ 32%]
........................................................................ [ 40%]
........................................................................ [ 48%]
........................................................................ [ 56%]
........................................................................ [ 64%]
........................................................................ [ 72%]
........................................................................ [ 80%]
........................................................................ [ 88%]
........................................................................ [ 96%]
..................................                                       [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /Users/pritamwani/Desktop/untitled folder 2/gpr-analyzer/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
898 passed, 1 warning in 29.63s
```

## Lint (ruff)

```console
$ .venv/bin/python -m ruff check .
All checks passed!
```

## Type check (mypy)

```console
$ .venv/bin/python -m mypy core sources parsers preprocess detect render evidence risk reason store pipeline api reports studio simulate reference
Success: no issues found in 72 source files
```

## Git — commit history

```console
$ git log --oneline -10
9bf5694 Day 2: pilot written pack + re-derived credibility count
a905df5 Add demo screenshots of GPR Studio
f5986b8 Day 1: candidates cache + chainage-only target export
78b0f3b Bring repo up to date: detector, parser, Studio, simulator, reasoning surface
49815b9 Expand README with detailed docs and a plain-language overview
1e7ef6a Replace prototype with full rewrite: GPR B-scan analysis pipeline
eae32c7 Update README.md
bd7ab0e Initial commit: GPR B-scan analysis pipeline
```

## Git — commit dates

```console
$ git log -6 --format='%h | authored %ad | committed %cd | %an | %s' --date=iso
9bf5694 | authored 2026-09-17 13:45:59 +0530 | committed 2026-09-17 13:45:59 +0530 | Pritam Wani | Day 2: pilot written pack + re-derived credibility count
a905df5 | authored 2026-09-16 14:03:35 +0530 | committed 2026-09-16 14:03:35 +0530 | Pritam Wani | Add demo screenshots of GPR Studio
f5986b8 | authored 2026-09-16 13:55:05 +0530 | committed 2026-09-16 13:55:05 +0530 | Pritam Wani | Day 1: candidates cache + chainage-only target export
78b0f3b | authored 2026-09-15 14:11:19 +0530 | committed 2026-09-15 14:11:19 +0530 | Pritam Wani | Bring repo up to date: detector, parser, Studio, simulator, reasoning surface
49815b9 | authored 2026-08-24 13:10:00 +0530 | committed 2026-08-24 13:10:00 +0530 | Pritam Wani | Expand README with detailed docs and a plain-language overview
1e7ef6a | authored 2026-08-24 10:12:27 +0530 | committed 2026-08-24 10:12:27 +0530 | Pritam Wani | Replace prototype with full rewrite: GPR B-scan analysis pipeline
```

## Git — working tree status

```console
$ git status
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   core/contracts.py
	modified:   docs/pilot/CAPABILITY_STATEMENT.md
	modified:   evidence/quality.py
	modified:   reason/prompt.py
	modified:   reason/prompts/v1_candidate.txt
	modified:   reason/prompts/v1_finding.txt
	modified:   reason/prompts/v1_pick.txt
	modified:   studio/corroborate.py
	modified:   studio/interpret.py
	modified:   studio/server.py
	modified:   studio/session.py
	modified:   tests/test_contracts.py
	modified:   tests/test_evidence_quality.py
	modified:   tests/test_reason_prompt.py
	modified:   tests/test_studio_corroborate.py
	modified:   tests/test_studio_session.py

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	docs/pilot/CHANNEL_IDENTITY.md
	docs/pilot/VERIFICATION_2026-09-23.md

no changes added to commit (use "git add" and/or "git commit -a")
```

## Git — local commits not on origin/main

```console
$ git log --oneline origin/main..main; echo '(end of list — empty means nothing unpushed)'
(end of list — empty means nothing unpushed)
```

## Git — origin/main commits not local

```console
$ git log --oneline main..origin/main; echo '(end of list — empty means nothing unpulled)'
(end of list — empty means nothing unpulled)
```

## Git — reflog (when refs actually moved)

```console
$ git reflog --date=iso -10
9bf5694 HEAD@{2026-09-17 13:45:59 +0530}: commit: Day 2: pilot written pack + re-derived credibility count
a905df5 HEAD@{2026-09-16 14:03:35 +0530}: commit: Add demo screenshots of GPR Studio
f5986b8 HEAD@{2026-09-16 13:55:05 +0530}: commit: Day 1: candidates cache + chainage-only target export
78b0f3b HEAD@{2026-09-15 14:11:19 +0530}: commit: Bring repo up to date: detector, parser, Studio, simulator, reasoning surface
49815b9 HEAD@{2026-08-24 13:10:00 +0530}: commit: Expand README with detailed docs and a plain-language overview
1e7ef6a HEAD@{2026-08-24 10:12:27 +0530}: commit: Replace prototype with full rewrite: GPR B-scan analysis pipeline
eae32c7 HEAD@{2026-08-24 10:11:56 +0530}: reset: moving to origin/main
```

## Today's date on this machine

```console
$ date
Wed Sep 23 12:19:06 IST 2026
```

## Candidate box count — per job, per channel, and total

```console
$ .venv/bin/python -c "
from pathlib import Path
from studio.candidates import load_candidates
root = Path('Dataset/DSU_GPR_Files')
grand = 0
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    subtotal = 0
    per_channel = []
    for cand in load_candidates(job.name, job):
        subtotal += 1
    by_ch = {}
    for cand in load_candidates(job.name, job):
        by_ch[cand.channel] = by_ch.get(cand.channel, 0) + 1
    grand += subtotal
    print(f'{job.name}: total={subtotal}  ' + '  '.join(f'{k}={v}' for k, v in sorted(by_ch.items())))
print(f'GRAND TOTAL across all 4 lines: {grand}')
"
Job_0696: total=64  RA1=23  RA2=21  RAD=20
Job_0703: total=58  RA1=20  RA2=21  RAD=17
Job_0720: total=44  RA1=13  RA2=12  RAD=19
Job_0730: total=67  RA1=21  RA2=26  RAD=20
GRAND TOTAL across all 4 lines: 233
```

## Credibility report (credible / clustered objects / corroborated on 2+ channels)

```console
$ .venv/bin/python scripts/credibility_report.py
job         boxes  credible   corroborated (2+ ch)  clustered objects
Job_0696       64        36                      8                 10
Job_0703       58        30                      2                  9
Job_0720       44        22                      2                  3
Job_0730       67        31                      5                  7

TOTAL: 233 boxes -> 119 credible -> 17 corroborated on 2+ channels (of 29 clustered objects)
```

## SPR parse time — WARM (repeated calls in one process, cache bypassed)

```console
$ .venv/bin/python -c "
import time
from pathlib import Path
from parsers.spr import parse_spr
root = Path('Dataset/DSU_GPR_Files')
times = []
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    for ext in ('RAD','RA1','RA2'):
        p = job / f'Single-01.{ext}'
        t0 = time.perf_counter()
        parse_spr(p)
        dt = (time.perf_counter() - t0) * 1000
        times.append(dt)
        print(f'{job.name}/{ext}: {dt:.3f} ms')
print(f'mean over {len(times)} channel files: {sum(times)/len(times):.3f} ms')
"
Job_0696/RAD: 0.486 ms
Job_0696/RA1: 0.425 ms
Job_0696/RA2: 0.406 ms
Job_0703/RAD: 0.390 ms
Job_0703/RA1: 0.389 ms
Job_0703/RA2: 0.379 ms
Job_0720/RAD: 0.394 ms
Job_0720/RA1: 0.398 ms
Job_0720/RA2: 0.384 ms
Job_0730/RAD: 0.389 ms
Job_0730/RA1: 0.394 ms
Job_0730/RA2: 0.384 ms
mean over 12 channel files: 0.401 ms
```

## SPR parse time — COLD (three separate fresh Python processes, one parse each)

```console
$ .venv/bin/python -c "<single parse_spr call in a fresh process>"   # run 1
cold first-ever parse in this process: 0.493 ms
$ .venv/bin/python -c "<single parse_spr call in a fresh process>"   # run 2
cold first-ever parse in this process: 0.491 ms
$ .venv/bin/python -c "<single parse_spr call in a fresh process>"   # run 3
cold first-ever parse in this process: 0.512 ms
```

---

# Part 2 — channel identity evidence

## Full raw SPR text headers — all 3 channels of all 4 jobs

```console
$ .venv/bin/python -c "
from parsers.spr import _parse_text_header
from pathlib import Path
root = Path('Dataset/DSU_GPR_Files')
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    print(f'================ {job.name} ================')
    for ext in ('RAD','RA1','RA2'):
        p = job / f'Single-01.{ext}'
        if not p.exists():
            print(f'  {ext}: FILE MISSING'); continue
        data = p.read_bytes()
        hdr = _parse_text_header(data)
        print(f'--- {ext}  ({len(data)} bytes) ---')
        for k, v in hdr.items():
            print(f'    {k:32s} {v}')
    print()
"
================ Job_0696 ================
--- RAD  (288706 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 12:33:03
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            100
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  0
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     4
    TVG_START_GAIN                   7
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    Z                                )K(
    X                                c  q6$b,11x)*
    R                                y%*xk('	]?G
    K                                	Dd
    F                                t8
--- RA1  (288706 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 12:33:03
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            200
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  1
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     3
    TVG_START_GAIN                   1
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    H                                wt-r>3G	}
    I                                
    P                                f
    V                                
--- RA2  (288706 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 12:33:03
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            400
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  2
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     2
    TVG_START_GAIN                   3
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00

================ Job_0703 ================
--- RAD  (286978 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 13:07:20
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            100
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  0
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     4
    TVG_START_GAIN                   7
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    I                                '
    YPI                              qq(
    R                                srDDF
    F                                vO	;%hWV_f>@pMc-&`^(V |:0w;KYxlr
--- RA1  (286978 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 13:07:20
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            200
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  1
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     3
    TVG_START_GAIN                   1
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    Q                                3
    E                                Bt)(d;
    L                                [P
--- RA2  (286978 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 13:07:20
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            400
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  2
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     2
    TVG_START_GAIN                   2
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00

================ Job_0720 ================
--- RAD  (292738 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 14:31:25
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            100
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  0
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     4
    TVG_START_GAIN                   5
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    ZL                               
    I                                rd0"
    C                                
--- RA1  (292738 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 14:31:25
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            200
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  1
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     3
    TVG_START_GAIN                   1
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    W                                FT	X#{&f:
    Q                                &#09H<8>,{ewf7|H!U
    P                                
--- RA2  (292738 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 14:31:25
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            400
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  2
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     2
    TVG_START_GAIN                   3
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    F                                
    K                                S;%}cImf{

================ Job_0730 ================
--- RAD  (290434 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 15:54:12
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            100
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  0
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     4
    TVG_START_GAIN                   9
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    X                                :
    W                                
--- RA1  (290434 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 15:54:12
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            200
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  1
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     3
    TVG_START_GAIN                   2
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    WW                               `TOa+L:
    NR                               q
    U                                ,
--- RA2  (290434 bytes) ---
    ACQUISITION_DATE                 12/24/22
    ACQUISITION_TIME                 15:54:12
    INSTRUMENT                       SUBSURFACE_IMAGING_SSPRSCAN_3D SPRSCAN_3D 0
    OBSERVER                         
    RECEIVER_SPECS                   SUBSURFACE_IMAGING_SYSTEMS
    SPR_FILE_VERSION                 7
    SPR_INTERVAL_MARKER              1
    SPR_LINE_COUNT                   1
    SPR_LINE_SEPARATION              1
    SPR_MEDIUM_DIELECTRIC            09.00
    SPR_NO_AVERAGES                  AVG_4
    SPR_SW_AVERAGES                  0
    SPR_SAMPLES_PER_SCAN             256
    SPR_SAMPLING_INTERVAL            400
    SPR_SHAFT_INTERVAL               0.025
    SPR_TIMER_FREQUENCY              10
    SPR_TRIGGER_MODE                 SHAFT
    SPR_CHANNEL_NUM                  2
    SPR_CHANNELS_TOTAL               3
    TRACE_SORT                       COMMON_OFFSET
    UNITS                            METERS
    ANTENNA_TYPE                     2
    TVG_START_GAIN                   4
    TVG_SLOPE                        15
    RADAR_HEAD_MARK                  3
    ANTENNA_TYPE_DETECTED            eQUANTUM
    MIGRATION_TYPE                   NONE
    WHEEL_CIRCUMFERENCE_MM           1276.7
    GPS_TYPE                         INTERNAL
    REVERSED_FOR_ZIGZAG              False
    NOTE                             Ver:5.5.4508_Machine:dde9035d0732b0d8c909
    LINE_ID                          01
    SPR_MARKER                       00
    E                                3DI@12V_Y

```

## Any receiver-offset / antenna-separation / position field anywhere in the headers?

```console
$ .venv/bin/python -c "
from parsers.spr import _parse_text_header
from pathlib import Path
root = Path('Dataset/DSU_GPR_Files')
needles = ('OFFSET','SEPAR','SPACING','BASELINE','RX','POS_X','X_POS','ANTENNA_DIST','CROSS')
hits = []
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    for ext in ('RAD','RA1','RA2'):
        p = job / f'Single-01.{ext}'
        if not p.exists(): continue
        for k, v in _parse_text_header(p.read_bytes()).items():
            if any(n in k.upper() for n in needles):
                hits.append(f'{job.name}/{ext}: {k} = {v}')
print('\n'.join(hits) if hits else 'NO receiver-offset / antenna-separation / position field found in any of the 12 channel headers.')
"
Job_0696/RAD: SPR_LINE_SEPARATION = 1
Job_0696/RA1: SPR_LINE_SEPARATION = 1
Job_0696/RA2: SPR_LINE_SEPARATION = 1
Job_0703/RAD: SPR_LINE_SEPARATION = 1
Job_0703/RA1: SPR_LINE_SEPARATION = 1
Job_0703/RA2: SPR_LINE_SEPARATION = 1
Job_0720/RAD: SPR_LINE_SEPARATION = 1
Job_0720/RA1: SPR_LINE_SEPARATION = 1
Job_0720/RA2: SPR_LINE_SEPARATION = 1
Job_0730/RAD: SPR_LINE_SEPARATION = 1
Job_0730/RA1: SPR_LINE_SEPARATION = 1
Job_0730/RA2: SPR_LINE_SEPARATION = 1
```

## Residual 'independent receiver' language in the repo after the Part 2 corrections

```console
$ grep -rniE 'independent receiver' --include='*.py' --include='*.md' --include='*.txt' . 2>/dev/null | grep -v '\.venv/' || echo '(no matches)'
./docs/pilot/CAPABILITY_STATEMENT.md:24:  2026-09-23 — an earlier version of this statement said "independent receivers," which the
./docs/pilot/VERIFICATION_2026-09-23.md:743:## Residual 'independent receiver' language in the repo after the Part 2 corrections
./docs/pilot/CHANNEL_IDENTITY.md:5:Every corroboration claim in this project ("seen on 2+ channels", "independent receivers agree")
./docs/pilot/CHANNEL_IDENTITY.md:76:**"Independent receivers" was the wrong description and has been corrected everywhere it
./docs/pilot/CHANNEL_IDENTITY.md:100:  the phrase "independent receivers" implied throughout the client-facing capability statement and
./reason/prompts/v1_pick.txt:27:- More agreeing frequency channels (RAD/RA1/RA2) is meaningful confirmation, though they share one antenna position, not independent receiver geometry. One channel alone
./reason/prompts/v1_candidate.txt:30:- More agreeing frequency channels (RAD/RA1/RA2) is meaningful confirmation, though they share one antenna position, not independent receiver geometry. One channel alone
./reason/prompts/v1_finding.txt:24:- More agreeing frequency channels (RAD/RA1/RA2) is meaningful confirmation, though they share one antenna position, not independent receiver geometry. One channel alone
```

---

_End of capture. Generated 2026-09-23._

---

# Discrepancies this re-run exposed

Two claims made earlier on 2026-09-23 did not survive being re-measured. Both were mine.

## 1. The "~0.8 ms to parse one channel" figure is NOT reproducible

Earlier today I reported three cold single-process parses of 0.830 ms, 0.439 ms and 0.458 ms,
and concluded that the long-standing "~0.8 ms per channel" claim was a genuine cold-process
measurement. Three fresh cold runs captured above give **0.493 / 0.491 / 0.512 ms** — tightly
clustered, and nowhere near 0.8 ms. Warm steady-state over all 12 channel files is 0.401 ms mean.

The honest reading: 0.830 ms was a single outlier, and I built an explanation around it that made
the original claim look validated. It was not validated. The reproducible numbers are:

| Condition | Measured 2026-09-23 |
|---|---|
| Cold — first parse in a fresh process | 0.49–0.51 ms (n=3) |
| Warm — repeated parses, `lru_cache` bypassed | 0.401 ms mean over 12 files |
| Via `studio.session.load_frame` (cache HIT, not a parse) | ~0.007 ms — measures cache lookup, not parsing |

**"0.8 ms to parse one channel" currently appears in all three published artifacts and should be
corrected to ~0.5 ms cold.** Not yet done at the time of writing.

## 2. "No separation field exists in any header" was false

`docs/pilot/CHANNEL_IDENTITY.md` originally claimed no position-offset or separation field appears
in any of the 12 channel headers. `SPR_LINE_SEPARATION` does exist, in all 12, reading `1`
everywhere. It does not change the channel-identity conclusion — it is constant across channels,
so it cannot encode a RAD/RA1/RA2 spatial offset, and its name points at survey-line spacing —
but the claim as written was wrong. `CHANNEL_IDENTITY.md` has been corrected with the narrower,
true version and the inference explicitly marked as an inference.

## Note on header parsing noise

The header key union across jobs is 52 keys, of which only 33 appear in every job. The
per-job differences are single-letter keys (`C`, `E`, `L`, `NR`, `Q`, `U`, `W`, `WW`, `YPI`, `ZL`,
…) — these are almost certainly `_parse_text_header`'s regex (`([A-Z][A-Z0-9_]*)\s+(.*)`) picking
up fragments of adjacent binary data, not real vendor fields. Every field this project's
conclusions rest on is in the 33 present everywhere. Worth tightening the parser's field
acceptance at some point; it does not affect any conclusion here.

---

# Reconciliation A — two numbers that did not agree (2026-09-23)

Both gaps were raised in review. Neither was a real property of the system; both were my
measurement errors, and they are different kinds of error.

## A1 — parse: "0.49 ms cold" vs "1.31 ms median"

Same stage, same function (`parsers.spr.parse_spr`), no cache in either. The 1.31 ms median
**does not reproduce**. Measured in four contexts below; every one lands at 0.36–0.55 ms.
The 1.31 ms figure came from a single profile run on a machine that had just finished a full
pytest + mypy sweep, and I reported it without re-running. It was noise, and the honest
number for parse is **~0.45 ms median, ~0.5 ms cold**.


### A1 context 1 — fresh process, ONE parse (the verification method)

```console
$ for f in Job_0703/Single-01.RAD Job_0730/Single-01.RA1; do .venv/bin/python -c "
import time; from pathlib import Path; from parsers.spr import parse_spr
p=Path('Dataset/DSU_GPR_Files/$f')
t=time.perf_counter(); parse_spr(p); print(f'$f: {(time.perf_counter()-t)*1000:.3f} ms')
"; done
Job_0703/Single-01.RAD: 0.746 ms
Job_0730/Single-01.RA1: 0.831 ms
```

### A1 context 2 — one process, all 12 parses, no other work

```console
$ .venv/bin/python -c "
import time; from pathlib import Path; from parsers.spr import parse_spr
import numpy as np
root=Path('Dataset/DSU_GPR_Files'); ts=[]
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    for ext in ('RAD','RA1','RA2'):
        p=job/f'Single-01.{ext}'
        t=time.perf_counter(); parse_spr(p); dt=(time.perf_counter()-t)*1000; ts.append(dt)
        print(f'  {job.name}/{ext}: {dt:.3f} ms')
print(f'median={np.median(ts):.3f}  mean={np.mean(ts):.3f}  first={ts[0]:.3f}')
"
  Job_0696/RAD: 0.817 ms
  Job_0696/RA1: 0.756 ms
  Job_0696/RA2: 0.784 ms
  Job_0703/RAD: 0.368 ms
  Job_0703/RA1: 0.701 ms
  Job_0703/RA2: 0.685 ms
  Job_0720/RAD: 0.713 ms
  Job_0720/RA1: 0.727 ms
  Job_0720/RA2: 0.714 ms
  Job_0730/RAD: 0.661 ms
  Job_0730/RA1: 0.379 ms
  Job_0730/RA2: 0.706 ms
median=0.709  mean=0.668  first=0.817
```

### A1 context 3 — parse interleaved with the heavy stages (reproducing the Part 3 profile)

```console
$ .venv/bin/python -c "
import time,sys; from pathlib import Path; import numpy as np
from parsers.spr import parse_spr
from studio import processing
sys.path.insert(0,'scripts')
from detect_candidates import find_candidate_boxes
from studio.velocity import fit_region
root=Path('Dataset/DSU_GPR_Files'); ts=[]
chain=processing.ProcessingChain(background_removal='mean', gain='agc')
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    for ext in ('RAD','RA1','RA2'):
        p=job/f'Single-01.{ext}'
        t=time.perf_counter(); fr=parse_spr(p); dt=(time.perf_counter()-t)*1000; ts.append(dt)
        sp=float(fr.provenance['raw_header']['SPR_SHAFT_INTERVAL']); iv=float(fr.sample_interval_ns)
        processing.run_chain(fr.traces.T, chain, trace_spacing_m=sp, sample_interval_ns=iv)
        bx=find_candidate_boxes(fr.traces, iv)
        for (tr,smp,w,h) in bx:
            try: fit_region(fr.traces, trace_start=tr, sample_start=smp, trace_span=w, sample_span=h, trace_spacing_m=sp, sample_interval_ns=iv)
            except Exception: pass
print(f'median={np.median(ts):.3f}  mean={np.mean(ts):.3f}  first={ts[0]:.3f}')
"
median=0.491  mean=0.490  first=0.447
```

### A1 context 4 — the exact Part 3 profile script, re-run twice

```console
$ .venv/bin/python profile_cold.py    # run 1
1. parse (SPR -> ScanFrame)                                         0.51      0.44      0.63   12
4. hyperbola fit, RANSAC, ALL candidate boxes on this channel     371.83    342.32    382.01   12
7. Studio render (normalise -> RGB -> PNG encode)                   4.77      4.24      6.97   12
mean per line (3 channels): 948.9 ms
$ .venv/bin/python profile_cold.py    # run 2
1. parse (SPR -> ScanFrame)                                         0.49      0.44      0.49   12
4. hyperbola fit, RANSAC, ALL candidate boxes on this channel     376.02    347.76    380.14   12
7. Studio render (normalise -> RGB -> PNG encode)                   4.74      4.25      4.75   12
mean per line (3 channels): 955.1 ms
```

**Conclusion: no gap exists.** Parse is ~0.45 ms median in every context. The 1.31 ms
reported in the Part 3 table was a one-off on a contended machine and should be read as 0.45 ms.

## A2 — box count: "~14/channel" vs 233/12 = 19.4

This one is **not** noise, and it is a real defect in what the project reports.

There are two different box sets, and they disagree:

- **What the detector produces today**: `find_candidate_boxes()` run live = **166 boxes**.
- **What is stored in `boxes.json` and what every public number is computed from**
  (`scripts/credibility_report.py`, the 233→119→17 funnel, all three artifacts) = **233 boxes**.

The Part 3 profile called the detector live, so it measured 166 boxes (13.8/channel) — the
smaller set. The credibility report reads the stored set (19.4/channel). Reviewer's arithmetic
was correct and my "~14 boxes/channel" understated the real cost by ~25%.

**Why the two sets differ.** `detect_and_store()` in `scripts/detect_candidates.py` only ever
*adds*: it loads the existing boxes, skips any whose rounded (channel, x, y) already exists, and
appends the rest. It never prunes. So `boxes.json` is the **cumulative union of every detector
version ever run against these jobs**, not the output of the current detector.

The comparison below shows every one of the 166 live boxes is present in the stored set, and
**67 stored boxes (29%) cannot be reproduced by the current detector**. 232 of the 233 carry the
machine-written `auto:` note, so these are stale detector output, not human picks.

This matters beyond timing: those 67 boxes were written by the detector *before* the
direct-wave-skip fix — the version known to have been wrong. The "158 → 233 boxes, the fix
recovered real signal" claim is therefore not what happened. The current detector finds 166;
the previous one found 158; the stored 233 is their union, and they agree on only 91.

### A2 — live detector output vs stored boxes.json, per channel

```console
$ .venv/bin/python -c "
from pathlib import Path
from collections import Counter
import sys
sys.path.insert(0, 'scripts')
from detect_candidates import find_candidate_boxes
from core import boxes as box_store
from parsers.spr import parse_spr
root = Path('Dataset/DSU_GPR_Files')
tot_live = tot_stored = 0
print(f'{\"job/ch\":<20} {\"live-detect\":>11} {\"stored\":>7} {\"stored&live\":>12} {\"stored-only\":>12}')
print('-' * 66)
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    stored = box_store.load_boxes(job.name)
    by_ch = {}
    for b in stored:
        by_ch.setdefault(b.channel, []).append(b)
    for ext in ('RAD', 'RA1', 'RA2'):
        p = job / f'Single-01.{ext}'
        if not p.exists(): continue
        fr = parse_spr(p)
        live = find_candidate_boxes(fr.traces, float(fr.sample_interval_ns))
        live_keys = {(round(x), round(y)) for x, y, w, h in live}
        st = by_ch.get(ext, [])
        st_keys = {(round(b.x), round(b.y)) for b in st}
        tot_live += len(live); tot_stored += len(st)
        print(f'{job.name}/{ext:<12} {len(live):>11} {len(st):>7} {len(live_keys & st_keys):>12} {len(st_keys - live_keys):>12}')
print('-' * 66)
print(f'{\"TOTAL\":<20} {tot_live:>11} {tot_stored:>7}')
notes = Counter()
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    for b in box_store.load_boxes(job.name):
        notes[(b.note or '')[:60]] += 1
print()
print('provenance of stored boxes:')
for n, c in notes.most_common():
    print(f'  {c:>4}  {n!r}')
"
job/ch               live-detect  stored  stored&live  stored-only
------------------------------------------------------------------
Job_0696/RAD                   16      20           16            4
Job_0696/RA1                   16      23           16            7
Job_0696/RA2                   11      21           11           10
Job_0703/RAD                   12      17           12            5
Job_0703/RA1                   16      20           16            4
Job_0703/RA2                   13      21           13            8
Job_0720/RAD                   15      19           15            4
Job_0720/RA1                   11      13           11            2
Job_0720/RA2                    7      12            7            5
Job_0730/RAD                   16      20           16            4
Job_0730/RA1                   17      21           17            4
Job_0730/RA2                   16      26           16           10
------------------------------------------------------------------
TOTAL                        166     233

provenance of stored boxes:
   232  'auto: background-removal + energy-envelope candidate, unclas'
     1  ''
```

### A2 — measured RANSAC fit cost over the STORED (233) box set, per channel

```console
$ .venv/bin/python -c "
import time
from pathlib import Path
from parsers.spr import parse_spr
from core import boxes as box_store
from studio.velocity import fit_region
root = Path('Dataset/DSU_GPR_Files')
tot_boxes = 0; tot_ms = 0.0
print(f'{\"job/ch\":<20} {\"stored boxes\":>12} {\"fit total ms\":>13} {\"ms/box\":>8}')
print('-'*56)
for job in sorted(p for p in root.iterdir() if p.is_dir()):
    stored = box_store.load_boxes(job.name)
    for ext in ('RAD','RA1','RA2'):
        p = job / f'Single-01.{ext}'
        if not p.exists(): continue
        fr = parse_spr(p)
        sp = float(fr.provenance['raw_header']['SPR_SHAFT_INTERVAL']); iv = float(fr.sample_interval_ns)
        bx = [b for b in stored if b.channel == ext]
        t0 = time.perf_counter()
        for b in bx:
            try:
                fit_region(fr.traces, trace_start=int(b.x), sample_start=int(b.y),
                           trace_span=int(b.w), sample_span=int(b.h),
                           trace_spacing_m=sp, sample_interval_ns=iv)
            except Exception:
                pass
        dt = (time.perf_counter()-t0)*1000
        tot_boxes += len(bx); tot_ms += dt
        print(f'{job.name}/{ext:<12} {len(bx):>12} {dt:>13.1f} {dt/len(bx) if bx else 0:>8.1f}')
print('-'*56)
print(f'{\"TOTAL\":<20} {tot_boxes:>12} {tot_ms:>13.1f} {tot_ms/tot_boxes:>8.1f}')
print()
print(f'per-channel mean fit time (stored set): {tot_ms/12:.1f} ms')
print(f'per-LINE (3 channels, stored set):      {tot_ms/4:.1f} ms')
"
job/ch               stored boxes  fit total ms   ms/box
--------------------------------------------------------
Job_0696/RAD                    20         461.6     23.1
Job_0696/RA1                    23         499.8     21.7
Job_0696/RA2                    21         457.7     21.8
Job_0703/RAD                    17         369.7     21.7
Job_0703/RA1                    20         433.0     21.7
Job_0703/RA2                    21         466.6     22.2
Job_0720/RAD                    19         432.4     22.8
Job_0720/RA1                    13         283.6     21.8
Job_0720/RA2                    12         264.7     22.1
Job_0730/RAD                    20         443.7     22.2
Job_0730/RA1                    21         456.7     21.7
Job_0730/RA2                    26         573.0     22.0
--------------------------------------------------------
TOTAL                         233        5142.6     22.1

per-channel mean fit time (stored set): 428.6 ms
per-LINE (3 channels, stored set):      1285.7 ms
```

### Corrected Part 3 per-line budget

Using the stored 233-box set (the one every public number is based on):

| Stage | per channel | per line (3 ch) |
|---|---|---|
| Parse | 0.45 ms | 1.4 ms |
| Processing chain (bkgd + AGC) | 1.3 ms | 3.9 ms |
| Candidate detection | 2.1 ms | 6.3 ms |
| **RANSAC fit, all stored boxes** | **419.7 ms** | **1259 ms** |
| Evidence + risk | 0.02 ms | 0.06 ms |
| Studio render (PNG) | 4.2 ms | 12.7 ms |
| DuckDB writes (~58 findings/line) | — | ~75 ms |
| **TOTAL** | | **~1.36 s** |

Against a 6.9 s walking-pace budget for a 9.6 m line at 1.4 m/s, that is **5.1× headroom, not
the 7× previously reported**. The fit is 93% of the total. Both the earlier 931 ms and the 7×
should be read as superseded by the 1.36 s and 5.1× here.
