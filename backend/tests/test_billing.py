"""Billing reads and CSV ingestion tests."""

from __future__ import annotations

import io
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DATA = ROOT / "sample_data"


def _month_of(value):
    if len(value) >= 7 and value[4] == "-":
        return value[:7]
    return parsedate_to_datetime(value).strftime("%Y-%m")


def _upload(client, name):
    payload = (SAMPLE_DATA / name).read_bytes()
    return _upload_payload(client, payload, name)


def _upload_payload(client, payload, name="upload.csv"):
    return client.post(
        "/api/batches/upload",
        data={"file": (io.BytesIO(payload), name)},
        content_type="multipart/form-data",
    )


def _preview(client, name):
    payload = (SAMPLE_DATA / name).read_bytes()
    return client.post(
        "/api/batches/preview",
        data={"file": (io.BytesIO(payload), name)},
        content_type="multipart/form-data",
    )


def test_billing_records_and_total(client):
    payload = client.get("/api/billing?limit=10").get_json()
    assert payload["total"] >= 324
    assert len(payload["items"]) == 10
    assert float(payload["total_cost"]) > 0
    assert payload["items"][0]["source_batch_id"] is not None


def test_billing_month_filter(client):
    payload = client.get("/api/billing?month=2026-09&limit=1000").get_json()
    assert payload["items"]
    assert all(_month_of(item["usage_start"]) == "2026-09" for item in payload["items"])


def test_monthly_rollup(client):
    payload = client.get("/api/billing/monthly?month=2026-10").get_json()
    assert payload["items"]
    assert all(_month_of(item["billing_month"]) == "2026-10" for item in payload["items"])
    assert sum(float(item["total_aggregate_cost"]) for item in payload["items"]) > 0


def test_shared_cost_allocation(client):
    payload = client.get("/api/billing/shared-allocation?month=2026-10").get_json()
    assert payload["items"]
    assert all(0 < float(item["allocation_percentage"]) <= 100 for item in payload["items"])


def test_preview_clean_batch_maps_and_resolves_accounts(client):
    response = _preview(client, "billing_upload_clean.csv")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["row_count"] == 5
    assert payload["valid_row_count"] == 5
    assert payload["parse_error_count"] == 0
    assert payload["mapping"]["BilledCost"] == "BilledCost"
    assert payload["currencies"] == ["USD"]
    assert len(payload["accounts"]) == 4
    assert all(account["resolved"] for account in payload["accounts"])


def test_upload_clean_batch(client):
    response = _upload(client, "billing_upload_clean.csv")
    assert response.status_code == 201
    payload = response.get_json()
    batch = payload["batch"]
    assert batch["accepted_count"] == 5
    assert batch["rejected_count"] == 0
    assert batch["duplicate_count"] == 0
    assert batch["status"] == "processed"
    assert float(batch["accepted_total"]) > 0
    assert payload["errors"] == []

    errors = client.get(f"/api/batches/{batch['batch_id']}/errors").get_json()
    assert errors["items"] == []


def test_upload_mixed_batch_quarantines_bad_rows(client):
    response = _upload(client, "billing_upload_mixed.csv")
    assert response.status_code == 201
    payload = response.get_json()
    batch = payload["batch"]
    assert batch["accepted_count"] == 1
    assert batch["rejected_count"] == 5
    assert batch["duplicate_count"] == 0
    assert batch["row_count"] == 6
    assert batch["status"] == "processed"

    errors = client.get(f"/api/batches/{batch['batch_id']}/errors").get_json()["items"]
    assert len(errors) == 5
    codes = {item["error_code"] for item in errors}
    assert "23514" in codes
    assert "23502" in codes
    assert "22P02" in codes
    assert all(item["error_message"] for item in errors)


def test_duplicate_file_is_rejected(client):
    first = _upload(client, "billing_upload_clean.csv")
    assert first.status_code == 201
    batch_id = first.get_json()["batch"]["batch_id"]

    second = _upload(client, "billing_upload_clean.csv")
    assert second.status_code == 409
    payload = second.get_json()
    assert payload["code"] == "DUPLICATE_FILE"
    assert payload["batch"]["batch_id"] == batch_id


def test_unknown_account_fails_batch(client):
    payload = (SAMPLE_DATA / "billing_upload_clean.csv").read_bytes()
    response = _upload_payload(
        client,
        payload.replace(b"111122223333", b"999999999999", 1),
        "unknown-account.csv",
    )
    assert response.status_code == 422
    result = response.get_json()
    assert result["code"] == "UNKNOWN_ACCOUNT"
    assert result["batch"]["status"] == "failed"


def test_upload_requires_file(client):
    response = client.post("/api/batches/upload", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
