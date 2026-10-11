"""Postgres connections and schema migrations, shared by every store in this package.

The connection URL is a secret (it carries the password), so it is read from the environment
(`DATABASE_URL`, set in the gitignored .env), never from config.yaml.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# Any constant shared by every process that migrates this database: two servers starting together
# must not both apply the same migration.
_MIGRATION_LOCK = 0x47505253  # "GPRS"


class DatabaseNotConfiguredError(RuntimeError):
    """DATABASE_URL is not set: a deployment fact, reported as such rather than guessed around."""


def database_url(variable: str = "DATABASE_URL") -> str:
    url = os.environ.get(variable, "").strip()
    if not url:
        raise DatabaseNotConfiguredError(
            f"{variable} is not set: start Postgres (docker compose up -d) and put its URL in .env")
    return url


def _migration_sort_key(path: Path) -> tuple[int, str]:
    # Numeric, not lexicographic: "1000_x.sql" sorts after "999_x.sql". Filenames start with a number.
    return (int(path.stem.split("_", 1)[0]), path.stem)


def migrate(pool: ConnectionPool) -> None:
    """Apply every pending migration, all in one transaction under an advisory lock.

    Postgres DDL is transactional, so a failure anywhere leaves the schema exactly as it was.
    """
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql"), key=_migration_sort_key):
            if path.stem in applied:
                continue
            conn.execute(path.read_text(encoding="utf-8"))  # no parameters: several statements are allowed
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            logger.info("store.migration_applied version=%s", path.stem)


def open_pool(url: str, *, max_size: int = 10) -> ConnectionPool:
    """A connection pool on a migrated database. Thread-safe: callers may use it from any thread."""
    # check: a connection that died with a Postgres restart is replaced before use, not handed out
    pool = ConnectionPool(url, min_size=1, max_size=max_size, open=True, check=ConnectionPool.check_connection)
    try:
        pool.wait(timeout=10)
        migrate(pool)
    except Exception:
        pool.close()
        raise
    return pool
