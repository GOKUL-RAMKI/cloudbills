"""Smoke tests for discovery, health and the presentation shell."""

from __future__ import annotations

PAGES = [
    "/",
    "/pages/resources.html",
    "/pages/recommendations.html",
    "/pages/billing.html",
    "/pages/budgets.html",
    "/pages/audit.html",
]

STATIC_ASSETS = [
    "/css/styles.css",
    "/js/api.js",
    "/js/layout.js",
    "/js/dashboard.js",
    "/js/resources.js",
    "/js/recommendations.js",
    "/js/billing.js",
    "/js/budgets.js",
    "/js/audit.js",
]


def test_health_reports_database(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["database"] == "cloudbills"


def test_schema_statistics(client):
    payload = client.get("/api/stats").get_json()
    assert payload["base_tables"] == 33
    assert payload["partitions"] == 10
    assert payload["materialized_views"] == 1
    assert payload["triggers"] >= 8
    assert payload["routines"] >= 20
    assert payload["rows"]["resources"] >= 27


def test_lookups_cover_form_reference_data(client):
    payload = client.get("/api/lookups").get_json()
    for key in ("providers", "accounts", "teams", "projects", "resource_types", "regions", "users"):
        assert payload[key], f"{key} should not be empty"
    assert any(project["project_name"] == "core-platform" for project in payload["projects"])


def test_dashboard_summary_shape(client):
    payload = client.get("/api/dashboard").get_json()
    summary = payload["summary"]
    assert summary["total_resources"] >= 27
    assert summary["total_projects"] >= 6
    assert summary["monthly_spend"] > 0
    assert isinstance(summary["spend_by_month"], list)
    assert isinstance(summary["recent_recommendations"], list)
    assert "recent_audit" in payload and "recent_batches" in payload


def test_audit_endpoint_returns_newest_first(client):
    items = client.get("/api/audit").get_json()["items"]
    assert items
    timestamps = [item["action_timestamp"] for item in items]
    assert timestamps == sorted(timestamps, reverse=True)


def test_api_discovery_lists_routes(client):
    payload = client.get("/api").get_json()
    paths = {entry["path"] for entry in payload["endpoints"]}
    assert "/api/resources" in paths
    assert "/api/recommendations" in paths
    assert "/api/batches/upload" in paths
    assert "/api/budgets/alerts" in paths


def test_pages_render(client):
    for path in PAGES:
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"<html" in response.data.lower(), path


def test_static_assets_are_served(client):
    for path in STATIC_ASSETS:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.data, path
