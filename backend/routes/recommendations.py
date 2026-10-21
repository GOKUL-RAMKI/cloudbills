"""Optimization workflow and detection endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from db import get_conn
from routes.utils import actor_id, decimal_value, int_arg, json_body, required

bp = Blueprint("recommendations", __name__)

DEFAULT_ACTIONS = {
    "idle_compute": "stop_instance",
    "rightsizing": "resize_instance",
    "unattached_volume": "delete_volume",
    "unattached_elastic_ip": "release_ip",
    "missing_tag": "stop_instance",
    "budget_overrun": "stop_instance",
}


def _queue_item(conn, recommendation_id: int):
    return conn.execute(
        "SELECT * FROM v_recommendation_queue WHERE recommendation_id = %s",
        (recommendation_id,),
    ).fetchone()


@bp.get("/recommendations")
def list_recommendations():
    status = request.args.get("status", "").strip()
    project_id = int_arg("project_id")
    search = request.args.get("search", "").strip()
    limit = int_arg("limit", default=100, minimum=1, maximum=500)
    offset = int_arg("offset", default=0, minimum=0)

    clauses: list[str] = []
    params: list = []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if project_id is not None:
        clauses.append("project_id = %s")
        params.append(project_id)
    if search:
        clauses.append("(resource_name ILIKE %s OR project_name ILIKE %s OR recommendation_type ILIKE %s)")
        params.extend([f"%{search}%"] * 3)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS total FROM v_recommendation_queue {where}", params
        ).fetchone()["total"]
        items = conn.execute(
            f"""
            SELECT * FROM v_recommendation_queue
            {where}
            ORDER BY detected_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        ).fetchall()
    return jsonify(items=items, total=total, limit=limit, offset=offset)


@bp.get("/recommendations/<int:recommendation_id>")
def get_recommendation(recommendation_id: int):
    with get_conn() as conn:
        item = _queue_item(conn, recommendation_id)
        if not item:
            return jsonify(error="Recommendation not found"), 404

        evidence = conn.execute(
            """
            SELECT e.*, mt.metric_name, mt.unit
            FROM recommendation_evidence e
            LEFT JOIN metric_types mt ON e.metric_type_id = mt.metric_type_id
            WHERE e.recommendation_id = %s
            ORDER BY e.observation_start DESC
            """,
            (recommendation_id,),
        ).fetchall()
        approvals = conn.execute(
            """
            SELECT a.*, req.username AS requested_by_name,
                   rev.username AS reviewed_by_name
            FROM approval_requests a
            LEFT JOIN users req ON a.requested_by = req.user_id
            LEFT JOIN users rev ON a.reviewed_by = rev.user_id
            WHERE a.recommendation_id = %s
            ORDER BY a.requested_at DESC
            """,
            (recommendation_id,),
        ).fetchall()
        history = conn.execute(
            """
            SELECT h.*, u.username AS changed_by_name
            FROM recommendation_status_history h
            LEFT JOIN users u ON h.changed_by = u.user_id
            WHERE h.recommendation_id = %s
            ORDER BY h.changed_at DESC
            """,
            (recommendation_id,),
        ).fetchall()
        actions = conn.execute(
            """
            SELECT act.*, u.username AS executed_by_name
            FROM recommendation_actions act
            LEFT JOIN users u ON act.executed_by = u.user_id
            WHERE act.recommendation_id = %s
            ORDER BY act.started_at DESC
            """,
            (recommendation_id,),
        ).fetchall()
        savings = conn.execute(
            "SELECT * FROM savings_realization WHERE recommendation_id = %s",
            (recommendation_id,),
        ).fetchone()

    return jsonify(
        recommendation=item,
        evidence=evidence,
        approvals=approvals,
        status_history=history,
        actions=actions,
        savings=savings,
    )


@bp.post("/recommendations/<int:recommendation_id>/request-review")
def request_review(recommendation_id: int):
    data = json_body()
    with get_conn() as conn:
        conn.execute(
            "CALL submit_recommendation_for_review(%s::bigint, %s::bigint, %s::text)",
            (recommendation_id, actor_id(data), data.get("comments")),
        )
        item = _queue_item(conn, recommendation_id)
    return jsonify(recommendation=item)


@bp.post("/recommendations/<int:recommendation_id>/approve")
def approve(recommendation_id: int):
    data = json_body()
    with get_conn() as conn:
        conn.execute(
            "CALL approve_optimization_recommendation(%s::bigint, %s::bigint, %s::text)",
            (recommendation_id, actor_id(data, key="reviewer_user_id"), data.get("comments")),
        )
        item = _queue_item(conn, recommendation_id)
    return jsonify(recommendation=item)


@bp.post("/recommendations/<int:recommendation_id>/reject")
def reject(recommendation_id: int):
    data = json_body()
    with get_conn() as conn:
        conn.execute(
            "CALL reject_optimization_recommendation(%s::bigint, %s::bigint, %s::text)",
            (recommendation_id, actor_id(data, key="reviewer_user_id"), data.get("comments")),
        )
        item = _queue_item(conn, recommendation_id)
    return jsonify(recommendation=item)


@bp.post("/recommendations/<int:recommendation_id>/execute")
def execute(recommendation_id: int):
    data = json_body()
    with get_conn() as conn:
        item = _queue_item(conn, recommendation_id)
        if not item:
            return jsonify(error="Recommendation not found"), 404
        action_type = data.get("action_type") or DEFAULT_ACTIONS.get(
            item["recommendation_type"], "stop_instance"
        )
        conn.execute(
            "CALL execute_recommendation_action(%s::bigint, %s::bigint, %s::text)",
            (recommendation_id, actor_id(data), action_type),
        )
        updated = _queue_item(conn, recommendation_id)
    return jsonify(recommendation=updated, action_type=action_type)


@bp.post("/recommendations/<int:recommendation_id>/savings")
def record_savings(recommendation_id: int):
    data = json_body()
    required(data, "baseline_monthly_cost", "actual_monthly_cost",
             "measurement_start", "measurement_end")
    with get_conn() as conn:
        conn.execute(
            "CALL record_savings(%s::bigint, %s::numeric, %s::numeric, %s::date, %s::date, %s::bigint)",
            (
                recommendation_id,
                decimal_value(data, "baseline_monthly_cost"),
                decimal_value(data, "actual_monthly_cost"),
                data["measurement_start"],
                data["measurement_end"],
                actor_id(data),
            ),
        )
        item = _queue_item(conn, recommendation_id)
        savings = conn.execute(
            "SELECT * FROM savings_realization WHERE recommendation_id = %s",
            (recommendation_id,),
        ).fetchone()
    return jsonify(recommendation=item, savings=savings)


@bp.get("/detections/idle")
def idle():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM detect_idle_compute()").fetchall()
    return jsonify(items=rows, total=len(rows))


@bp.get("/detections/orphaned-volumes")
def orphaned():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM orphaned_volumes()").fetchall()
    return jsonify(items=rows, total=len(rows))


@bp.get("/detections/missing-tags")
def tags():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM missing_tags()").fetchall()
    return jsonify(items=rows, total=len(rows))
