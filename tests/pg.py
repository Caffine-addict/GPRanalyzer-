"""Postgres for the test suite: every caller gets its own empty schema in the gpr_test database.

Only TEST_DATABASE_URL is read from .env (never DATABASE_URL), so a test can never touch real
logins or findings. Schemas are dropped when the session ends (tests/conftest.py).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg.conninfo import make_conninfo

from store.postgres_store import PostgresStore

_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
_schemas: list[str] = []
_stores: list[PostgresStore] = []


def _base_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(_ENV_FILE).get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set: start Postgres (docker compose up -d) and see .env.example")
    return url


def fresh_url() -> str:
    """A connection URL whose search_path is a new, empty schema."""
    base = _base_url()
    schema = f"t_{uuid.uuid4().hex[:16]}"
    try:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{schema}"')
    except psycopg.OperationalError as exc:
        pytest.fail(f"test Postgres unreachable ({exc}); start it with: docker compose up -d")
    _schemas.append(schema)
    return make_conninfo(base, options=f"-c search_path={schema}")


def fresh_store() -> PostgresStore:
    store = PostgresStore(fresh_url())
    _stores.append(store)
    return store


def drop_all() -> None:
    for store in _stores:
        store.close()  # closing twice is harmless; a test may already have closed it
    if not _schemas:
        return
    with psycopg.connect(_base_url(), autocommit=True) as conn:
        for schema in _schemas:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    _schemas.clear()
    _stores.clear()
