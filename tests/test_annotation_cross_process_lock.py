"""Two *processes* writing one job's annotations must not lose each other's work.

`ANNOTATION_WRITE_LOCK` is a `threading.RLock`, which serialises writers inside one process and
does nothing at all between processes. That gap is not theoretical here: the detector runs as a
CLI (`scripts/detect_candidates.py --all`) while a Studio server may be serving an interpreter
in another process, and `replace_detector_boxes` rewrites the whole file. The losing interleave
destroys a human-drawn box — irreplaceable data — so this is tested against real subprocesses
rather than threads, because threads would pass against a lock that only works in-process.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from core import annotation_io
from core import boxes as box_store

_REPO = Path(__file__).resolve().parent.parent


def _run(code: str, annotations_root: Path) -> subprocess.Popen:
    """Run a snippet in a real separate interpreter, pointed at a temp annotations root."""
    program = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_REPO)!r})
        from pathlib import Path
        from core import annotation_io, boxes as box_store
        from studio import picks as pick_store
        # Both stores, always: a subprocess left pointing at the real annotations/ folder writes
        # straight into the operator's data. That happened once (2026-09-23) and is why this is here.
        box_store._ANNOTATIONS_ROOT = Path({str(annotations_root)!r})
        pick_store._ANNOTATIONS_ROOT = Path({str(annotations_root)!r})
    """) + textwrap.dedent(code)
    return subprocess.Popen([sys.executable, "-c", program], cwd=_REPO)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from studio import picks as pick_store

    annotations = tmp_path / "annotations"
    monkeypatch.setattr(box_store, "_ANNOTATIONS_ROOT", annotations)
    monkeypatch.setattr(pick_store, "_ANNOTATIONS_ROOT", annotations)
    return annotations


def test_the_lock_is_a_real_os_lock_that_blocks_another_process(root: Path) -> None:
    """A second process must *wait*, not sail through.

    This is the property a threading lock cannot provide, so it is checked directly: if the
    lock were still per-process, the waiter would return immediately and the elapsed time
    would be ~0 rather than the held duration.
    """
    holder = _run(
        """
        import time
        with annotation_io.job_lock(Path(box_store._ANNOTATIONS_ROOT) / "Job_X"):
            Path(box_store._ANNOTATIONS_ROOT / "Job_X" / "held").write_text("y")
            time.sleep(1.5)
        """,
        root,
    )
    # Wait for the holder to actually be inside the lock before racing it.
    flag = root / "Job_X" / "held"
    for _ in range(100):
        if flag.exists():
            break
        time.sleep(0.05)
    assert flag.exists(), "holder process never entered the lock"

    started = time.monotonic()
    with annotation_io.job_lock(root / "Job_X"):
        waited = time.monotonic() - started
    holder.wait(timeout=30)

    assert waited > 0.5, f"second process acquired the lock after only {waited:.3f}s — not a real lock"


def test_an_exception_inside_the_lock_still_releases_it_for_the_next_process(root: Path) -> None:
    """A crash mid-write must not leave the job wedged for every future writer.

    The holder raises inside `job_lock`'s own body — not inside the subprocess harness — so
    this exercises `job_lock`'s `try/finally` specifically, not just the outer `with` blocks
    around it. A version that released the OS lock only on the non-exception path (e.g. `yield`
    with no `finally`) would leave a real OS-level lock held forever, since nothing else in the
    process is left running to ever close that file handle.
    """
    holder = _run(
        """
        import time
        job_dir = Path(box_store._ANNOTATIONS_ROOT) / "Job_X"
        try:
            with annotation_io.job_lock(job_dir):
                (job_dir / "held").write_text("y")
                raise RuntimeError("simulated crash mid-write")
        except RuntimeError:
            pass
        # Stay alive well past the parent's wait below: if the process's own exit (closing its
        # file handles) were doing the real releasing, keeping it alive here would expose that.
        time.sleep(5)
        """,
        root,
    )
    flag = root / "Job_X" / "held"
    for _ in range(100):
        if flag.exists():
            break
        time.sleep(0.05)
    assert flag.exists(), "holder process never entered the lock"

    started = time.monotonic()
    with annotation_io.job_lock(root / "Job_X"):
        waited = time.monotonic() - started
    holder.wait(timeout=30)

    assert waited < 1.0, (
        f"waited {waited:.3f}s for a lock whose holder is still alive but already raised — "
        "the lock was not released on the exception path"
    )


