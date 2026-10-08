"""Postgres implementation of the Store interface. The only module with SQL for frames and findings."""

from __future__ import annotations

import logging
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from core.contracts import Evidence, Finding, ScanFrame, TraceBox
from store.base import Store
from store.database import open_pool

logger = logging.getLogger(__name__)


class PostgresStore(Store):
    """Thread-safe: every call borrows its own pooled connection and commits on return."""

    def __init__(self, url: str) -> None:
        self._pool: ConnectionPool = open_pool(url)

    def _one(self, sql: str, params: tuple[Any, ...]) -> Any:
        with self._pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
        assert row is not None  # RETURNING / COUNT(*) always give exactly one row
        return row[0]

    def _rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(sql, params).fetchall()

    def save_frame(self, survey_id: str, line_id: str, frame: ScanFrame) -> int:
        return int(self._one(
            """
            INSERT INTO frames (
                survey_id, line_id, source_type, position, position_source,
                antenna_freq_mhz, sample_interval_ns, dielectric_assumed, provenance
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING frame_id
            """,
            (survey_id, line_id, frame.source_type, frame.position, frame.position_source,
             frame.antenna_freq_mhz, frame.sample_interval_ns, frame.dielectric_assumed, Jsonb(frame.provenance)),
        ))

    def save_finding(self, survey_id: str, line_id: str, frame_id: int, finding: Finding) -> int:
        ev = finding.evidence
        loc = finding.location
        return int(self._one(
            """
            INSERT INTO findings (
                survey_id, line_id, frame_id,
                detection_class, detection_confidence,
                depth_m, depth_confidence, position_m, position_confidence,
                amplitude, amplitude_confidence, hyperbola_width_px, neighbours,
                corroborating_channels,
                risk_level, risk_score, risk_rules_fired,
                "what", "where", "why", "how", recommended_action, reasoning_latency_ms,
                location_trace_start, location_trace_end, location_sample_start, location_sample_end
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s)
            RETURNING finding_id
            """,
            (survey_id, line_id, frame_id,
             ev.detection_class, ev.detection_confidence,
             ev.depth_m, ev.depth_confidence, ev.position_m, ev.position_confidence,
             ev.amplitude, ev.amplitude_confidence, ev.hyperbola_width_px, Jsonb(list(ev.neighbours)),
             ev.corroborating_channels,
             finding.risk_level, finding.risk_score, Jsonb(list(finding.risk_rules_fired)),
             finding.what, finding.where, finding.why, finding.how,
             finding.recommended_action, finding.reasoning_latency_ms,
             None if loc is None else loc.trace_start, None if loc is None else loc.trace_end,
             None if loc is None else loc.sample_start, None if loc is None else loc.sample_end),
        ))

    def update_finding_reasoning(self, finding_id: int, finding: Finding) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE findings SET
                    "what" = %s, "where" = %s, "why" = %s, "how" = %s,
                    recommended_action = %s, reasoning_latency_ms = %s, updated_at = now()
                WHERE finding_id = %s
                """,
                (finding.what, finding.where, finding.why, finding.how,
                 finding.recommended_action, finding.reasoning_latency_ms, finding_id),
            )

    def get_findings_by_line(self, survey_id: str, line_id: str) -> list[Finding]:
        return self._query_findings(
            "SELECT * FROM findings WHERE survey_id = %s AND line_id = %s ORDER BY finding_id", (survey_id, line_id))

    def get_findings_by_survey(self, survey_id: str) -> list[Finding]:
        return self._query_findings("SELECT * FROM findings WHERE survey_id = %s ORDER BY finding_id", (survey_id,))

    def get_findings_for_line_id(self, line_id: str) -> list[Finding]:
        return self._query_findings("SELECT * FROM findings WHERE line_id = %s ORDER BY finding_id", (line_id,))

    def _query_findings(self, sql: str, params: tuple[Any, ...]) -> list[Finding]:
        findings: list[Finding] = []
        for row in self._rows(sql, params):
            try:
                findings.append(_row_to_finding(row))
            except ValueError as e:
                # One row failing Evidence's/Finding's own invariants (bitrot, a manual fix) must not
                # take down every other finding in the query: per-row fault isolation.
                logger.warning("store.skip_malformed_row finding_id=%s error=%s", row.get("finding_id"), e)
        return findings

    def get_prior_passes(self, line_id: str, position: float, tolerance_m: float) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT finding_id, survey_id, detection_class, risk_level, risk_score, position_m
            FROM findings
            WHERE line_id = %s AND position_m IS NOT NULL AND ABS(position_m - %s) <= %s
            ORDER BY finding_id
            """,
            (line_id, position, tolerance_m),
        )

    def get_survey_summary(self, survey_id: str) -> dict[str, Any]:
        total = self._one("SELECT COUNT(*) FROM findings WHERE survey_id = %s", (survey_id,))
        by_risk = self._rows("SELECT risk_level AS k, COUNT(*) AS n FROM findings WHERE survey_id = %s "
                             "GROUP BY risk_level", (survey_id,))
        by_class = self._rows("SELECT detection_class AS k, COUNT(*) AS n FROM findings WHERE survey_id = %s "
                              "GROUP BY detection_class", (survey_id,))
        return {
            "survey_id": survey_id,
            "total_findings": int(total),
            "by_risk_level": {str(r["k"]): int(r["n"]) for r in by_risk},
            "by_class": {str(r["k"]): int(r["n"]) for r in by_class},
        }

    def survey_exists(self, survey_id: str) -> bool:
        return bool(self._one("SELECT EXISTS (SELECT 1 FROM frames WHERE survey_id = %s)", (survey_id,)))

    def close(self) -> None:
        self._pool.close()


def _row_to_finding(row: dict[str, Any]) -> Finding:
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
        neighbours=tuple(row["neighbours"]),
        corroborating_channels=row["corroborating_channels"],
    )
    return Finding(
        evidence=evidence,
        risk_level=row["risk_level"],
        risk_score=row["risk_score"],
        risk_rules_fired=tuple(row["risk_rules_fired"]),
        what=row["what"],
        where=row["where"],
        why=row["why"],
        how=row["how"],
        recommended_action=row["recommended_action"],
        reasoning_latency_ms=row["reasoning_latency_ms"],
        location=None
        if row["location_trace_start"] is None
        else TraceBox(row["location_trace_start"], row["location_trace_end"],
                      row["location_sample_start"], row["location_sample_end"]),
    )
