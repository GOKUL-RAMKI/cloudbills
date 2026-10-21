"""Dashboard aggregate endpoint."""

from __future__ import annotations

from flask import Blueprint, jsonify

from db import get_conn

bp = Blueprint("dashboard", __name__)


@bp.get("/dashboard")
def dashboard():
    with get_conn() as conn:
        summary = conn.execute("SELECT get_dashboard_summary() AS summary").fetchone()["summary"]
        audit = conn.execute(
            """
            SELECT al.audit_id, al.action_type, al.entity_type, al.entity_id,
                   al.action_timestamp, u.username
            FROM audit_logs al
            LEFT JOIN users u ON al.user_id = u.user_id
            ORDER BY al.action_timestamp DESC
            LIMIT 8
            """
        ).fetchall()
        batches = conn.execute(
            """
            SELECT batch_id, file_name, status, row_count, accepted_count,
                   rejected_count, imported_at
            FROM billing_import_batches
            ORDER BY imported_at DESC
            LIMIT 5
            """
        ).fetchall()
    return jsonify(summary=summary, recent_audit=audit, recent_batches=batches)
