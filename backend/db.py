"""Database access helpers for clouDBills.

Tier 2 never holds business truth: it only borrows pooled connections and
invokes stored programs in PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://cloudbills:cloudbills@localhost:5432/cloudbills",
)

POOL = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=10,
    kwargs={"row_factory": dict_row},
    open=False,
    timeout=15,
)


def open_pool() -> None:
    if POOL.closed:
        POOL.open()
        POOL.wait(timeout=10)


def close_pool() -> None:
    if not POOL.closed:
        POOL.close()


def get_conn():
    """Return a pooled connection context manager.

    Exiting the context commits on success and rolls back on exception.
    """
    return POOL.connection()
