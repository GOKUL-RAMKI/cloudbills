"""Budget CRUD, utilisation and alert tests."""

from __future__ import annotations


def test_budget_list_and_utilisation(client):
    payload = client.get("/api/budgets").get_json()
    assert payload["items"]
    for item in payload["items"]:
        assert float(item["budget_amount"]) > 0
        assert item["budget_status"] in ("healthy", "warning", "over_budget")


def test_budget_month_filter(client):
    payload = client.get("/api/budgets?month=2026-10").get_json()
    assert payload["items"]
    assert all(item["budget_month"].startswith("2026-10") for item in payload["items"])


def test_budget_crud(client):
    created = client.post(
        "/api/budgets",
        json={
            "project_id": 1,
            "budget_month": "2026-11-01",
            "budget_amount": 500,
            "warning_percentage": 75,
            "user_id": 1,
        },
    )
    assert created.status_code == 201
    budget = created.get_json()["budget"]
    budget_id = budget["budget_id"]
    assert float(budget["budget_amount"]) == 500
    assert float(budget["warning_percentage"]) == 75

    updated = client.put(
        f"/api/budgets/{budget_id}",
        json={"budget_amount": 650, "warning_percentage": 80, "user_id": 1},
    )
    assert updated.status_code == 200
    assert float(updated.get_json()["budget"]["budget_amount"]) == 650

    rechecked = client.post(f"/api/budgets/{budget_id}/check", json={"user_id": 1})
    assert rechecked.status_code == 200

    deleted = client.delete(f"/api/budgets/{budget_id}", json={"user_id": 1})
    assert deleted.status_code == 200


def test_budget_requires_exactly_one_scope(client):
    response = client.post(
        "/api/budgets",
        json={
            "project_id": 1,
            "team_id": 1,
            "budget_month": "2026-11-01",
            "budget_amount": 100,
        },
    )
    assert response.status_code == 400

    missing = client.post(
        "/api/budgets",
        json={"budget_month": "2026-11-01", "budget_amount": 100},
    )
    assert missing.status_code == 400


def test_budget_validation(client):
    response = client.post(
        "/api/budgets",
        json={"project_id": 1, "budget_month": "2026-11-01", "budget_amount": -5},
    )
    assert response.status_code == 400


def test_alerts_and_acknowledgement(client):
    alerts = client.get("/api/budgets/alerts").get_json()["items"]
    assert alerts
    pending = next((alert for alert in alerts if not alert["is_acknowledged"]), None)
    assert pending is not None

    acknowledged = client.post(
        f"/api/budgets/alerts/{pending['alert_id']}/ack", json={"user_id": 1}
    )
    assert acknowledged.status_code == 200

    refreshed = client.get("/api/budgets/alerts?acknowledged=true").get_json()["items"]
    assert any(alert["alert_id"] == pending["alert_id"] for alert in refreshed)
