"""clouDBills Flask application.

Presentation never touches PostgreSQL directly: every API route validates
shallow input, then delegates to a stored procedure or function.
"""

from __future__ import annotations

import atexit
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from flask import Flask, jsonify, send_from_directory
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException

from db import close_pool, open_pool
from routes import register_blueprints

BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


class ClouDBillsJSONProvider(DefaultJSONProvider):
    """Serialise PostgreSQL NUMERIC values as JSON numbers."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(FRONTEND_DIR),
        static_url_path="",
    )
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
    app.config["JSON_SORT_KEYS"] = False
    app.json = ClouDBillsJSONProvider(app)

    open_pool()
    register_blueprints(app)

    @app.get("/")
    def home():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.errorhandler(psycopg.Error)
    def handle_database_error(exc: psycopg.Error):
        state = getattr(exc, "sqlstate", None)
        message = str(exc).strip()
        status = 500
        if state == "23505":
            status = 409
        elif state in ("23503", "23514", "23502", "22P02", "22007", "22008", "22003", "22023"):
            status = 400
        elif state == "P0002":
            status = 404
        elif state == "P0001":
            status = 409
        elif state in ("40001", "40P01"):
            status = 409
        elif "Concurrency Conflict" in message:
            status = 409
        elif "not found" in message.lower():
            status = 404
        return jsonify(error=message, sqlstate=state, status=status), status

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        return jsonify(error=exc.description, status=exc.code), exc.code

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        app.logger.exception("Unhandled error")
        return jsonify(error=str(exc), status=500), 500

    @app.teardown_appcontext
    def _noop(_exc):
        return None

    return app


app = create_app()
atexit.register(close_pool)


def shutdown() -> None:
    close_pool()
