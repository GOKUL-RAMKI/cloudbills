"""Budget CRUD, utilisation and alert endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from db import get_conn
from routes.utils import actor_id, decimal_value, int_arg, json_body, required

bp = Blueprint("budgets", __name__)


def _budget(conn, budget_id: int):
    return conn.execute(
        "SELECT * FROM v_budget_utilization WHERE budget_id = %s", (budget_id,)
    ).fetchone()


@bp.get("/budgets")
def list_budgets():
    month = request.args.get("month", "").strip() or None
    if month and len(month) == 7:
        month = f"{month}-01"
    with get_conn() as conn:
        if month:
            rows = conn.execute(
                """
                SELECT * FROM v_budget_utilization
                WHERE budget_month = DATE_TRUNC('month', %s::date)::date
                ORDER BY budget_month DESC, budget_id
                """,
                (month,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM v_budget_utilization ORDER BY budget_month DESC, budget_id"
            ).fetchall()
    return jsonify(items=rows)


@bp.post("/budgets")
def create_budget():
    data = json_body()
    required(data, "budget_month", "budget_amount")
    project_id = data.get("project_id")
    team_id = data.get("team_id")
    project_id = int(project_id) if project_id not in (None, "") else None
    team_id = int(team_id) if team_id not in (None, "") else None
    if (project_id is None) == (team_id is None):
        return jsonify(error="Provide exactly one of project_id or team_id"), 400

    warning = data.get("warning_percentage", 80)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT create_budget(%s::bigint, %s::bigint, %s::date, %s::numeric, %s::numeric, %s::bigint) AS budget_id",
            (
                project_id,
                team_id,
                data["budget_month"],
                decimal_value(data, "budget_amount"),
                decimal_value({"warning_percentage": warning}, "warning_percentage"),
                actor_id(data),
            ),
        ).fetchone()
        created = _budget(conn, row["budget_id"])
    return jsonify(budget=created), 201


@bp.put("/budgets/<int:budget_id>")
def update_budget(budget_id: int):
    data = json_body()
    required(data, "budget_amount", "warning_percentage")
    with get_conn() as conn:
        conn.execute(
            "CALL update_budget(%s::bigint, %s::numeric, %s::numeric, %s::bigint)",
            (
                budget_id,
                decimal_value(data, "budget_amount"),
                decimal_value(data, "warning_percentage"),
                actor_id(data),
            ),
        )
        updated = _budget(conn, budget_id)
    return jsonify(budget=updated)


@bp.delete("/budgets/<int:budget_id>")
def delete_budget(budget_id: int):
    data = request.get_json(silent=True) or {}
    with get_conn() as conn:
        conn.execute(
            "CALL delete_budget(%s::bigint, %s::bigint)",
            (budget_id, actor_id(data)),
        )
    return jsonify(status="deleted", budget_id=budget_id)


@bp.post("/budgets/<int:budget_id>/check")
def check_budget(budget_id: int):
    with get_conn() as conn:
        conn.execute("SELECT check_budget_threshold(%s::bigint)", (budget_id,))
        updated = _budget(conn, budget_id)
    return jsonify(budget=updated)


@bp.get("/budgets/alerts")
def list_alerts():
    acknowledged = request.args.get("acknowledged")
    clauses = []
    params: list = []
    if acknowledged in ("true", "false"):
        clauses.append("ba.is_acknowledged = %s")
        params.append(acknowledged == "true")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT ba.*, b.project_id, p.project_name, b.team_id, t.team_name,
                   b.budget_month, b.warning_percentage,
                   ROUND(ba.current_spend / NULLIF(ba.budget_amount, 0) * 100, 2) AS utilization_percentage
            FROM budget_alerts ba
            JOIN budgets b ON ba.budget_id = b.budget_id
            LEFT JOIN projects p ON b.project_id = p.project_id
            LEFT JOIN teams t ON b.team_id = t.team_id
            {where}
            ORDER BY ba.is_acknowledged, ba.triggered_at DESC
            """,
            params,
        ).fetchall()
    return jsonify(items=rows)


@bp.post("/budgets/alerts/<int:alert_id>/ack")
def acknowledge_alert(alert_id: int):
    data = request.get_json(silent=True) or {}
    with get_conn() as conn:
        conn.execute(
            "CALL acknowledge_budget_alert(%s::bigint, %s::bigint)",
            (alert_id, actor_id(data)),
        )
    return jsonify(status="acknowledged", alert_id=alert_id)
