"""Billing facts, CSV ELT ingestion and import-batch endpoints."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date

from flask import Blueprint, jsonify, request

from db import get_conn
from routes.utils import actor_id, int_arg

bp = Blueprint("billing", __name__)

STAGING_COLUMNS = (
    "provider_name",
    "external_account_id",
    "external_resource_id",
    "service_name",
    "usage_start_text",
    "usage_end_text",
    "usage_quantity_text",
    "billed_cost_text",
    "currency_code_text",
)


def _month_bounds(month: str | None):
    if not month:
        return None, None
    try:
        year, mon = (int(part) for part in month.split("-", 1))
        start = date(year, mon, 1)
    except (ValueError, TypeError):
        return None, None
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start, end


@bp.get("/billing")
def list_billing():
    project_id = int_arg("project_id")
    account_id = int_arg("account_id")
    month = request.args.get("month", "").strip() or None
    start, end = _month_bounds(month)
    limit = int_arg("limit", default=100, minimum=1, maximum=1000)
    offset = int_arg("offset", default=0, minimum=0)

    clauses: list[str] = []
    params: list = []
    if project_id is not None:
        clauses.append("b.project_id = %s")
        params.append(project_id)
    if account_id is not None:
        clauses.append("b.account_id = %s")
        params.append(account_id)
    if start:
        clauses.append("b.usage_start >= %s AND b.usage_start < %s")
        params.extend([start, end])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_conn() as conn:
        summary = conn.execute(
            f"""
            SELECT COUNT(*) AS rows, COALESCE(SUM(b.billed_cost), 0) AS total_cost
            FROM billing_records b
            {where}
            """,
            params,
        ).fetchone()
        items = conn.execute(
            f"""
            SELECT b.billing_id, b.usage_start, b.usage_end, b.service_name,
                   b.usage_quantity, b.billed_cost, b.currency_code,
                   b.account_id, a.account_name, cp.provider_name,
                   b.resource_id, r.resource_name,
                   b.project_id, p.project_name,
                   b.source_batch_id
            FROM billing_records b
            JOIN cloud_accounts a ON b.account_id = a.account_id
            JOIN cloud_providers cp ON a.provider_id = cp.provider_id
            LEFT JOIN resources r ON b.resource_id = r.resource_id
            LEFT JOIN projects p ON b.project_id = p.project_id
            {where}
            ORDER BY b.usage_start DESC, b.billing_id DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        ).fetchall()

    return jsonify(
        items=items,
        total=summary["rows"],
        total_cost=summary["total_cost"],
        limit=limit,
        offset=offset,
    )


@bp.get("/billing/monthly")
def monthly():
    month = request.args.get("month", "").strip() or None
    start, _ = _month_bounds(month)
    with get_conn() as conn:
        if start:
            rows = conn.execute(
                """
                SELECT * FROM mv_monthly_project_cost
                WHERE billing_month = %s
                ORDER BY total_aggregate_cost DESC
                """,
                (start,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM mv_monthly_project_cost
                ORDER BY billing_month DESC, total_aggregate_cost DESC
                """
            ).fetchall()
    return jsonify(items=rows)


@bp.get("/billing/shared-allocation")
def shared_allocation():
    month = request.args.get("month", "").strip() or None
    start, _ = _month_bounds(month)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM shared_cost_allocation(COALESCE(%s::date, CURRENT_DATE))",
            (start,),
        ).fetchall()
    return jsonify(items=rows)


@bp.post("/batches/upload")
def upload_batch():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="A CSV file is required"), 400

    raw = upload.read()
    if len(raw) > 10 * 1024 * 1024:
        return jsonify(error="CSV file is larger than 10 MB"), 400

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(error="CSV must be UTF-8 encoded"), 400

    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if rows and rows[0] and rows[0][0].strip().lower() in ("provider_name", "provider"):
        rows = rows[1:]
    if not rows:
        return jsonify(error="CSV contains no data rows"), 400

    normalized: list[list[str]] = []
    for row in rows:
        padded = (row + [""] * len(STAGING_COLUMNS))[: len(STAGING_COLUMNS)]
        normalized.append([cell.strip() for cell in padded])

    user_id = actor_id(
        {"user_id": request.form.get("user_id") or 1},
        key="user_id",
    )

    with get_conn() as conn:
        account_id = request.form.get("account_id")
        if account_id in (None, ""):
            first = normalized[0]
            account = conn.execute(
                """
                SELECT a.account_id
                FROM cloud_accounts a
                JOIN cloud_providers p ON a.provider_id = p.provider_id
                WHERE LOWER(p.provider_name) = LOWER(%s)
                  AND a.external_account_id = %s
                """,
                (first[0], first[1]),
            ).fetchone()
            if not account:
                return jsonify(
                    error="Could not infer cloud account; provide account_id or use a valid provider/account row"
                ), 400
            account_id = account["account_id"]
        else:
            try:
                account_id = int(account_id)
            except ValueError:
                return jsonify(error="account_id must be an integer"), 400

        checksum = hashlib.sha256(raw).hexdigest()
        batch = conn.execute(
            """
            INSERT INTO billing_import_batches
                (account_id, imported_by, file_name, file_checksum, row_count, status)
            VALUES (%s, %s, %s, %s, %s, 'uploaded')
            RETURNING batch_id
            """,
            (account_id, user_id, upload.filename, checksum, len(normalized)),
        ).fetchone()
        batch_id = batch["batch_id"]

        with conn.cursor() as cur:
            with cur.copy(
                """
                COPY billing_staging (
                    batch_id, provider_name, external_account_id, external_resource_id,
                    service_name, usage_start_text, usage_end_text,
                    usage_quantity_text, billed_cost_text, currency_code_text
                ) FROM STDIN
                """
            ) as copy:
                for row in normalized:
                    copy.write_row([batch_id, *row])

        result = conn.execute(
            "SELECT validate_billing_batch(%s) AS message", (batch_id,)
        ).fetchone()
        conn.execute("CALL refresh_materialized_views()")
        summary = conn.execute(
            """
            SELECT b.*, a.account_name, cp.provider_name
            FROM billing_import_batches b
            JOIN cloud_accounts a ON b.account_id = a.account_id
            JOIN cloud_providers cp ON a.provider_id = cp.provider_id
            WHERE b.batch_id = %s
            """,
            (batch_id,),
        ).fetchone()
        errors = conn.execute(
            """
            SELECT error_id, raw_row_index, error_code, error_message
            FROM billing_import_errors
            WHERE batch_id = %s
            ORDER BY error_id
            """,
            (batch_id,),
        ).fetchall()

    return jsonify(message=result["message"], batch=summary, errors=errors), 201


@bp.get("/batches")
def list_batches():
    limit = int_arg("limit", default=50, minimum=1, maximum=200)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT b.*, a.account_name, cp.provider_name, u.username AS imported_by_name,
                   (SELECT COUNT(*) FROM billing_import_errors e WHERE e.batch_id = b.batch_id) AS error_count
            FROM billing_import_batches b
            JOIN cloud_accounts a ON b.account_id = a.account_id
            JOIN cloud_providers cp ON a.provider_id = cp.provider_id
            LEFT JOIN users u ON b.imported_by = u.user_id
            ORDER BY b.imported_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return jsonify(items=rows)


@bp.get("/batches/<int:batch_id>/errors")
def batch_errors(batch_id: int):
    with get_conn() as conn:
        errors = conn.execute(
            """
            SELECT * FROM billing_import_errors
            WHERE batch_id = %s
            ORDER BY error_id
            """,
            (batch_id,),
        ).fetchall()
    return jsonify(batch_id=batch_id, items=errors)
