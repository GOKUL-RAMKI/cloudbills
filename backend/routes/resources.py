"""Inventory CRUD endpoints. Every mutation is a stored program call."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from psycopg.types.json import Jsonb

from db import get_conn
from routes.utils import actor_id, decimal_value, int_arg, json_body, required

bp = Blueprint("resources", __name__)


def _nullable_int(value):
    if value in (None, ""):
        return None
    return int(value)


def _tags_json(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    cleaned = {
        str(key).strip(): str(val)
        for key, val in value.items()
        if str(key).strip() and val is not None
    }
    return Jsonb(cleaned)


@bp.get("/resources")
def list_resources():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    project_id = int_arg("project_id")
    account_id = int_arg("account_id")
    category = request.args.get("category", "").strip()
    limit = int_arg("limit", default=100, minimum=1, maximum=500)
    offset = int_arg("offset", default=0, minimum=0)

    clauses: list[str] = []
    params: list = []
    if search:
        clauses.append("(resource_name ILIKE %s OR external_resource_id ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    if status:
        clauses.append("status = %s")
        params.append(status)
    if project_id is not None:
        clauses.append("project_id = %s")
        params.append(project_id)
    if account_id is not None:
        clauses.append("account_id = %s")
        params.append(account_id)
    if category:
        clauses.append("generic_category = %s")
        params.append(category)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS total FROM v_resource_inventory {where}", params
        ).fetchone()["total"]
        items = conn.execute(
            f"""
            SELECT * FROM v_resource_inventory
            {where}
            ORDER BY resource_id
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        ).fetchall()

    return jsonify(items=items, total=total, limit=limit, offset=offset)


@bp.get("/resources/<int:resource_id>")
def get_resource(resource_id: int):
    with get_conn() as conn:
        resource = conn.execute(
            "SELECT * FROM v_resource_inventory WHERE resource_id = %s", (resource_id,)
        ).fetchone()
        if not resource:
            return jsonify(error="Resource not found"), 404

        history = conn.execute(
            """
            SELECT * FROM resource_status_history
            WHERE resource_id = %s
            ORDER BY transition_time DESC
            """,
            (resource_id,),
        ).fetchall()
        dependencies = conn.execute(
            """
            SELECT 'parent' AS direction, rd.dependency_type,
                   p.resource_id, p.resource_name, p.status
            FROM resource_dependencies rd
            JOIN resources p ON rd.parent_resource_id = p.resource_id
            WHERE rd.child_resource_id = %s
            UNION ALL
            SELECT 'child' AS direction, rd.dependency_type,
                   c.resource_id, c.resource_name, c.status
            FROM resource_dependencies rd
            JOIN resources c ON rd.child_resource_id = c.resource_id
            WHERE rd.parent_resource_id = %s
            """,
            (resource_id, resource_id),
        ).fetchall()
        recommendations = conn.execute(
            """
            SELECT recommendation_id, recommendation_type, status,
                   estimated_monthly_saving, detected_at
            FROM recommendations
            WHERE resource_id = %s
            ORDER BY detected_at DESC
            """,
            (resource_id,),
        ).fetchall()

    return jsonify(
        resource=resource,
        status_history=history,
        dependencies=dependencies,
        recommendations=recommendations,
    )


@bp.post("/resources")
def create_resource():
    data = json_body()
    required(data, "account_id", "type_id", "region_id", "external_resource_id", "resource_name")
    tags = _tags_json(data.get("tags") or {})
    params = (
        int(data["account_id"]),
        _nullable_int(data.get("project_id")),
        int(data["type_id"]),
        int(data["region_id"]),
        str(data["external_resource_id"]).strip(),
        str(data["resource_name"]).strip(),
        data.get("status") or "active",
        decimal_value(data, "hourly_cost", required_field=False) or 0,
        tags,
        actor_id(data),
    )
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT add_resource(
                %s::bigint, %s::bigint, %s::int, %s::int, %s, %s,
                %s, %s::numeric, %s::jsonb, %s::bigint
            ) AS resource_id
            """,
            params,
        ).fetchone()
        created = conn.execute(
            "SELECT * FROM v_resource_inventory WHERE resource_id = %s",
            (row["resource_id"],),
        ).fetchone()
    return jsonify(resource=created), 201


@bp.put("/resources/<int:resource_id>")
def update_resource(resource_id: int):
    data = json_body()
    required(data, "type_id", "region_id", "resource_name", "status")
    tags = _tags_json(data.get("tags")) if "tags" in data else None
    params = (
        resource_id,
        _nullable_int(data.get("project_id")),
        int(data["type_id"]),
        int(data["region_id"]),
        str(data["resource_name"]).strip(),
        data["status"],
        decimal_value(data, "hourly_cost", required_field=False) or 0,
        tags,
        actor_id(data),
    )
    with get_conn() as conn:
        conn.execute(
            """
            CALL update_resource(
                %s::bigint, %s::bigint, %s::int, %s::int, %s,
                %s, %s::numeric, %s::jsonb, %s::bigint
            )
            """,
            params,
        )
        updated = conn.execute(
            "SELECT * FROM v_resource_inventory WHERE resource_id = %s",
            (resource_id,),
        ).fetchone()
    return jsonify(resource=updated)


@bp.delete("/resources/<int:resource_id>")
def delete_resource(resource_id: int):
    data = request.get_json(silent=True) or {}
    with get_conn() as conn:
        conn.execute(
            "CALL delete_resource(%s::bigint, %s::bigint)",
            (resource_id, actor_id(data)),
        )
    return jsonify(status="deleted", resource_id=resource_id)


@bp.post("/resources/<int:resource_id>/tags")
def add_tag(resource_id: int):
    data = json_body()
    required(data, "key", "value")
    with get_conn() as conn:
        conn.execute(
            "CALL add_resource_tag(%s::bigint, %s, %s)",
            (resource_id, str(data["key"]).strip(), str(data["value"])),
        )
        tags = conn.execute(
            "SELECT tag_key, tag_value FROM resource_tags WHERE resource_id = %s ORDER BY tag_key",
            (resource_id,),
        ).fetchall()
    return jsonify(tags=tags), 201


@bp.delete("/resources/<int:resource_id>/tags/<key>")
def delete_tag(resource_id: int, key: str):
    with get_conn() as conn:
        conn.execute(
            "CALL delete_resource_tag(%s::bigint, %s)",
            (resource_id, key),
        )
    return jsonify(status="deleted", resource_id=resource_id, tag_key=key)


@bp.get("/dependencies/<int:resource_id>")
def dependencies(resource_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM dependency_chain(%s::bigint)", (resource_id,)
        ).fetchall()
    return jsonify(resource_id=resource_id, items=rows)
