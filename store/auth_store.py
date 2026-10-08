"""Postgres storage for GPR Studio logins: accounts, sessions and the sign-in log.

Only storage lives here. Hashing, lock-out and cookies are studio/auth.py's; this module never
sees a password, only its Argon2 hash, and never a session token, only its SHA-256.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from store.database import open_pool


@dataclass(frozen=True)
class Account:
    user_id: int
    username: str
    password_hash: str
    disabled: bool


@dataclass(frozen=True)
class LoginEvent:
    at: datetime
    username: str
    event: str
    ip: str | None
    user_agent: str | None


class AuthStore:
    def __init__(self, url: str) -> None:
        self._pool: ConnectionPool = open_pool(url, max_size=5)

    def close(self) -> None:
        self._pool.close()

    def _rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    # ---- accounts

    def create_account(self, username: str, password_hash: str) -> int:
        (row,) = self._rows("INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING user_id",
                            (username, password_hash))
        return int(row["user_id"])

    def account(self, username: str) -> Account | None:
        rows = self._rows("SELECT user_id, username, password_hash, disabled_at IS NOT NULL AS disabled "
                          "FROM users WHERE lower(username) = lower(%s)", (username,))
        return Account(**rows[0]) if rows else None

    def accounts(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT u.username, u.created_at, u.disabled_at,
                   (SELECT max(at) FROM login_events e WHERE e.user_id = u.user_id AND e.event = 'login') AS last_login
            FROM users u ORDER BY lower(u.username)
            """, ())

    def has_accounts(self) -> bool:
        return bool(self._rows("SELECT EXISTS (SELECT 1 FROM users) AS any", ())[0]["any"])

    def set_password_hash(self, user_id: int, password_hash: str, *, changed: bool) -> None:
        """Store a new hash; `changed` is False for a same-password rehash to stronger parameters."""
        self._rows("UPDATE users SET password_hash = %s"
                   + (", password_changed_at = now()" if changed else "") + " WHERE user_id = %s",
                   (password_hash, user_id))

    def set_disabled(self, user_id: int, disabled: bool) -> None:
        self._rows("UPDATE users SET disabled_at = CASE WHEN %s THEN now() END WHERE user_id = %s",
                   (disabled, user_id))

    # ---- sign-in log

    def record(self, event: str, username: str, user_id: int | None, ip: str | None, user_agent: str | None) -> None:
        self._rows("INSERT INTO login_events (event, username, user_id, ip, user_agent) VALUES (%s, %s, %s, %s, %s)",
                   (event, username[:200], user_id, ip, (user_agent or "")[:400] or None))

    def failures_since(self, *, username: str, ip: str | None, minutes: int) -> tuple[int, int]:
        """Failed attempts in the window: (for this username, from this address)."""
        (row,) = self._rows(
            """
            SELECT count(*) FILTER (WHERE lower(username) = lower(%s)) AS by_name,
                   count(*) FILTER (WHERE ip = %s) AS by_ip
            FROM login_events
            WHERE event = 'login_failed' AND at > now() - make_interval(mins => %s)
            """, (username, ip, minutes))
        return int(row["by_name"]), int(row["by_ip"])

    def history(self, limit: int = 50, username: str | None = None) -> list[LoginEvent]:
        rows = self._rows(
            "SELECT at, username, event, ip, user_agent FROM login_events "
            "WHERE %s::text IS NULL OR lower(username) = lower(%s) ORDER BY at DESC, event_id DESC LIMIT %s",
            (username, username, limit))
        return [LoginEvent(**r) for r in rows]

    # ---- sessions

    def create_session(self, token_sha256: bytes, user_id: int, hours: float, ip: str | None,
                       user_agent: str | None) -> None:
        self._rows("INSERT INTO sessions (token_sha256, user_id, expires_at, ip, user_agent) "
                   "VALUES (%s, %s, now() + make_interval(secs => %s), %s, %s)",
                   (token_sha256, user_id, hours * 3600, ip, (user_agent or "")[:400] or None))

    def session_account(self, token_sha256: bytes) -> tuple[int, str] | None:
        """(user_id, username) for a live session of an enabled account; touches last_seen_at."""
        rows = self._rows(
            """
            UPDATE sessions s SET last_seen_at = now()
            FROM users u
            WHERE s.token_sha256 = %s AND u.user_id = s.user_id
              AND s.revoked_at IS NULL AND s.expires_at > now() AND u.disabled_at IS NULL
            RETURNING u.user_id, u.username
            """, (token_sha256,))
        return (int(rows[0]["user_id"]), rows[0]["username"]) if rows else None

    def revoke_session(self, token_sha256: bytes) -> tuple[int, str] | None:
        rows = self._rows(
            "UPDATE sessions s SET revoked_at = now() FROM users u WHERE s.token_sha256 = %s "
            "AND u.user_id = s.user_id AND s.revoked_at IS NULL RETURNING u.user_id, u.username", (token_sha256,))
        return (int(rows[0]["user_id"]), rows[0]["username"]) if rows else None

    def revoke_all_sessions(self, user_id: int) -> None:
        self._rows("UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
