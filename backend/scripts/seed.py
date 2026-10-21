"""Populate clouDBills with rich, demonstrative data.

The seed intentionally routes every recommendation state change through the
same stored procedures the web UI uses, so triggers, partial indexes and
audit trails are exercised during setup.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from werkzeug.security import generate_password_hash

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from db import DATABASE_URL  # noqa: E402

UTC = timezone.utc
PASSWORD = "cloudbills123"


def one(cur, sql: str, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def run_seed(force: bool = False) -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            existing = one(cur, "SELECT COUNT(*) FROM cloud_providers")
            if existing and not force:
                print("Seed data already present. Use --force to reseed.")
                return

            if existing:
                cur.execute("TRUNCATE audit_logs, savings_realization, recommendation_actions, "
                            "recommendation_status_history, approval_requests, recommendation_evidence, "
                            "recommendations, budget_alerts, budgets, allocated_costs, "
                            "resource_daily_summaries, usage_metrics, billing_records, billing_import_errors, "
                            "billing_staging, billing_import_batches, resource_status_history, "
                            "resource_consumers, resource_dependencies, resource_tags, resources, "
                            "cloud_accounts, resource_types, regions, projects, teams, user_roles, roles, "
                            "users, environments, cloud_providers, metric_types, optimization_rules "
                            "RESTART IDENTITY CASCADE")

            # ---------------------------------------------------------
            # Master data
            # ---------------------------------------------------------
            providers = {}
            for name, url in [
                ("AWS", "https://aws.amazon.com"),
                ("GCP", "https://cloud.google.com"),
                ("Azure", "https://azure.microsoft.com"),
            ]:
                providers[name] = one(
                    cur,
                    "INSERT INTO cloud_providers (provider_name, api_endpoint_url) "
                    "VALUES (%s, %s) RETURNING provider_id",
                    (name, url),
                )

            environments = {}
            for code, prod in [
                ("development", False),
                ("testing", False),
                ("staging", False),
                ("production", True),
            ]:
                environments[code] = one(
                    cur,
                    "INSERT INTO environments (environment_code, is_production_grade) "
                    "VALUES (%s, %s) RETURNING environment_id",
                    (code, prod),
                )

            users = {}
            user_specs = [
                ("admin", "Ananya", "Rao", "admin@cloudbills.io"),
                ("lead", "Rahul", "Menon", "rahul@cloudbills.io"),
                ("analyst", "Priya", "Nair", "priya@cloudbills.io"),
                ("auditor", "Vikram", "Iyer", "vikram@cloudbills.io"),
                ("engineer", "Meera", "Das", "meera@cloudbills.io"),
            ]
            for username, first, last, email in user_specs:
                users[username] = one(
                    cur,
                    "INSERT INTO users (email, username, password_hash, first_name, last_name) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING user_id",
                    (email, username, generate_password_hash(PASSWORD), first, last),
                )

            roles = {}
            for role, desc, perms in [
                ("FinOps Admin", "Full approval and configuration access",
                 {"recommendations": ["read", "approve", "execute"], "budgets": ["write"]}),
                ("Team Lead", "Owns projects, budgets and review requests",
                 {"recommendations": ["read", "request"], "budgets": ["read"]}),
                ("Auditor", "Read-only access to financial and audit data",
                 {"recommendations": ["read"], "audit_logs": ["read"]}),
            ]:
                roles[role] = one(
                    cur,
                    "INSERT INTO roles (role_name, description, permissions) "
                    "VALUES (%s, %s, %s) RETURNING role_id",
                    (role, desc, Jsonb(perms)),
                )

            for username, role in [
                ("admin", "FinOps Admin"),
                ("lead", "Team Lead"),
                ("analyst", "Team Lead"),
                ("auditor", "Auditor"),
                ("engineer", "Team Lead"),
            ]:
                cur.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                    (users[username], roles[role]),
                )

            teams = {}
            for team, cc, manager in [
                ("Platform Engineering", "CC-1001", "lead"),
                ("Data Analytics", "CC-1002", "analyst"),
                ("Web Products", "CC-1003", "admin"),
                ("Security", "CC-1004", "auditor"),
            ]:
                teams[team] = one(
                    cur,
                    "INSERT INTO teams (team_name, cost_center, manager_user_id) "
                    "VALUES (%s, %s, %s) RETURNING team_id",
                    (team, cc, users[manager]),
                )

            projects = {}
            project_specs = [
                ("core-platform", "Platform Engineering", "production", "Shared platform services"),
                ("data-lake", "Data Analytics", "development", "Analytics data lake and ETL"),
                ("analytics-prod", "Data Analytics", "production", "Production analytics API"),
                ("storefront", "Web Products", "production", "Customer storefront"),
                ("storefront-staging", "Web Products", "staging", "Pre-production storefront"),
                ("internal-tools", "Platform Engineering", "development", "Internal engineering tools"),
            ]
            for name, team, env, desc in project_specs:
                projects[name] = one(
                    cur,
                    "INSERT INTO projects (team_id, environment_id, project_name, description) "
                    "VALUES (%s, %s, %s, %s) RETURNING project_id",
                    (teams[team], environments[env], name, desc),
                )

            region_codes = {
                "AWS": [("us-east-1", "US East (N. Virginia)", "North America"),
                        ("eu-west-1", "EU West (Ireland)", "Europe"),
                        ("ap-south-1", "Asia Pacific (Mumbai)", "Asia")],
                "GCP": [("us-central1", "Iowa", "North America"),
                        ("europe-west1", "Belgium", "Europe"),
                        ("asia-south1", "Mumbai", "Asia")],
                "Azure": [("eastus", "East US", "North America"),
                          ("westeurope", "West Europe", "Europe"),
                          ("centralindia", "Central India", "Asia")],
            }
            regions = {}
            for provider, items in region_codes.items():
                for code, name, continent in items:
                    regions[(provider, code)] = one(
                        cur,
                        "INSERT INTO regions (provider_id, region_code, region_name, continent) "
                        "VALUES (%s, %s, %s, %s) RETURNING region_id",
                        (providers[provider], code, name, continent),
                    )

            type_specs = {
                "AWS": [
                    ("EC2", "t3.medium", "compute", "hour"),
                    ("EBS", "gp3", "storage", "GB-month"),
                    ("Elastic IP", "eip", "network", "hour"),
                    ("RDS", "db.t3.medium", "database", "hour"),
                ],
                "GCP": [
                    ("Compute Engine", "e2-standard-4", "compute", "hour"),
                    ("Persistent Disk", "pd-balanced", "storage", "GB-month"),
                    ("Cloud NAT", "nat-gateway", "network", "hour"),
                    ("Cloud SQL", "db-custom-4-16384", "database", "hour"),
                ],
                "Azure": [
                    ("Virtual Machines", "Standard_D4s_v5", "compute", "hour"),
                    ("Managed Disks", "Premium SSD", "storage", "GB-month"),
                    ("Public IP", "static-ip", "network", "hour"),
                    ("Azure SQL", "S4", "database", "hour"),
                ],
            }
            resource_types = {}
            for provider, items in type_specs.items():
                for service, type_name, category, unit in items:
                    resource_types[(provider, category)] = one(
                        cur,
                        "INSERT INTO resource_types "
                        "(provider_id, raw_service_code, raw_type_name, generic_category, pricing_unit) "
                        "VALUES (%s, %s, %s, %s, %s) RETURNING type_id",
                        (providers[provider], service, type_name, category, unit),
                    )

            account_specs = [
                ("AWS", "aws-prod", "AWS Infrastructure Prod", "111122223333", "USD"),
                ("AWS", "aws-labs", "AWS Engineering Labs", "444455556666", "USD"),
                ("GCP", "gcp-analytics", "GCP Analytics Platform", "gcp-datalake-01", "USD"),
                ("GCP", "gcp-shared", "GCP Shared Services", "gcp-shared-01", "USD"),
                ("Azure", "azure-store", "Azure Storefront", "sub-storefront-01", "USD"),
                ("Azure", "azure-security", "Azure Security", "sub-security-01", "USD"),
            ]
            accounts = {}
            for provider, key, name, external, currency in account_specs:
                accounts[key] = one(
                    cur,
                    "INSERT INTO cloud_accounts "
                    "(provider_id, account_name, external_account_id, currency_code) "
                    "VALUES (%s, %s, %s, %s) RETURNING account_id",
                    (providers[provider], name, external, currency),
                )

            # ---------------------------------------------------------
            # Inventory
            # ---------------------------------------------------------
            resource_specs = [
                # key, account, project, category, region, external id, name, cost, tags
                ("web-prod-01", "aws-prod", "storefront", "compute", "us-east-1", "i-0a1b2c3d4e5f60001", 0.192,
                 {"owner": "web-team", "project": "storefront", "environment": "production", "tier": "frontend"}),
                ("web-prod-02", "aws-prod", "storefront", "compute", "us-east-1", "i-0a1b2c3d4e5f60002", 0.192,
                 {"owner": "web-team", "project": "storefront", "environment": "production", "tier": "frontend"}),
                ("api-prod-01", "aws-prod", "core-platform", "compute", "us-east-1", "i-0a1b2c3d4e5f60003", 0.384,
                 {"owner": "platform", "project": "core-platform", "environment": "production", "tier": "api"}),
                ("worker-prod-01", "aws-prod", "core-platform", "compute", "eu-west-1", "i-0a1b2c3d4e5f60004", 0.288,
                 {"owner": "platform", "project": "core-platform", "environment": "production", "tier": "worker"}),
                ("dev-idle-01", "aws-labs", "internal-tools", "compute", "us-east-1", "i-0a1b2c3d4e5f60005", 0.192,
                 {"owner": "platform", "project": "internal-tools", "environment": "development"}),
                ("dev-idle-02", "aws-labs", "internal-tools", "compute", "us-east-1", "i-0a1b2c3d4e5f60006", 0.192,
                 {"owner": "platform", "project": "internal-tools", "environment": "development"}),
                ("test-idle-01", "aws-labs", "storefront-staging", "compute", "eu-west-1", "i-0a1b2c3d4e5f60007", 0.096,
                 {"owner": "web-team", "project": "storefront-staging", "environment": "staging"}),
                ("exp-idle-01", "gcp-analytics", "data-lake", "compute", "us-central1", "gce-exp-0001", 0.256,
                 {"owner": "ml-team"}),
                ("gcp-analytics-01", "gcp-analytics", "analytics-prod", "compute", "us-central1", "gce-analytics-01", 0.420,
                 {"owner": "analytics", "project": "analytics-prod", "environment": "production"}),
                ("gcp-etl-01", "gcp-analytics", "data-lake", "compute", "us-central1", "gce-etl-01", 0.350,
                 {"owner": "analytics", "project": "data-lake", "environment": "development"}),
                ("azure-store-01", "azure-store", "storefront", "compute", "eastus", "vm-store-0001", 0.310,
                 {"owner": "web-team", "project": "storefront", "environment": "production"}),
                ("azure-store-02", "azure-store", "storefront", "compute", "eastus", "vm-store-0002", 0.310,
                 {"owner": "web-team", "project": "storefront", "environment": "production"}),
                ("vol-web-01", "aws-prod", "storefront", "storage", "us-east-1", "vol-0a1b2c3d4e5f60001", 0.080,
                 {"owner": "web-team", "project": "storefront", "environment": "production"}),
                ("vol-api-01", "aws-prod", "core-platform", "storage", "us-east-1", "vol-0a1b2c3d4e5f60002", 0.100,
                 {"owner": "platform", "project": "core-platform", "environment": "production"}),
                ("vol-orphan-01", "aws-prod", "storefront", "storage", "us-east-1", "vol-0a1b2c3d4e5f60003", 0.080,
                 {"owner": "web-team", "project": "storefront", "environment": "production"}),
                ("vol-orphan-02", "aws-labs", "data-lake", "storage", "eu-west-1", "vol-0a1b2c3d4e5f60004", 0.050, {}),
                ("snap-legacy-01", "aws-labs", "internal-tools", "storage", "us-east-1", "snap-0a1b2c3d4e5f60005", 0.020,
                 {"owner": "platform", "project": "internal-tools", "environment": "development"}),
                ("vol-db-01", "gcp-analytics", "analytics-prod", "storage", "us-central1", "pd-analytics-01", 0.120,
                 {"owner": "analytics", "project": "analytics-prod", "environment": "production"}),
                ("eip-web-01", "aws-prod", "storefront", "network", "us-east-1", "eipalloc-0001", 0.005,
                 {"owner": "web-team", "project": "storefront", "environment": "production"}),
                ("eip-orphan-01", "aws-labs", "storefront-staging", "network", "eu-west-1", "eipalloc-0002", 0.005,
                 {"owner": "netops"}),
                ("nat-prod-01", "aws-prod", "core-platform", "network", "us-east-1", "nat-0001", 0.045,
                 {"owner": "platform", "project": "core-platform", "environment": "production"}),
                ("db-prod-01", "aws-prod", "core-platform", "database", "us-east-1", "rds-prod-0001", 0.960,
                 {"owner": "platform", "project": "core-platform", "environment": "production", "backup": "daily"}),
                ("db-analytics-01", "gcp-analytics", "analytics-prod", "database", "us-central1", "sql-analytics-01", 1.200,
                 {"owner": "analytics", "project": "analytics-prod", "environment": "production"}),
                ("db-shared-01", "gcp-shared", None, "database", "us-central1", "sql-shared-01", 2.400,
                 {"owner": "platform", "environment": "production", "shared": "true"}),
                ("k8s-shared-01", "aws-prod", None, "compute", "us-east-1", "eks-shared-01", 1.500,
                 {"owner": "platform", "environment": "production", "shared": "true"}),
                ("cdn-global-01", "azure-store", "storefront", "network", "westeurope", "cdn-store-01", 0.120,
                 {"owner": "web-team", "project": "storefront", "environment": "production"}),
                ("cache-prod-01", "azure-store", "core-platform", "database", "eastus", "redis-prod-01", 0.250,
                 {"owner": "platform", "project": "core-platform", "environment": "production"}),
            ]

            resources: dict[str, int] = {}
            resource_meta: dict[str, dict] = {}
            for key, account, project, category, region, external, cost, tags in resource_specs:
                provider = next(p for p, a, *_ in account_specs if a == account)
                cur.execute(
                    "INSERT INTO resources "
                    "(account_id, project_id, type_id, region_id, external_resource_id, "
                    " resource_name, status, hourly_cost) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'active', %s) RETURNING resource_id",
                    (
                        accounts[account],
                        projects[project] if project else None,
                        resource_types[(provider, category)],
                        regions[(provider, region)],
                        external,
                        key,
                        Decimal(str(cost)),
                    ),
                )
                rid = cur.fetchone()[0]
                resources[key] = rid
                resource_meta[key] = {
                    "account": account,
                    "project": project,
                    "category": category,
                    "provider": provider,
                    "cost": Decimal(str(cost)),
                }
                for tag_key, tag_val in tags.items():
                    cur.execute(
                        "INSERT INTO resource_tags (resource_id, tag_key, tag_value) VALUES (%s, %s, %s)",
                        (rid, tag_key, tag_val),
                    )
                cur.execute(
                    "INSERT INTO resource_status_history (resource_id, previous_status, new_status, reason) "
                    "VALUES (%s, NULL, 'active', 'Seeded inventory')",
                    (rid,),
                )

            dependencies = [
                ("web-prod-01", "vol-web-01", "attached_storage"),
                ("api-prod-01", "vol-api-01", "attached_storage"),
                ("web-prod-01", "eip-web-01", "elastic_ip_association"),
                ("gcp-analytics-01", "vol-db-01", "attached_storage"),
                ("k8s-shared-01", "api-prod-01", "network_interface"),
                ("nat-prod-01", "web-prod-01", "network_route"),
                ("db-prod-01", "worker-prod-01", "database_client"),
            ]
            for parent, child, dep_type in dependencies:
                cur.execute(
                    "INSERT INTO resource_dependencies "
                    "(parent_resource_id, child_resource_id, dependency_type) VALUES (%s, %s, %s)",
                    (resources[parent], resources[child], dep_type),
                )

            for resource, project, pct in [
                ("k8s-shared-01", "core-platform", 40),
                ("k8s-shared-01", "analytics-prod", 35),
                ("k8s-shared-01", "storefront", 25),
                ("db-shared-01", "core-platform", 60),
                ("db-shared-01", "analytics-prod", 40),
            ]:
                cur.execute(
                    "INSERT INTO resource_consumers (resource_id, project_id, allocation_percentage) "
                    "VALUES (%s, %s, %s)",
                    (resources[resource], projects[project], Decimal(str(pct))),
                )

            # ---------------------------------------------------------
            # Telemetry
            # ---------------------------------------------------------
            metrics = {}
            for name, unit, agg in [
                ("cpu_utilization", "percent", "avg"),
                ("memory_utilization", "percent", "avg"),
                ("disk_iops", "iops", "avg"),
                ("network_bytes", "bytes", "sum"),
            ]:
                metrics[name] = one(
                    cur,
                    "INSERT INTO metric_types (metric_name, unit, aggregation_method) "
                    "VALUES (%s, %s, %s) RETURNING metric_type_id",
                    (name, unit, agg),
                )

            idle_resources = ["dev-idle-01", "dev-idle-02", "test-idle-01", "exp-idle-01"]
            busy_compute = [
                "web-prod-01", "web-prod-02", "api-prod-01", "worker-prod-01",
                "gcp-analytics-01", "gcp-etl-01", "azure-store-01", "azure-store-02",
                "k8s-shared-01",
            ]

            for key in idle_resources:
                cur.execute(
                    """
                    INSERT INTO usage_metrics (resource_id, metric_type_id, measured_at, metric_value)
                    SELECT %s, %s, CURRENT_TIMESTAMP - ((g || ' hours')::INTERVAL),
                           2.0 + (g %% 3) * 0.5
                    FROM generate_series(0, 503) AS g
                    """,
                    (resources[key], metrics["cpu_utilization"]),
                )
                cur.execute(
                    """
                    INSERT INTO usage_metrics (resource_id, metric_type_id, measured_at, metric_value)
                    SELECT %s, %s, CURRENT_TIMESTAMP - ((g * 6 || ' hours')::INTERVAL),
                           25.0 + (g %% 10)
                    FROM generate_series(0, 83) AS g
                    """,
                    (resources[key], metrics["memory_utilization"]),
                )

            for idx, key in enumerate(busy_compute):
                cur.execute(
                    """
                    INSERT INTO usage_metrics (resource_id, metric_type_id, measured_at, metric_value)
                    SELECT %s, %s, CURRENT_TIMESTAMP - ((g * 6 || ' hours')::INTERVAL),
                           35.0 + ((g + %s) %% 35)
                    FROM generate_series(0, 83) AS g
                    """,
                    (resources[key], metrics["cpu_utilization"], idx),
                )
                cur.execute(
                    """
                    INSERT INTO usage_metrics (resource_id, metric_type_id, measured_at, metric_value)
                    SELECT %s, %s, CURRENT_TIMESTAMP - ((g * 6 || ' hours')::INTERVAL),
                           45.0 + ((g + %s) %% 30)
                    FROM generate_series(0, 83) AS g
                    """,
                    (resources[key], metrics["memory_utilization"], idx),
                )

            for key in ["vol-web-01", "vol-api-01", "vol-orphan-01", "vol-orphan-02",
                        "vol-db-01", "snap-legacy-01"]:
                cur.execute(
                    """
                    INSERT INTO usage_metrics (resource_id, metric_type_id, measured_at, metric_value)
                    SELECT %s, %s, CURRENT_TIMESTAMP - ((g * 12 || ' hours')::INTERVAL),
                           10.0 + (g %% 12)
                    FROM generate_series(0, 41) AS g
                    """,
                    (resources[key], metrics["disk_iops"]),
                )

            cur.execute(
                """
                INSERT INTO resource_daily_summaries
                    (resource_id, metric_type_id, summary_date, avg_value, max_value, min_value, sample_count)
                SELECT resource_id, metric_type_id, measured_at::DATE,
                       ROUND(AVG(metric_value), 6), MAX(metric_value), MIN(metric_value), COUNT(*)
                FROM usage_metrics
                GROUP BY resource_id, metric_type_id, measured_at::DATE
                ON CONFLICT (resource_id, metric_type_id, summary_date) DO NOTHING
                """
            )

            # ---------------------------------------------------------
            # Billing batches and facts
            # ---------------------------------------------------------
            batch_specs = [
                ("aws-prod", "billing_2026_09_aws.csv", "a" * 64, 1),
                ("gcp-analytics", "billing_2026_09_gcp.csv", "b" * 64, 2),
                ("azure-store", "billing_2026_09_azure.csv", "c" * 64, 3),
            ]
            batches = {}
            for account, filename, checksum, days_ago in batch_specs:
                batches[account] = one(
                    cur,
                    "INSERT INTO billing_import_batches "
                    "(account_id, imported_by, file_name, file_checksum, imported_at, status) "
                    "VALUES (%s, %s, %s, %s, %s, 'processed') RETURNING batch_id",
                    (accounts[account], users["analyst"], filename, checksum,
                     datetime.now(UTC) - timedelta(days=days_ago)),
                )

            batch_counts = {batch_id: 0 for batch_id in batches.values()}
            service_names = {
                "compute": "Compute Instance",
                "storage": "Block Storage",
                "network": "Network Services",
                "database": "Managed Database",
            }
            billed_resources = [k for k, meta in resource_meta.items() if meta["cost"] > 0]
            for month_start in (date(2026, 9, 1), date(2026, 10, 1)):
                for idx, key in enumerate(billed_resources):
                    meta = resource_meta[key]
                    batch_id = batches.get(meta["account"])
                    if batch_id is None:
                        batch_id = batches["aws-prod"]
                    for j in range(6):
                        day = 1 + ((idx * 5 + j * 4) % 27)
                        start = datetime(month_start.year, month_start.month, day, tzinfo=UTC)
                        end = start + timedelta(days=1)
                        cost = (meta["cost"] * Decimal(24) *
                                (Decimal("1") + Decimal((idx + j) % 4) * Decimal("0.03")))
                        cur.execute(
                            "INSERT INTO billing_records "
                            "(account_id, resource_id, project_id, source_batch_id, service_name, "
                            " usage_start, usage_end, usage_quantity, billed_cost, currency_code) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'USD')",
                            (
                                accounts[meta["account"]],
                                resources[key],
                                projects[meta["project"]] if meta["project"] else None,
                                batch_id,
                                service_names[meta["category"]],
                                start,
                                end,
                                Decimal("24"),
                                cost.quantize(Decimal("0.000001")),
                            ),
                        )
                        batch_counts[batch_id] += 1

            for batch_id, count in batch_counts.items():
                cur.execute(
                    "UPDATE billing_import_batches "
                    "SET row_count = %s, accepted_count = %s, rejected_count = 0 WHERE batch_id = %s",
                    (count, count, batch_id),
                )

            # Point-in-time allocation snapshot for shared infrastructure.
            cur.execute(
                """
                INSERT INTO allocated_costs
                    (resource_id, project_id, allocation_month, allocation_percentage, allocated_amount)
                SELECT b.resource_id, rc.project_id, DATE '2026-10-01', rc.allocation_percentage,
                       ROUND(SUM(b.billed_cost) * rc.allocation_percentage / 100.0, 2)
                FROM billing_records b
                JOIN resource_consumers rc ON b.resource_id = rc.resource_id
                WHERE b.project_id IS NULL
                  AND DATE_TRUNC('month', b.usage_start)::DATE = DATE '2026-10-01'
                GROUP BY b.resource_id, rc.project_id, rc.allocation_percentage
                """
            )

            # ---------------------------------------------------------
            # Budgets (amounts chosen relative to actual October spend)
            # ---------------------------------------------------------
            cur.execute(
                "SELECT project_id, COALESCE(SUM(billed_cost), 0) "
                "FROM billing_records WHERE DATE_TRUNC('month', usage_start)::DATE = DATE '2026-10-01' "
                "GROUP BY project_id"
            )
            spend = {row[0]: row[1] for row in cur.fetchall()}

            def amount_for(project_name: str, factor: str) -> Decimal:
                value = spend.get(projects[project_name], Decimal("0")) * Decimal(factor)
                return max(value, Decimal("100")).quantize(Decimal("0.01"))

            budget_specs = [
                ("core-platform", None, amount_for("core-platform", "0.90"), "80"),
                ("analytics-prod", None, amount_for("analytics-prod", "1.50"), "75"),
                ("storefront", None, amount_for("storefront", "1.10"), "85"),
                ("data-lake", None, amount_for("data-lake", "2.00"), "90"),
            ]
            budget_ids = {}
            for project_name, team_name, amount, warning in budget_specs:
                budget_ids[project_name] = one(
                    cur,
                    "SELECT create_budget(%s, %s, DATE '2026-10-01', %s, %s, %s)",
                    (
                        projects[project_name] if project_name else None,
                        teams[team_name] if team_name else None,
                        amount,
                        Decimal(warning),
                        users["admin"],
                    ),
                )

            team_spend = spend.get(projects["analytics-prod"], Decimal("0")) + \
                spend.get(projects["data-lake"], Decimal("0"))
            one(
                cur,
                "SELECT create_budget(%s, %s, DATE '2026-10-01', %s, %s, %s)",
                (
                    None,
                    teams["Data Analytics"],
                    max(team_spend * Decimal("1.30"), Decimal("100")).quantize(Decimal("0.01")),
                    Decimal("80"),
                    users["admin"],
                ),
            )

            # ---------------------------------------------------------
            # Optimization rules and workflow data
            # ---------------------------------------------------------
            rules = {}
            rule_specs = [
                ("Idle Compute (21 days)", "idle_compute", "Detect non-production compute below CPU threshold", "high",
                 {"threshold_cpu_percent": 5.0, "lookback_days": 21, "min_telemetry_fraction": 0.8}),
                ("Unattached Block Volumes", "unattached_volume", "Find storage with no active parent attachment", "medium",
                 {"min_idle_days": 14}),
                ("Missing FinOps Tags", "missing_tag", "Find active resources missing owner/project/environment", "low",
                 {"required_tags": ["owner", "project", "environment"]}),
                ("Unattached Elastic IPs", "unattached_ip", "Find static IPs not linked to a live resource", "low",
                 {"grace_days": 7}),
                ("Rightsizing Compute", "rightsizing", "Oversized instances with low sustained utilization", "medium",
                 {"p95_cpu_percent": 20, "lookback_days": 30}),
                ("Budget Overrun", "budget_overrun", "Spend has crossed the configured budget warning line", "critical",
                 {"warning_percentage": 80}),
            ]
            for name, rtype, desc, severity, params in rule_specs:
                rules[rtype] = one(
                    cur,
                    "INSERT INTO optimization_rules "
                    "(rule_name, rule_type, description, severity, parameters) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING rule_id",
                    (name, rtype, desc, severity, Jsonb(params)),
                )

            now = datetime.now(UTC)
            rec_specs = [
                ("dev-idle-02", "idle_compute", "idle_compute",
                 "Average CPU stayed under 3% for 21 days on a development instance.",
                 "Stop the instance outside engineering hours.", "140.16", "low", "detected", None, 6),
                ("test-idle-01", "idle_compute", "idle_compute",
                 "Staging instance has averaged 2.8% CPU with no deployment in 21 days.",
                 "Stop the staging instance until the next release cycle.", "70.08", "low", "under_review",
                 "stop_instance", 5),
                ("exp-idle-01", "idle_compute", "idle_compute",
                 "Notebook experiment server is idle but still billed hourly.",
                 "Stop the experiment server and snapshot its disk.", "186.88", "medium", "under_review",
                 "stop_instance", 4),
                ("vol-orphan-01", "unattached_volume", "unattached_volume",
                 "Block volume has no active instance attachment.",
                 "Snapshot then delete the unattached volume.", "58.40", "low", "detected", "delete_volume", 3),
                ("vol-orphan-02", "unattached_volume", "unattached_volume",
                 "Untagged volume from a retired data-lab job has no parent.",
                 "Delete the orphaned volume after backup verification.", "36.50", "low", "detected",
                 "delete_volume", 3),
                ("db-analytics-01", "rightsizing", "rightsizing",
                 "Database is provisioned for 4x the observed peak load.",
                 "Resize the Cloud SQL instance to a smaller tier.", "220.00", "high", "approved",
                 "resize_instance", 8),
                ("gcp-etl-01", "rightsizing", "rightsizing",
                 "ETL worker is oversized for its nightly batch window.",
                 "Resize to e2-standard-2 during the next maintenance window.", "120.00", "medium", "rejected",
                 "resize_instance", 7),
                ("eip-orphan-01", "unattached_ip", "unattached_elastic_ip",
                 "Static IP is allocated but no longer associated with an instance.",
                 "Release the unused elastic IP.", "3.65", "low", "executed", "release_ip", 10),
                ("snap-legacy-01", "unattached_volume", "unattached_volume",
                 "Legacy snapshot volume is no longer attached to any active resource.",
                 "Delete the legacy snapshot volume.", "14.60", "low", "validated", "delete_volume", 14),
            ]

            for resource_key, rule_type, rec_type, problem, action, saving, risk, final_state, action_type, days_ago in rec_specs:
                meta = resource_meta[resource_key]
                rec_id = one(
                    cur,
                    "INSERT INTO recommendations "
                    "(rule_id, resource_id, project_id, recommendation_type, problem_description, "
                    " suggested_action, estimated_monthly_saving, risk_level, status, detected_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'detected', %s) RETURNING recommendation_id",
                    (
                        rules[rule_type],
                        resources[resource_key],
                        projects[meta["project"]],
                        rec_type,
                        problem,
                        action,
                        Decimal(saving),
                        risk,
                        now - timedelta(days=days_ago),
                    ),
                )

                if rec_type in ("idle_compute", "rightsizing"):
                    observed = Decimal("2.9")
                    threshold = Decimal("5.0")
                    metric_id = metrics["cpu_utilization"]
                else:
                    observed = None
                    threshold = None
                    metric_id = None

                cur.execute(
                    "INSERT INTO recommendation_evidence "
                    "(recommendation_id, metric_type_id, observation_start, observation_end, "
                    " observed_value, threshold_value, narrative) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        rec_id,
                        metric_id,
                        now - timedelta(days=21),
                        now,
                        observed,
                        threshold,
                        problem,
                    ),
                )

                if final_state == "detected":
                    continue

                cur.execute(
                    "CALL submit_recommendation_for_review(%s, %s, %s)",
                    (rec_id, users["lead"], "Please review this finding."),
                )

                if final_state == "under_review":
                    continue

                if final_state == "rejected":
                    cur.execute(
                        "CALL reject_optimization_recommendation(%s, %s, %s)",
                        (rec_id, users["admin"], "Dependency check failed; keep the resource for now."),
                    )
                    continue

                cur.execute(
                    "CALL approve_optimization_recommendation(%s, %s, %s)",
                    (rec_id, users["admin"], "Approved after dependency review."),
                )

                if final_state == "approved":
                    continue

                cur.execute(
                    "CALL execute_recommendation_action(%s, %s, %s)",
                    (rec_id, users["engineer"], action_type),
                )

                if final_state == "executed":
                    continue

                if final_state == "validated":
                    cur.execute(
                        "CALL record_savings(%s, %s, %s, %s, %s, %s)",
                        (
                            rec_id,
                            Decimal("14.60"),
                            Decimal("0.00"),
                            date(2026, 9, 1),
                            date(2026, 10, 1),
                            users["admin"],
                        ),
                    )

            cur.execute(
                "INSERT INTO audit_logs (user_id, action_type, entity_type, entity_id, new_state) "
                "VALUES (%s, 'SEED_DATA', 'database', NULL, %s)",
                (users["admin"], Jsonb({"message": "Demonstration dataset loaded"})),
            )

            cur.execute("REFRESH MATERIALIZED VIEW mv_monthly_project_cost")
            conn.commit()

            print("Seed complete:")
            print(f"  providers={len(providers)} accounts={len(accounts)} resources={len(resources)}")
            print(f"  billing_batches={len(batches)} recommendations={len(rec_specs)}")


if __name__ == "__main__":
    run_seed(force="--force" in sys.argv)