def test_concurrent_processes_do_not_lose_each_others_boxes(root: Path) -> None:
    writer = """
        for i in range(25):
            box_store.add_box("Job_0703", channel="RAD", x=float(i), y=1.0, w=2.0, h=2.0,
                              note="operator " + str(i))
    """
    procs = [_run(writer, root) for _ in range(3)]
    for p in procs:
        assert p.wait(timeout=120) == 0

    stored = box_store.load_boxes("Job_0703")
    assert len(stored) == 75, f"expected 75 boxes from 3 processes x 25, got {len(stored)}"


def test_a_detector_run_cannot_destroy_a_human_box_written_concurrently(root: Path) -> None:
    """The interleave that matters, and the reason this file exists.

    Process A repeatedly replaces the detector's output (a full-file rewrite); process B is an
    operator marking targets. Without a cross-process lock, A computes its `kept` list from a
    snapshot taken before B's write and then overwrites B's box out of existence.
    """
    detector = """
        from core.boxes import DetectedBox
        for i in range(30):
            box_store.replace_detector_boxes(
                "Job_0703", detector="d", detector_version="v1",
                boxes=[DetectedBox(channel="RAD", x=float(i), y=9.0, w=3.0, h=3.0,
                                   note="auto: candidate")],
            )
    """
    human = """
        for i in range(30):
            box_store.add_box("Job_0703", channel="RA1", x=float(i), y=5.0, w=4.0, h=4.0,
                              note="operator marked this")
    """
    a, b = _run(detector, root), _run(human, root)
    assert a.wait(timeout=120) == 0
    assert b.wait(timeout=120) == 0

    stored = box_store.load_boxes("Job_0703")
    human_boxes = [x for x in stored if x.note == "operator marked this"]
    assert len(human_boxes) == 30, (
        f"{30 - len(human_boxes)} human-drawn box(es) destroyed by a concurrent detector run "
        f"— stored: {len(stored)} total, {len(human_boxes)} human"
    )
    # And the detector's own output is still exactly one run's worth, not an accumulation.
    detector_boxes = [x for x in stored if x.detector == "d"]
    assert len(detector_boxes) == 1, f"detector output accumulated: {len(detector_boxes)} boxes"


def test_locking_one_job_does_not_stall_a_different_job(root: Path) -> None:
    """A slow cross-process wait on Job_A must not block a request for unrelated Job_B.

    Regression target: an earlier version took one lock shared by every job before entering
    the (potentially slow, cross-process) OS-level wait, so a detector run on one job stalled
    every other job's requests in the same process — a real availability problem for a
    thread-pooled server, even though it never lost data.
    """
    holder = _run(
        """
        import time
        with annotation_io.job_lock(Path(box_store._ANNOTATIONS_ROOT) / "Job_A"):
            Path(box_store._ANNOTATIONS_ROOT / "Job_A" / "held").write_text("y")
            time.sleep(2.0)
        """,
        root,
    )
    flag = root / "Job_A" / "held"
    for _ in range(100):
        if flag.exists():
            break
        time.sleep(0.05)
    assert flag.exists(), "holder process never entered the lock"

    started = time.monotonic()
    with annotation_io.job_lock(root / "Job_B"):
        waited = time.monotonic() - started
    holder.wait(timeout=30)

    assert waited < 0.5, (
        f"waited {waited:.3f}s for an unrelated job's lock while Job_A's holder was still "
        "running — the two jobs are sharing a lock that should be per-job"
    )


# --------------------------------------------------- studio.picks: the same race, same fix

# Every test above exercises core.boxes. studio.picks.add_pick/delete_pick go through the same
# job_lock() machinery but were, until these two tests, completely unexercised under real
# concurrency — confirmed by mutation testing: un-wiring either call site back to the old
# per-process-only lock passed the entire suite silently. "No human pick lost" is literally
# what studio.picks.Pick represents, so this is the more direct case for that guarantee than
# the box tests above are.


