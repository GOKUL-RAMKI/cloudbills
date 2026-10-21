"""Health, discovery, lookups and audit endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify

from db import get_conn

bp = Blueprint("meta", __name__)

ENDPOINTS = [
    {"method": "GET", "path": "/api", "description": "API discovery"},
    {"method": "GET", "path": "/api/health", "description": "Database connectivity"},
    {"method": "GET", "path": "/api/stats", "description": "Schema object and row counts"},
    {"method": "GET", "path": "/api/lookups", "description": "Form reference data"},
    {"method": "GET", "path": "/api/dashboard", "description": "FinOps KPI payload"},
    {"method": "GET", "path": "/api/resources", "description": "List inventory"},
    {"method": "GET", "path": "/api/resources/<id>", "description": "Resource detail"},
    {"method": "POST", "path": "/api/resources", "description": "Create resource via add_resource"},
    {"method": "PUT", "path": "/api/resources/<id>", "description": "Update resource"},
    {"method": "DELETE", "path": "/api/resources/<id>", "description": "Soft delete resource"},
    {"method": "POST", "path": "/api/resources/<id>/tags", "description": "Upsert a tag"},
    {"method": "DELETE", "path": "/api/resources/<id>/tags/<key>", "description": "Delete a tag"},
    {"method": "GET", "path": "/api/recommendations", "description": "Approval queue"},
    {"method": "GET", "path": "/api/recommendations/<id>", "description": "Recommendation detail"},
    {"method": "POST", "path": "/api/recommendations/<id>/request-review", "description": "Submit for review"},
    {"method": "POST", "path": "/api/recommendations/<id>/approve", "description": "Approve with FOR UPDATE"},
    {"method": "POST", "path": "/api/recommendations/<id>/reject", "description": "Reject"},
    {"method": "POST", "path": "/api/recommendations/<id>/execute", "description": "Execute action"},
    {"method": "POST", "path": "/api/recommendations/<id>/savings", "description": "Record savings"},
    {"method": "GET", "path": "/api/detections/idle", "description": "Idle compute detector"},
    {"method": "GET", "path": "/api/detections/orphaned-volumes", "description": "Orphan storage detector"},
    {"method": "GET", "path": "/api/detections/missing-tags", "description": "Missing tags detector"},
    {"method": "GET", "path": "/api/dependencies/<resource_id>", "description": "Recursive dependency chain"},
    {"method": "GET", "path": "/api/billing", "description": "Billing facts"},
    {"method": "GET", "path": "/api/billing/monthly", "description": "Materialized monthly rollup"},
    {"method": "GET", "path": "/api/billing/shared-allocation", "description": "Shared-cost allocation"},
    {"method": "POST", "path": "/api/batches/upload", "description": "CSV to staging + validation"},
    {"method": "GET", "path": "/api/batches", "description": "Import batches"},
    {"method": "GET", "path": "/api/batches/<id>/errors", "description": "Quarantined rows"},
    {"method": "GET", "path": "/api/budgets", "description": "Budget utilisation"},
    {"method": "POST", "path": "/api/budgets", "description": "Create budget"},
    {"method": "PUT", "path": "/api/budgets/<id>", "description": "Update budget"},
    {"method": "DELETE", "path": "/api/budgets/<id>", "description": "Delete budget"},
    {"method": "GET", "path": "/api/budgets/alerts", "description": "Budget alerts"},
    {"method": "POST", "path": "/api/budgets/alerts/<id>/ack", "description": "Acknowledge alert"},
    {"method": "GET", "path": "/api/audit", "description": "Recent audit trail"},
]


@bp.get("")
@bp.get("/")
def index():
    return jsonify(
        name="clouDBills API",
        version="1.0",
        architecture="HTML/JS -> Flask -> PostgreSQL stored programs",
        endpoints=ENDPOINTS,
    )


@bp.get("/health")
def health():
    with get_conn() as conn:
        row = conn.execute("SELECT NOW() AS server_time, current_database() AS database").fetchone()
    return jsonify(status="ok", **row)


@bp.get("/stats")
def stats():
    with get_conn() as conn:
        tables = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
            """
        ).fetchone()["count"]
        partitions = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relispartition
            """
        ).fetchone()["count"]
        triggers = conn.execute(
            "SELECT COUNT(*) AS count FROM pg_trigger WHERE NOT tgisinternal"
        ).fetchone()["count"]
        routines = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM information_schema.routines
            WHERE routine_schema = 'public'
            """
        ).fetchone()["count"]
        materialized = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'm'
            """
        ).fetchone()["count"]
        rows = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM resources) AS resources,
                (SELECT COUNT(*) FROM billing_records) AS billing_records,
                (SELECT COUNT(*) FROM usage_metrics) AS usage_metrics,
                (SELECT COUNT(*) FROM recommendations) AS recommendations,
                (SELECT COUNT(*) FROM audit_logs) AS audit_logs
            """
        ).fetchone()
    return jsonify(
        base_tables=tables,
        partitions=partitions,
        materialized_views=materialized,
        triggers=triggers,
        routines=routines,
        rows=rows,
    )


@bp.get("/lookups")
def lookups():
    with get_conn() as conn:
        providers = conn.execute("SELECT * FROM cloud_providers ORDER BY provider_name").fetchall()
        accounts = conn.execute(
            """
            SELECT a.*, p.provider_name
            FROM cloud_accounts a
            JOIN cloud_providers p ON a.provider_id = p.provider_id
            ORDER BY p.provider_name, a.account_name
            """
        ).fetchall()
        teams = conn.execute("SELECT * FROM teams ORDER BY team_name").fetchall()
        projects = conn.execute(
            """
            SELECT p.*, t.team_name, e.environment_code, e.is_production_grade
            FROM projects p
            JOIN teams t ON p.team_id = t.team_id
            JOIN environments e ON p.environment_id = e.environment_id
            ORDER BY p.project_name
            """
        ).fetchall()
        resource_types = conn.execute(
            """
            SELECT rt.*, cp.provider_name
            FROM resource_types rt
            JOIN cloud_providers cp ON rt.provider_id = cp.provider_id
            ORDER BY cp.provider_name, rt.generic_category, rt.raw_type_name
            """
        ).fetchall()
        regions = conn.execute(
            """
            SELECT r.*, cp.provider_name
            FROM regions r
            JOIN cloud_providers cp ON r.provider_id = cp.provider_id
            ORDER BY cp.provider_name, r.region_code
            """
        ).fetchall()
        users = conn.execute(
            """
            SELECT user_id, email, username, first_name, last_name, is_active
            FROM users ORDER BY user_id
            """
        ).fetchall()
        roles = conn.execute("SELECT * FROM roles ORDER BY role_name").fetchall()
        environments = conn.execute("SELECT * FROM environments ORDER BY environment_id").fetchall()
        metric_types = conn.execute("SELECT * FROM metric_types ORDER BY metric_name").fetchall()
        rules = conn.execute(
            "SELECT * FROM optimization_rules ORDER BY rule_id"
        ).fetchall()
    return jsonify(
        providers=providers,
        accounts=accounts,
        teams=teams,
        projects=projects,
        resource_types=resource_types,
        regions=regions,
        users=users,
        roles=roles,
        environments=environments,
        metric_types=metric_types,
        optimization_rules=rules,
    )


@bp.get("/audit")
def audit():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT al.*, u.username
            FROM audit_logs al
            LEFT JOIN users u ON al.user_id = u.user_id
            ORDER BY al.action_timestamp DESC
            LIMIT 100
            """
        ).fetchall()
    return jsonify(items=rows)
