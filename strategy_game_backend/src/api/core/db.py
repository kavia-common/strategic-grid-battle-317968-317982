from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from src.api.core.config import get_settings


# PUBLIC_INTERFACE
@contextmanager
def get_db() -> Iterator[psycopg.Connection]:
    """Yield a PostgreSQL connection with dict-row results.

    Uses POSTGRES_URL from environment via settings.
    """
    settings = get_settings()
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_one(conn: psycopg.Connection, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_all(conn: psycopg.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def execute(conn: psycopg.Connection, query: str, params: tuple[Any, ...] = ()) -> None:
    with conn.cursor() as cur:
        cur.execute(query, params)
