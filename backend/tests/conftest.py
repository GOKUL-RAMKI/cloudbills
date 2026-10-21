"""Shared pytest fixtures.

Every test runs inside one PostgreSQL transaction that is rolled back at the
end, so writes exercised through the API never leak into the demo database.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import db  # noqa: E402
from app import create_app  # noqa: E402
from routes import billing, budgets, dashboard, meta, recommendations, resources  # noqa: E402

ROUTE_MODULES = (meta, dashboard, resources, recommendations, billing, budgets)


@pytest.fixture(scope="session")
def flask_app():
    application = create_app()
    application.config.update(TESTING=True)
    yield application
    db.close_pool()


@pytest.fixture()
def connection():
    conn = psycopg.connect(db.DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def client(flask_app, connection, monkeypatch):
    @contextmanager
    def shared_connection():
        connection.execute("SAVEPOINT api_request")
        try:
            yield connection
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT api_request")
            connection.execute("RELEASE SAVEPOINT api_request")
            raise
        else:
            connection.execute("RELEASE SAVEPOINT api_request")

    for module in ROUTE_MODULES:
        monkeypatch.setattr(module, "get_conn", shared_connection)

    with flask_app.test_client() as test_client:
        yield test_client
