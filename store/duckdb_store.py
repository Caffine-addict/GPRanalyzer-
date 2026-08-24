"""DuckDB implementation of the Store interface. The only module allowed to `import duckdb`."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import duckdb

from core.contracts import Evidence, Finding, ScanFrame
from store.base import Store

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _fetch_scalar(cursor: duckdb.DuckDBPyConnection) -> Any:
    row = cursor.fetchone()
    assert row is not None  # nextval()/COUNT(*) always return exactly one row
    return row[0]


def _migration_sort_key(path: Path) -> tuple[int, str]:
    # Sort by the numeric prefix, not lexicographically — "1000_x.sql" must
    # sort after "999_x.sql", which plain string sort gets wrong. Convention:
    # migration filenames start with a numeric prefix (any width).
    prefix = path.stem.split("_", 1)[0]
    return (int(prefix), path.stem)


class DuckDBStore(Store):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self._path))
        self._migrate()

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version VARCHAR PRIMARY KEY, applied_at TIMESTAMP DEFAULT now())"
        )
        applied = {row[0] for row in self._conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for migration_path in sorted(_MIGRATIONS_DIR.glob("*.sql"), key=_migration_sort_key):
            version = migration_path.stem
            if version in applied:
                continue
            # DuckDB auto-commits each statement in a multi-statement
            # .execute() independently — without an explicit transaction, a
            # failure partway through a migration file would leave earlier
            # statements committed while schema_migrations never records the
            # version, causing a full (partial-)reapply on next startup.
            # Every migration file must still be internally idempotent
            # (IF NOT EXISTS, etc.) as a second line of defense.
            self._conn.execute("BEGIN TRANSACTION")
            try:
                self._conn.execute(migration_path.read_text(encoding="utf-8"))
                self._conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", [version])
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            logger.info("store.migration_applied version=%s", version)

    def save_frame(self, survey_id: str, line_id: str, frame: ScanFrame) -> int:
        frame_id = _fetch_scalar(self._conn.execute("SELECT nextval('frame_id_seq')"))
        self._conn.execute(
            """
            INSERT INTO frames (
                frame_id, survey_id, line_id, source_type, position, position_source,
                antenna_freq_mhz, sample_interval_ns, dielectric_assumed, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                frame_id,
                survey_id,
                line_id,
                frame.source_type,
                frame.position,
                frame.position_source,
                frame.antenna_freq_mhz,
                frame.sample_interval_ns,
                frame.dielectric_assumed,
                json.dumps(frame.provenance),
            ],
        )
        return int(frame_id)

    def save_finding(self, survey_id: str, line_id: str, frame_id: int, finding: Finding) -> int:
        finding_id = _fetch_scalar(self._conn.execute("SELECT nextval('finding_id_seq')"))
        ev = finding.evidence
        self._conn.execute(
            """
            INSERT INTO findings (
                finding_id, survey_id, line_id, frame_id,
                detection_class, detection_confidence,
                depth_m, depth_confidence, position_m, position_confidence,
                amplitude, amplitude_confidence, hyperbola_width_px, neighbours,
                risk_level, risk_score, risk_rules_fired,
                "what", "where", "why", "how", recommended_action, reasoning_latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                finding_id,
                survey_id,
                line_id,
                frame_id,
                ev.detection_class,
                ev.detection_confidence,
                ev.depth_m,
                ev.depth_confidence,
                ev.position_m,
                ev.position_confidence,
                ev.amplitude,
                ev.amplitude_confidence,
                ev.hyperbola_width_px,
                json.dumps(list(ev.neighbours)),
                finding.risk_level,
                finding.risk_score,
                json.dumps(list(finding.risk_rules_fired)),
                finding.what,
                finding.where,
                finding.why,
                finding.how,
                finding.recommended_action,
                finding.reasoning_latency_ms,
            ],
        )
        return int(finding_id)

    def update_finding_reasoning(self, finding_id: int, finding: Finding) -> None:
        self._conn.execute(
            """
            UPDATE findings SET
                "what" = ?, "where" = ?, "why" = ?, "how" = ?,
                recommended_action = ?, reasoning_latency_ms = ?, updated_at = now()
            WHERE finding_id = ?
            """,
            [
                finding.what,
                finding.where,
                finding.why,
                finding.how,
                finding.recommended_action,
                finding.reasoning_latency_ms,
                finding_id,
            ],
        )

    def get_findings_by_line(self, survey_id: str, line_id: str) -> list[Finding]:
        return self._query_findings(
            "SELECT * FROM findings WHERE survey_id = ? AND line_id = ? ORDER BY finding_id",
            [survey_id, line_id],
        )

    def get_findings_by_survey(self, survey_id: str) -> list[Finding]:
        return self._query_findings(
            "SELECT * FROM findings WHERE survey_id = ? ORDER BY finding_id",
            [survey_id],
        )

    def get_findings_for_line_id(self, line_id: str) -> list[Finding]:
        return self._query_findings(
            "SELECT * FROM findings WHERE line_id = ? ORDER BY finding_id",
            [line_id],
        )

    def _query_findings(self, sql: str, params: list[Any]) -> list[Finding]:
        self._conn.execute(sql, params)
        columns = [d[0] for d in self._conn.description]
        rows = self._conn.fetchall()

        findings: list[Finding] = []
        for row in rows:
            row_dict = dict(zip(columns, row, strict=True))
            try:
                findings.append(self._row_to_finding(row_dict))
            except ValueError as e:
                # One row failing Evidence's/Finding's own __post_init__
                # invariants (DB bitrot, a manual fix, a future bug
                # elsewhere) must not take down every other finding in this
                # query — same per-unit fault isolation as detect/model.py's
                # per-box skip and reason/engine.py's catch-all.
                logger.warning(
                    "store.skip_malformed_row finding_id=%s error=%s", row_dict.get("finding_id"), e
                )
        return findings

    def get_prior_passes(self, line_id: str, position: float, tolerance_m: float) -> list[dict[str, Any]]:
        self._conn.execute(
            """
            SELECT finding_id, survey_id, detection_class, risk_level, risk_score, position_m
            FROM findings
            WHERE line_id = ? AND position_m IS NOT NULL AND ABS(position_m - ?) <= ?
            ORDER BY finding_id
            """,
            [line_id, position, tolerance_m],
        )
        columns = [d[0] for d in self._conn.description]
        rows = self._conn.fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def get_survey_summary(self, survey_id: str) -> dict[str, Any]:
        total = _fetch_scalar(
            self._conn.execute("SELECT COUNT(*) FROM findings WHERE survey_id = ?", [survey_id])
        )
        by_risk = dict(
            self._conn.execute(
                "SELECT risk_level, COUNT(*) FROM findings WHERE survey_id = ? GROUP BY risk_level",
                [survey_id],
            ).fetchall()
        )
        by_class = dict(
            self._conn.execute(
                "SELECT detection_class, COUNT(*) FROM findings WHERE survey_id = ? GROUP BY detection_class",
                [survey_id],
            ).fetchall()
        )
        return {
            "survey_id": survey_id,
            "total_findings": int(total),
            "by_risk_level": {str(k): int(v) for k, v in by_risk.items()},
            "by_class": {str(k): int(v) for k, v in by_class.items()},
        }

    def close(self) -> None:
        self._conn.close()

    def _row_to_finding(self, row: dict[str, Any]) -> Finding:
        evidence = Evidence(
            detection_class=row["detection_class"],
            detection_confidence=row["detection_confidence"],
            depth_m=row["depth_m"],
            depth_confidence=row["depth_confidence"],
            position_m=row["position_m"],
            position_confidence=row["position_confidence"],
            amplitude=row["amplitude"],
            amplitude_confidence=row["amplitude_confidence"],
            hyperbola_width_px=row["hyperbola_width_px"],
            neighbours=tuple(json.loads(row["neighbours"])) if row["neighbours"] else (),
        )
        return Finding(
            evidence=evidence,
            risk_level=row["risk_level"],
            risk_score=row["risk_score"],
            risk_rules_fired=tuple(json.loads(row["risk_rules_fired"])) if row["risk_rules_fired"] else (),
            what=row["what"],
            where=row["where"],
            why=row["why"],
            how=row["how"],
            recommended_action=row["recommended_action"],
            reasoning_latency_ms=row["reasoning_latency_ms"],
        )
