"""Billing facts, FOCUS CSV ELT ingestion and import-batch endpoints."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from psycopg.types.json import Jsonb

from db import get_conn
from focus import FocusError, parse_csv
from routes.utils import actor_id, int_arg

bp = Blueprint("billing", __name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PILOT_CURRENCY = os.getenv("PILOT_CURRENCY", "USD").strip().upper()


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


def _read_upload(upload) -> bytes:
    raw = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise FocusError("FILE_TOO_LARGE", "CSV file is larger than 10 MB")
    return raw


def _error_response(exc: FocusError):
    return jsonify(error=exc.message, code=exc.code, **exc.details), 400


def _batch_summary(conn, batch_id: int):
    return conn.execute(
        """
        SELECT b.*,
               a.account_name,
               cp.provider_name,
               u.username AS imported_by_name,
               (SELECT COUNT(*) FROM billing_import_errors e
                 WHERE e.batch_id = b.batch_id) AS error_count
        FROM billing_import_batches b
        LEFT JOIN cloud_accounts a ON b.account_id = a.account_id
        LEFT JOIN cloud_providers cp ON a.provider_id = cp.provider_id
        LEFT JOIN users u ON b.imported_by = u.user_id
        WHERE b.batch_id = %s
        """,
        (batch_id,),
    ).fetchone()


def _batch_errors(conn, batch_id: int):
    return conn.execute(
        """
        SELECT error_id, raw_row_index, error_code, error_message
        FROM billing_import_errors
        WHERE batch_id = %s
        ORDER BY error_id
        """,
        (batch_id,),
    ).fetchall()


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
                   b.usage_quantity, b.billed_cost, b.effective_cost,
                   b.charge_category, b.currency_code,
                   b.billing_period_start, b.billing_period_end,
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


@bp.post("/batches/preview")
def preview_batch():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="A CSV file is required"), 400

    try:
        focus_file = parse_csv(_read_upload(upload))
    except FocusError as exc:
        return _error_response(exc)

    total = Decimal("0")
    for row in focus_file.valid_rows:
        value = row.values.get("BilledCost")
        if value in (None, ""):
            continue
        try:
            total += Decimal(str(value))
        except InvalidOperation:
            pass

    account_keys: list[tuple[str, str, str]] = []
    for row in focus_file.valid_rows:
        key = (
            str(row.values.get("ServiceProviderName") or ""),
            str(row.values.get("BillingAccountId") or ""),
            str(row.values.get("SubAccountId") or ""),
        )
        if key not in account_keys:
            account_keys.append(key)

    accounts = []
    with get_conn() as conn:
        for provider, billing_account, sub_account in account_keys:
            resolved = conn.execute(
                """
                SELECT focus_provider_id(%s) AS provider_id,
                       focus_account_id(focus_provider_id(%s), %s, NULLIF(%s, '')) AS account_id
                """,
                (provider, provider, billing_account, sub_account),
            ).fetchone()
            accounts.append(
                {
                    "provider": provider,
                    "billing_account_id": billing_account,
                    "sub_account_id": sub_account or None,
                    "provider_id": resolved["provider_id"],
                    "account_id": resolved["account_id"],
                    "resolved": resolved["account_id"] is not None,
                }
            )

    currencies = sorted(
        {
            str(row.values.get("BillingCurrency") or "").upper()
            for row in focus_file.valid_rows
            if row.values.get("BillingCurrency")
        }
    )
    warnings = []
    if focus_file.unknown_columns:
        warnings.append(
            "Unknown columns will be preserved in focus_payload.extra: "
            + ", ".join(focus_file.unknown_columns)
        )
    if currencies and currencies != [PILOT_CURRENCY]:
        warnings.append(
            f"File currencies {', '.join(currencies)} do not match pilot currency {PILOT_CURRENCY}"
        )
    unresolved = [account for account in accounts if not account["resolved"]]
    if unresolved:
        warnings.append(f"{len(unresolved)} provider/account combination(s) are not registered")

    return jsonify(
        file_name=upload.filename,
        row_count=len(focus_file.rows),
        valid_row_count=sum(1 for _ in focus_file.valid_rows),
        parse_error_count=sum(1 for row in focus_file.rows if row.error),
        headers=focus_file.raw_headers,
        mapping=focus_file.mapping,
        unknown_columns=focus_file.unknown_columns,
        sample_rows=[
            {
                "row_index": row.index,
                "error": row.error,
                "values": row.values,
            }
            for row in focus_file.rows[:10]
        ],
        accounts=accounts,
        currencies=currencies,
        total_billed_cost=str(total),
        pilot_currency=PILOT_CURRENCY,
        warnings=warnings,
    )


@bp.post("/batches/upload")
def upload_batch():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="A CSV file is required"), 400

    try:
        raw = _read_upload(upload)
        focus_file = parse_csv(raw)
    except FocusError as exc:
        return _error_response(exc)

    checksum = hashlib.sha256(raw).hexdigest()
    header_map = {
        header: focus_file.mapping[header] for header in focus_file.raw_headers
    }
    actor = actor_id({})

    with get_conn() as conn:
        conn.execute(
            "SELECT set_config('app.pilot_currency', %s, true)",
            (PILOT_CURRENCY,),
        )
        started = conn.execute(
            """
            SELECT start_focus_batch(%s, %s, %s, %s, %s) AS result
            """,
            (
                upload.filename,
                checksum,
                len(focus_file.rows),
                Jsonb(header_map),
                actor,
            ),
        ).fetchone()["result"]

        batch_id = started["batch_id"]
        if started["duplicate"]:
            return (
                jsonify(
                    error="This file has already been imported",
                    code="DUPLICATE_FILE",
                    batch=_batch_summary(conn, batch_id),
                    errors=_batch_errors(conn, batch_id),
                ),
                409,
            )

        with conn.cursor() as cur:
            with cur.copy(
                """
                COPY billing_staging (batch_id, row_index, raw_payload)
                FROM STDIN
                """
            ) as copy:
                for row in focus_file.rows:
                    copy.write_row([batch_id, row.index, Jsonb(row.values)])

        result = conn.execute(
            "SELECT load_focus_billing_batch(%s) AS result", (batch_id,)
        ).fetchone()["result"]
        summary = _batch_summary(conn, batch_id)
        errors = _batch_errors(conn, batch_id)

        if result.get("error"):
            return (
                jsonify(
                    error=result["error"],
                    code=result.get("code"),
                    batch=summary,
                    errors=errors,
                ),
                422,
            )

        conn.execute("CALL refresh_materialized_views()")

    message = (
        f"Batch {batch_id}: {result['accepted']} accepted, "
        f"{result['rejected']} rejected, {result['duplicate']} duplicate"
    )
    return (
        jsonify(message=message, result=result, batch=summary, errors=errors),
        201,
    )


@bp.get("/batches")
def list_batches():
    limit = int_arg("limit", default=50, minimum=1, maximum=200)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT b.*,
                   COALESCE(a.account_name, 'Multiple accounts') AS account_name,
                   COALESCE(cp.provider_name, 'Multiple providers') AS provider_name,
                   u.username AS imported_by_name,
                   (SELECT COUNT(*) FROM billing_import_errors e
                     WHERE e.batch_id = b.batch_id) AS error_count
            FROM billing_import_batches b
            LEFT JOIN cloud_accounts a ON b.account_id = a.account_id
            LEFT JOIN cloud_providers cp ON a.provider_id = cp.provider_id
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
