"""Inventory, tag and detector endpoint tests."""

from __future__ import annotations


def test_resource_inventory_paginates(client):
    payload = client.get("/api/resources?limit=5").get_json()
    assert payload["total"] >= 27
    assert len(payload["items"]) == 5
    assert payload["items"][0]["resource_id"] == 1


def test_resource_search_and_filter(client):
    payload = client.get("/api/resources?search=web-prod").get_json()
    assert payload["total"] >= 2
    assert all("web-prod" in item["resource_name"] for item in payload["items"])

    idle = client.get("/api/resources?status=stopped").get_json()
    assert all(item["status"] == "stopped" for item in idle["items"])


def test_resource_detail_includes_related_rows(client):
    payload = client.get("/api/resources/1").get_json()
    assert payload["resource"]["resource_name"] == "web-prod-01"
    assert "dependencies" in payload
    assert "status_history" in payload
    assert "recommendations" in payload


def test_resource_detail_missing_returns_404(client):
    assert client.get("/api/resources/999999").status_code == 404


def test_resource_crud_and_tags(client):
    lookups = client.get("/api/lookups").get_json()
    account_id = lookups["accounts"][0]["account_id"]
    type_id = lookups["resource_types"][0]["type_id"]
    region_id = lookups["regions"][0]["region_id"]
    project_id = lookups["projects"][0]["project_id"]

    created = client.post(
        "/api/resources",
        json={
            "account_id": account_id,
            "project_id": project_id,
            "type_id": type_id,
            "region_id": region_id,
            "external_resource_id": "test-fixture-0001",
            "resource_name": "fixture-worker",
            "status": "active",
            "hourly_cost": 0.42,
            "tags": {"owner": "finops", "environment": "test"},
            "user_id": 1,
        },
    )
    assert created.status_code == 201
    resource = created.get_json()["resource"]
    resource_id = resource["resource_id"]
    assert resource["resource_name"] == "fixture-worker"
    assert resource["tags"]["owner"] == "finops"

    updated = client.put(
        f"/api/resources/{resource_id}",
        json={
            "project_id": project_id,
            "type_id": type_id,
            "region_id": region_id,
            "resource_name": "fixture-worker-renamed",
            "status": "stopped",
            "hourly_cost": 0.5,
            "user_id": 1,
        },
    )
    assert updated.status_code == 200
    assert updated.get_json()["resource"]["status"] == "stopped"

    tagged = client.post(
        f"/api/resources/{resource_id}/tags",
        json={"key": "cost_center", "value": "CC-42"},
    )
    assert tagged.status_code == 201
    assert {"tag_key": "cost_center", "tag_value": "CC-42"} in tagged.get_json()["tags"]

    removed = client.delete(f"/api/resources/{resource_id}/tags/cost_center")
    assert removed.status_code == 200

    deleted = client.delete(f"/api/resources/{resource_id}", json={"user_id": 1})
    assert deleted.status_code == 200
    detail = client.get(f"/api/resources/{resource_id}").get_json()
    assert detail["resource"]["status"] == "deleted"


def test_resource_validation_errors(client):
    response = client.post("/api/resources", json={"resource_name": "incomplete"})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_detectors(client):
    idle = client.get("/api/detections/idle").get_json()
    assert idle["total"] >= 1
    assert any(item["resource_name"] == "dev-idle-01" for item in idle["items"])

    orphans = client.get("/api/detections/orphaned-volumes").get_json()
    assert orphans["total"] >= 1

    tags = client.get("/api/detections/missing-tags").get_json()
    assert tags["total"] >= 1


def test_dependency_chain(client):
    payload = client.get("/api/dependencies/1").get_json()
    assert payload["resource_id"] == 1
    assert isinstance(payload["items"], list)
