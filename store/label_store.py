"""Postgres storage for verified apex labels (studio/verify.py writes them, training reads them)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from store.database import open_pool


@dataclass(frozen=True)
class ApexLabel:
    job: str
    trace: int
    time_ns: float
    decision: str
    username: str
    decided_at: datetime


class LabelStore:
    def __init__(self, url: str) -> None:
        self._pool: ConnectionPool = open_pool(url, max_size=3)

    def close(self) -> None:
        self._pool.close()

    def _rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def record(self, job: str, trace: int, time_ns: float, decision: str, user_id: int) -> None:
        self._rows("INSERT INTO apex_labels (job, trace, time_ns, decision, user_id) VALUES (%s, %s, %s, %s, %s)",
                   (job, trace, time_ns, decision, user_id))

    def latest(self, jobs: list[str] | None = None) -> list[ApexLabel]:
        """The current decision on every labelled target, optionally for some lines only."""
        rows = self._rows(
            """
            SELECT DISTINCT ON (l.job, l.trace, l.time_ns)
                   l.job, l.trace, l.time_ns, l.decision, u.username, l.decided_at
            FROM apex_labels l JOIN users u ON u.user_id = l.user_id
            WHERE %s::text[] IS NULL OR l.job = ANY(%s::text[])
            ORDER BY l.job, l.trace, l.time_ns, l.decided_at DESC, l.label_id DESC
            """, (jobs, jobs))
        return [ApexLabel(**r) for r in rows]
