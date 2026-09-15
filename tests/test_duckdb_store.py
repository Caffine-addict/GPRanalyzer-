"""Tests for store/duckdb_store.py against a real (temp-file) DuckDB database."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.contracts import Evidence, Finding, ScanFrame
from store.duckdb_store import DuckDBStore


@pytest.fixture
def store(tmp_path: Path) -> DuckDBStore:
    s = DuckDBStore(tmp_path / "test.duckdb")
    yield s
    s.close()


def _frame(**overrides: object) -> ScanFrame:
    base: dict[str, object] = {
        "source_type": "replay",
        "provenance": {"path": "001.jpg"},
        "image": np.zeros((10, 10), dtype=np.uint8),
        "position": 1.0,
        "position_source": "synthetic",
    }
    base.update(overrides)
    return ScanFrame(**base)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> Evidence:
    base: dict[str, object] = {
        "detection_class": "cavities",
        "detection_confidence": 0.8,
        "depth_m": 0.5,
        "depth_confidence": "estimated",
        "position_m": None,
        "position_confidence": "unavailable",
        "amplitude": None,
        "amplitude_confidence": "unavailable",
        "hyperbola_width_px": 10.0,
    }
    base.update(overrides)
    return Evidence(**base)  # type: ignore[arg-type]


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "evidence": _evidence(),
        "risk_level": "MEDIUM",
        "risk_score": 0.5,
        "risk_rules_fired": (),
    }
    base.update(overrides)
    return Finding(**base)  # type: ignore[arg-type]


def test_save_frame_returns_incrementing_ids(store: DuckDBStore) -> None:
    id1 = store.save_frame("s1", "l1", _frame())
    id2 = store.save_frame("s1", "l1", _frame())
    assert id2 > id1


def test_save_and_retrieve_finding_round_trips_evidence(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    finding = _finding(evidence=_evidence(neighbours=("elongated_linear_target", "cavities")))
    store.save_finding("s1", "l1", frame_id, finding)

    [retrieved] = store.get_findings_by_line("s1", "l1")
    assert retrieved.evidence.detection_class == "cavities"
    assert retrieved.evidence.depth_m == 0.5
    assert retrieved.evidence.depth_confidence == "estimated"
    assert retrieved.evidence.neighbours == ("elongated_linear_target", "cavities")
    assert retrieved.risk_level == "MEDIUM"
    assert retrieved.what is None  # no reasoning yet


def test_round_trip_does_not_swap_depth_and_amplitude(store: DuckDBStore) -> None:
    # A column-order bug in the INSERT (depth_m <-> amplitude) would go
    # undetected by the test above, since amplitude defaults to None there —
    # both fields need distinct, non-None values to catch a swap directly
    # rather than via an unrelated Evidence.__post_init__ crash.
    frame_id = store.save_frame("s1", "l1", _frame())
    finding = _finding(
        evidence=_evidence(
            depth_m=0.5,
            depth_confidence="calibrated",
            amplitude=0.91,
            amplitude_confidence="calibrated",
        )
    )
    store.save_finding("s1", "l1", frame_id, finding)

    [retrieved] = store.get_findings_by_line("s1", "l1")
    assert retrieved.evidence.depth_m == 0.5
    assert retrieved.evidence.amplitude == 0.91


def test_round_trip_does_not_swap_risk_score_and_detection_confidence(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    finding = _finding(
        risk_score=0.37,
        evidence=_evidence(detection_confidence=0.82),
    )
    store.save_finding("s1", "l1", frame_id, finding)

    [retrieved] = store.get_findings_by_line("s1", "l1")
    assert retrieved.risk_score == 0.37
    assert retrieved.evidence.detection_confidence == 0.82


def test_update_finding_reasoning_updates_fields(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    finding_id = store.save_finding("s1", "l1", frame_id, _finding())

    reasoned = _finding(
        what="a linear reflector",
        where="0.5m deep",
        why="consistent hyperbola",
        how="moderate confidence",
        recommended_action="confirm with second pass",
        reasoning_latency_ms=250.0,
    )
    store.update_finding_reasoning(finding_id, reasoned)

    [retrieved] = store.get_findings_by_line("s1", "l1")
    assert retrieved.what == "a linear reflector"
    assert retrieved.recommended_action == "confirm with second pass"
    assert retrieved.reasoning_latency_ms == 250.0


def test_get_findings_by_line_empty_when_none(store: DuckDBStore) -> None:
    assert store.get_findings_by_line("nonexistent", "nonexistent") == []


def test_get_findings_by_line_scoped_to_survey_and_line(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding())
    store.save_finding("s1", "l2", frame_id, _finding())  # different line
    store.save_finding("s2", "l1", frame_id, _finding())  # different survey

    assert len(store.get_findings_by_line("s1", "l1")) == 1
    assert len(store.get_findings_by_line("s1", "l2")) == 1
    assert len(store.get_findings_by_line("s2", "l1")) == 1


def test_get_findings_by_survey_spans_every_line(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding())
    store.save_finding("s1", "l2", frame_id, _finding())
    store.save_finding("s2", "l1", frame_id, _finding())  # different survey, excluded

    findings = store.get_findings_by_survey("s1")
    assert len(findings) == 2


def test_get_findings_by_survey_empty_when_none(store: DuckDBStore) -> None:
    assert store.get_findings_by_survey("nonexistent") == []


def test_get_findings_for_line_id_spans_every_survey(store: DuckDBStore) -> None:
    # line_id is a persistent physical-location identifier — a re-survey of
    # the same ground gets a new survey_id but the same line_id.
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("survey-day-1", "l1", frame_id, _finding())
    store.save_finding("survey-day-2", "l1", frame_id, _finding())
    store.save_finding("survey-day-1", "other-line", frame_id, _finding())  # different line, excluded

    findings = store.get_findings_for_line_id("l1")
    assert len(findings) == 2


def test_get_findings_for_line_id_empty_when_none(store: DuckDBStore) -> None:
    assert store.get_findings_for_line_id("nonexistent") == []


def test_get_findings_by_line_preserves_save_order(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    for conf in (0.1, 0.2, 0.3):
        store.save_finding("s1", "l1", frame_id, _finding(evidence=_evidence(detection_confidence=conf)))

    findings = store.get_findings_by_line("s1", "l1")
    assert [f.evidence.detection_confidence for f in findings] == [0.1, 0.2, 0.3]


def test_get_findings_by_survey_preserves_save_order(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    for conf in (0.1, 0.2, 0.3):
        store.save_finding("s1", "l2", frame_id, _finding(evidence=_evidence(detection_confidence=conf)))

    findings = store.get_findings_by_survey("s1")
    assert [f.evidence.detection_confidence for f in findings] == [0.1, 0.2, 0.3]


def test_get_findings_for_line_id_preserves_save_order(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    for survey_id, conf in (("survey-day-1", 0.1), ("survey-day-2", 0.2), ("survey-day-3", 0.3)):
        store.save_finding(survey_id, "l1", frame_id, _finding(evidence=_evidence(detection_confidence=conf)))

    findings = store.get_findings_for_line_id("l1")
    assert [f.evidence.detection_confidence for f in findings] == [0.1, 0.2, 0.3]


def test_get_findings_by_line_skips_one_malformed_row_without_losing_others(store: DuckDBStore) -> None:
    # A row violating Evidence's own invariants (unreachable via the normal
    # save_finding path, but possible from DB bitrot or a manual fix) must
    # not take down every other finding on the same line.
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding())  # healthy row

    store._conn.execute(
        """
        INSERT INTO findings (
            finding_id, survey_id, line_id, frame_id,
            detection_class, detection_confidence,
            depth_m, depth_confidence, position_m, position_confidence,
            amplitude, amplitude_confidence, hyperbola_width_px,
            risk_level, risk_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            9999,
            "s1",
            "l1",
            frame_id,
            "cavities",
            0.9,
            None,
            "calibrated",  # invalid: calibrated with no depth_m — violates Evidence.__post_init__
            None,
            "unavailable",
            None,
            "unavailable",
            10.0,
            "LOW",
            0.1,
        ],
    )

    findings = store.get_findings_by_line("s1", "l1")
    assert len(findings) == 1  # the malformed row was skipped, not raised


