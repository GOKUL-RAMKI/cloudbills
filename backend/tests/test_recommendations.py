"""Approval workflow, detection and savings tests."""

from __future__ import annotations


def _first(client, status):
    payload = client.get(f"/api/recommendations?status={status}&limit=1").get_json()
    assert payload["items"], f"expected a recommendation in state {status}"
    return payload["items"][0]


def test_queue_filters_by_status(client):
    payload = client.get("/api/recommendations?status=under_review").get_json()
    assert payload["items"]
    assert all(item["status"] == "under_review" for item in payload["items"])

    detected = client.get("/api/recommendations?status=detected").get_json()
    assert detected["total"] >= 1


def test_recommendation_detail(client):
    item = _first(client, "under_review")
    payload = client.get(f"/api/recommendations/{item['recommendation_id']}").get_json()
    assert payload["recommendation"]["recommendation_id"] == item["recommendation_id"]
    assert payload["evidence"]
    assert payload["approvals"]
    assert payload["status_history"]


def test_recommendation_detail_missing_returns_404(client):
    assert client.get("/api/recommendations/999999").status_code == 404


def test_full_approval_workflow(client):
    item = _first(client, "detected")
    recommendation_id = item["recommendation_id"]

    requested = client.post(
        f"/api/recommendations/{recommendation_id}/request-review",
        json={"user_id": 1, "comments": "Please review idle instance"},
    )
    assert requested.status_code == 200
    assert requested.get_json()["recommendation"]["status"] == "under_review"

    duplicate = client.post(
        f"/api/recommendations/{recommendation_id}/request-review",
        json={"user_id": 1},
    )
    assert duplicate.status_code == 409

    approved = client.post(
        f"/api/recommendations/{recommendation_id}/approve",
        json={"reviewer_user_id": 2, "comments": "Approved by FinOps"},
    )
    assert approved.status_code == 200
    assert approved.get_json()["recommendation"]["status"] == "approved"

    executed = client.post(
        f"/api/recommendations/{recommendation_id}/execute",
        json={"user_id": 2},
    )
    assert executed.status_code == 200
    assert executed.get_json()["recommendation"]["status"] == "executed"

    savings = client.post(
        f"/api/recommendations/{recommendation_id}/savings",
        json={
            "baseline_monthly_cost": float(item["estimated_monthly_saving"]),
            "actual_monthly_cost": 0,
            "measurement_start": "2026-10-01",
            "measurement_end": "2026-10-21",
            "user_id": 2,
        },
    )
    assert savings.status_code == 200
    assert savings.get_json()["recommendation"]["status"] == "validated"
    assert float(savings.get_json()["savings"]["actual_saving"]) == float(
        item["estimated_monthly_saving"]
    )

    detail = client.get(f"/api/recommendations/{recommendation_id}").get_json()
    assert detail["recommendation"]["status"] == "validated"
    assert detail["actions"]
    assert detail["savings"]["measurement_start"] == "2026-10-01"


def test_execute_requires_approved_state(client):
    item = _first(client, "detected")
    response = client.post(
        f"/api/recommendations/{item['recommendation_id']}/execute",
        json={"user_id": 1},
    )
    assert response.status_code == 409


def test_savings_requires_measurement_fields(client):
    item = _first(client, "under_review")
    response = client.post(
        f"/api/recommendations/{item['recommendation_id']}/savings",
        json={"baseline_monthly_cost": 10},
    )
    assert response.status_code == 400