def test_concurrent_processes_do_not_lose_each_others_interpreted_picks(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Against studio.picks directly, not core.boxes — this is specifically the gap mutation
    # testing found: add_pick was unprotected where add_box was already covered above.
    # studio.picks keeps its own module-level _ANNOTATIONS_ROOT, separate from core.boxes's —
    # the shared subprocess bootstrap only patches the latter, so it must be set here too.
    writer = """
        from studio import picks as pick_store
        pick_store._ANNOTATIONS_ROOT = Path(box_store._ANNOTATIONS_ROOT)
        for i in range(25):
            pick_store.add_pick(
                "Job_0703", channel="RAD", trace=float(i), sample=90.0, time_ns=9.0,
                depth_m=0.45, velocity_m_per_ns=0.1, velocity_source="assumed", dielectric=9.0,
                note="operator " + str(i),
            )
    """
    procs = [_run(writer, root) for _ in range(3)]
    for p in procs:
        assert p.wait(timeout=120) == 0

    from studio import picks as pick_store

    monkeypatch.setattr(pick_store, "_ANNOTATIONS_ROOT", root)
    stored = pick_store.load_picks("Job_0703")
    assert len(stored) == 75, f"expected 75 picks from 3 processes x 25, got {len(stored)} — a pick was lost"


def test_a_concurrent_delete_does_not_race_a_concurrent_add(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_pick and add_pick on the same job, at once — neither must silently lose the other's work."""
    from studio import picks as pick_store

    monkeypatch.setattr(pick_store, "_ANNOTATIONS_ROOT", root)
    seed_ids = [
        pick_store.add_pick(
            "Job_0703", channel="RAD", trace=float(i), sample=90.0, time_ns=9.0, depth_m=0.45,
            velocity_m_per_ns=0.1, velocity_source="assumed", dielectric=9.0, note="to be deleted",
        ).id
        for i in range(15)
    ]
    (root / "seed_ids.txt").write_text("\n".join(seed_ids))

    deleter = """
        from studio import picks as pick_store
        pick_store._ANNOTATIONS_ROOT = Path(box_store._ANNOTATIONS_ROOT)
        ids = (Path(box_store._ANNOTATIONS_ROOT) / "seed_ids.txt").read_text().splitlines()
        for pid in ids:
            pick_store.delete_pick("Job_0703", pid)
    """
    adder = """
        from studio import picks as pick_store
        pick_store._ANNOTATIONS_ROOT = Path(box_store._ANNOTATIONS_ROOT)
        from studio import picks as pick_store
        for i in range(15):
            pick_store.add_pick(
                "Job_0703", channel="RA1", trace=float(i), sample=50.0, time_ns=5.0, depth_m=0.2,
                velocity_m_per_ns=0.1, velocity_source="assumed", dielectric=9.0, note="new " + str(i),
            )
    """
    a, b = _run(deleter, root), _run(adder, root)
    assert a.wait(timeout=120) == 0
    assert b.wait(timeout=120) == 0

    stored = pick_store.load_picks("Job_0703")
    remaining_seed = [p for p in stored if p.note == "to be deleted"]
    added = [p for p in stored if p.note.startswith("new ")]
    assert remaining_seed == [], f"delete lost to a concurrent add: {len(remaining_seed)} still present"
    assert len(added) == 15, f"add lost to a concurrent delete: only {len(added)}/15 present"


def test_nesting_the_lock_for_one_job_in_one_thread_does_not_deadlock(tmp_path: Path) -> None:
    """flock belongs to an open file description: a nested entry that re-opened the lock file
    used to wait forever on the lock its own thread held. Run in a thread so a regression fails
    this test instead of hanging the suite."""
    import threading

    entered = threading.Event()

    def nested() -> None:
        with annotation_io.job_lock(tmp_path), annotation_io.job_lock(tmp_path):
            entered.set()

    worker = threading.Thread(target=nested, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert entered.is_set(), "nested job_lock on the same job deadlocked"


def test_the_os_lock_is_released_after_a_nested_exit(root: Path) -> None:
    """After nested entries unwind, another process must be able to take the lock."""
    job = root / "Job_X"
    with annotation_io.job_lock(job), annotation_io.job_lock(job):
        pass
    proc = _run(f"""
        with annotation_io.job_lock(Path({str(job)!r})):
            pass
    """, root)
    assert proc.wait(timeout=10) == 0