def test_get_findings_by_line_logs_skipped_malformed_row(
    store: DuckDBStore, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    frame_id = store.save_frame("s1", "l1", _frame())
    store._conn.execute(
        """
        INSERT INTO findings (
            finding_id, survey_id, line_id, frame_id,
            detection_class, detection_confidence,
            depth_m, depth_confidence, position_m, position_confidence,
            amplitude, amplitude_confidence, hyperbola_width_px,
            risk_level, risk_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [8888, "s1", "l1", frame_id, "cavities", 0.9, None, "calibrated", None, "unavailable", None, "unavailable", 10.0, "LOW", 0.1],
    )

    with caplog.at_level(logging.WARNING, logger="store.duckdb_store"):
        store.get_findings_by_line("s1", "l1")

    messages = [r.getMessage() for r in caplog.records]
    assert any("store.skip_malformed_row" in m and "8888" in m for m in messages)


def test_get_prior_passes_empty_when_none(store: DuckDBStore) -> None:
    assert store.get_prior_passes("l1", 5.0, 1.0) == []


def test_get_prior_passes_excludes_unavailable_position(store: DuckDBStore) -> None:
    # position_m None (position "unavailable") must never surface as a prior
    # pass — there's no real position to have matched on.
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding())  # default evidence has position_m=None
    assert store.get_prior_passes("l1", 1.0, 100.0) == []


def test_get_prior_passes_finds_within_tolerance(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    finding = _finding(
        evidence=_evidence(position_m=5.0, position_confidence="calibrated")
    )
    store.save_finding("survey-old", "l1", frame_id, finding)

    results = store.get_prior_passes("l1", position=5.2, tolerance_m=0.5)
    assert len(results) == 1
    assert results[0]["survey_id"] == "survey-old"


def test_get_prior_passes_excludes_outside_tolerance(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    finding = _finding(evidence=_evidence(position_m=5.0, position_confidence="calibrated"))
    store.save_finding("survey-old", "l1", frame_id, finding)

    assert store.get_prior_passes("l1", position=10.0, tolerance_m=0.5) == []


def test_get_prior_passes_tolerance_boundary_is_inclusive(store: DuckDBStore) -> None:
    # Distance exactly equals tolerance_m — the SQL uses <=, so this must
    # match. The only way to distinguish <= from < in the implementation.
    frame_id = store.save_frame("s1", "l1", _frame())
    finding = _finding(evidence=_evidence(position_m=5.0, position_confidence="calibrated"))
    store.save_finding("survey-old", "l1", frame_id, finding)

    results = store.get_prior_passes("l1", position=5.5, tolerance_m=0.5)
    assert len(results) == 1


def test_get_prior_passes_scoped_to_line(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    finding = _finding(evidence=_evidence(position_m=5.0, position_confidence="calibrated"))
    store.save_finding("survey-old", "other-line", frame_id, finding)

    assert store.get_prior_passes("l1", position=5.0, tolerance_m=0.5) == []


def test_get_survey_summary_counts_by_risk_and_class(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding(risk_level="LOW", evidence=_evidence(detection_class="cavities")))
    store.save_finding("s1", "l1", frame_id, _finding(risk_level="HIGH", evidence=_evidence(detection_class="cavities")))
    store.save_finding(
        "s1", "l1", frame_id, _finding(risk_level="HIGH", evidence=_evidence(detection_class="elongated_linear_target"))
    )

    summary = store.get_survey_summary("s1")
    assert summary["total_findings"] == 3
    assert summary["by_risk_level"] == {"LOW": 1, "HIGH": 2}
    assert summary["by_class"] == {"cavities": 2, "elongated_linear_target": 1}


def test_get_survey_summary_empty_survey(store: DuckDBStore) -> None:
    summary = store.get_survey_summary("nonexistent")
    assert summary["total_findings"] == 0
    assert summary["by_risk_level"] == {}
    assert summary["by_class"] == {}


def test_survey_exists_true_once_a_frame_is_saved(store: DuckDBStore) -> None:
    store.save_frame("s1", "l1", _frame())
    assert store.survey_exists("s1") is True


def test_survey_exists_false_for_an_unknown_survey_id(store: DuckDBStore) -> None:
    assert store.survey_exists("nonexistent") is False


def test_survey_exists_true_even_with_zero_findings(store: DuckDBStore) -> None:
    # get_survey_summary can't tell "never existed" from "existed, zero findings" — this can,
    # because save_frame runs for every frame regardless of whether it produces a finding.
    store.save_frame("s1", "l1", _frame())
    summary = store.get_survey_summary("s1")
    assert summary["total_findings"] == 0
    assert store.survey_exists("s1") is True


def test_migrations_are_idempotent_across_reconnects(tmp_path: Path) -> None:
    db_path = tmp_path / "reconnect.duckdb"
    store1 = DuckDBStore(db_path)
    frame_id = store1.save_frame("s1", "l1", _frame())
    store1.save_finding("s1", "l1", frame_id, _finding())
    store1.close()

    # Reopening must not fail (migration already applied) and must see prior data.
    store2 = DuckDBStore(db_path)
    findings = store2.get_findings_by_line("s1", "l1")
    assert len(findings) == 1
    store2.close()


def test_schema_migration_recorded(store: DuckDBStore) -> None:
    rows = store._conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert ("001_initial",) in rows


def test_migration_sort_key_orders_numerically_not_lexicographically() -> None:
    from store.duckdb_store import _migration_sort_key

    paths = [Path("999_x.sql"), Path("1000_y.sql"), Path("2_z.sql"), Path("001_initial.sql")]
    ordered = sorted(paths, key=_migration_sort_key)
    assert [p.stem for p in ordered] == ["001_initial", "2_z", "999_x", "1000_y"]


def test_failing_migration_rolls_back_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import store.duckdb_store as duckdb_store_module

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_initial.sql").write_text(
        "CREATE TABLE IF NOT EXISTS ok (id INTEGER);\n"
        "INSERT INTO ok VALUES (1);\n"
        "SELECT * FROM this_table_does_not_exist;\n",  # fails partway through
        encoding="utf-8",
    )
    monkeypatch.setattr(duckdb_store_module, "_MIGRATIONS_DIR", migrations_dir)

    with pytest.raises(duckdb_store_module.duckdb.Error):
        duckdb_store_module.DuckDBStore(tmp_path / "atomic_test.duckdb")

    # Reconnect and confirm the whole migration was rolled back atomically —
    # including the DDL, not just the INSERT: "ok" must not exist at all,
    # otherwise a retried deploy would hit "CREATE TABLE IF NOT EXISTS"
    # silently no-op while "INSERT INTO ok VALUES (1)" duplicated the row,
    # instead of cleanly failing again on the same still-broken statement.
    conn = duckdb_store_module.duckdb.connect(str(tmp_path / "atomic_test.duckdb"))
    applied = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert applied == []  # migration never recorded as applied
    with pytest.raises(duckdb_store_module.duckdb.Error, match="ok"):
        conn.execute("SELECT COUNT(*) FROM ok")
    conn.close()


# --- corroborating_channels persistence, and the alignment trap (2026-09-14) -


def test_corroborating_channels_survives_a_round_trip(store: DuckDBStore) -> None:
    # Without persisting this, a finding read back loses the strongest evidence it had and
    # evidence/quality.py silently regrades it from QL-B1 to QL-B2 — a downgrade caused purely by
    # a trip through storage.
    frame_id = store.save_frame("s1", "line_1", _frame())
    finding = _finding(evidence=_evidence(corroborating_channels=3))
    store.save_finding("s1", "line_1", frame_id, finding)

    [read_back] = store.get_findings_by_line("s1", "line_1")
    assert read_back.evidence.corroborating_channels == 3


def test_a_finding_saved_without_corroboration_reads_back_as_one(store: DuckDBStore) -> None:
    frame_id = store.save_frame("s1", "line_1", _frame())
    store.save_finding("s1", "line_1", frame_id, _finding())

    [read_back] = store.get_findings_by_line("s1", "line_1")
    assert read_back.evidence.corroborating_channels == 1


def test_the_insert_statement_columns_placeholders_and_values_all_agree() -> None:
    """Guards the specific mistake that adding this column caused.

    The column name and the bound value were both added correctly, in matching positions — but the
    VALUES clause kept 23 placeholders for 24 columns. DuckDB raised per frame, the orchestrator's
    per-frame exception guard swallowed it by design, the survey completed having emitted nothing,
    and a WebSocket test waited forever for a finding that would never arrive. A missing "?"
    presented as a hung test suite, not as an error.

    The next migration will face the same trap, so this counts the three lists rather than trusting
    that a human lined them up.
    """
    import inspect

    source = inspect.getsource(DuckDBStore.save_finding)
    statement = source[source.index("INSERT INTO findings") : source.index("return int(finding_id)")]

    column_block = statement[statement.index("(") + 1 : statement.index(") VALUES")]
    n_columns = len([c for c in column_block.replace("\n", " ").split(",") if c.strip()])
    n_placeholders = statement[statement.index(") VALUES") :].count("?")
    value_block = statement[statement.index("[") + 1 : statement.index("],")]
    n_values = len([v for v in value_block.split(",\n") if v.strip()])

    assert n_columns == n_placeholders == n_values, (
        f"INSERT is misaligned: {n_columns} columns, {n_placeholders} placeholders, {n_values} values"
    )
