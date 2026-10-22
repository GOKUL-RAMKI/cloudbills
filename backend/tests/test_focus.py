"""FOCUS CSV contract tests."""

from __future__ import annotations

import csv
import io

import pytest

from focus import FOCUS_COLUMNS, MANDATORY_COLUMNS, FocusError, canonical_header, parse_csv

MANDATORY_ORDER = [
    "BilledCost",
    "BillingAccountId",
    "BillingAccountName",
    "BillingCurrency",
    "BillingPeriodEnd",
    "BillingPeriodStart",
    "ChargeCategory",
    "ChargeClass",
    "ChargeDescription",
    "ChargePeriodEnd",
    "ChargePeriodStart",
    "ContractedCost",
    "EffectiveCost",
    "HostProviderName",
    "InvoiceIssuerName",
    "ListCost",
    "PricingQuantity",
    "PricingUnit",
    "ServiceCategory",
    "ServiceName",
    "ServiceProviderName",
]

DEFAULTS = {
    "BilledCost": "10.00",
    "BillingAccountId": "111122223333",
    "BillingAccountName": "AWS Infrastructure Prod",
    "BillingCurrency": "USD",
    "BillingPeriodEnd": "2026-10-01T00:00:00Z",
    "BillingPeriodStart": "2026-09-01T00:00:00Z",
    "ChargeCategory": "Usage",
    "ChargeClass": "",
    "ChargeDescription": "Test charge",
    "ChargePeriodEnd": "2026-10-01T00:00:00Z",
    "ChargePeriodStart": "2026-09-01T00:00:00Z",
    "ContractedCost": "10.00",
    "EffectiveCost": "10.00",
    "HostProviderName": "AWS",
    "InvoiceIssuerName": "AWS",
    "ListCost": "10.00",
    "PricingQuantity": "730",
    "PricingUnit": "Hrs",
    "ServiceCategory": "Compute",
    "ServiceName": "Amazon EC2",
    "ServiceProviderName": "AWS",
}


def _csv(*rows: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _headers(*extra: str) -> list[str]:
    return MANDATORY_ORDER + list(extra)


def _row(*extra: str, **overrides: str) -> list[str]:
    values = {**DEFAULTS, **overrides}
    return [values.get(name, "") for name in _headers(*extra)]


def test_focus_contract_has_65_columns_and_21_mandatory():
    assert len(FOCUS_COLUMNS) == 65
    assert set(MANDATORY_ORDER) == MANDATORY_COLUMNS


def test_header_aliases_are_canonicalised():
    assert canonical_header("ProviderName") == "ServiceProviderName"
    assert canonical_header("billed_cost") == "BilledCost"
    assert canonical_header("Billing Account ID") == "BillingAccountId"
    assert canonical_header("UnknownColumn") is None


def test_parse_preserves_unknown_columns_and_tags():
    raw = _csv(
        _headers("Tags", "ExtraColumn"),
        _row("Tags", "ExtraColumn", Tags='{"Owner":"web-team"}', ExtraColumn="keep-me"),
    )
    parsed = parse_csv(raw)
    assert parsed.unknown_columns == ["ExtraColumn"]
    assert parsed.rows[0].values["Tags"] == '{"Owner":"web-team"}'
    assert parsed.rows[0].values["_extra"] == {"ExtraColumn": "keep-me"}


def test_parse_allows_negative_billed_cost():
    raw = _csv(_headers(), _row(BilledCost="-50.00", ChargeCategory="Credit"))
    parsed = parse_csv(raw)
    assert parsed.rows[0].values["BilledCost"] == "-50.00"


def test_parse_rejects_missing_required_header():
    headers = [name for name in MANDATORY_ORDER if name != "BilledCost"]
    values = [DEFAULTS[name] for name in headers]
    with pytest.raises(FocusError) as exc:
        parse_csv(_csv(headers, values))
    assert exc.value.code == "MISSING_REQUIRED_COLUMN"
    assert "BilledCost" in exc.value.details["columns"]


def test_parse_marks_row_width_errors_without_aborting_file():
    raw = _csv(_headers(), _row(), _row()[:-1])
    parsed = parse_csv(raw)
    assert len(parsed.rows) == 2
    assert parsed.rows[0].error is None
    assert parsed.rows[1].error is not None
    assert parsed.rows[1].values["_parse_error"] == parsed.rows[1].error


def test_parse_rejects_duplicate_headers():
    headers = _headers("BilledCost")
    values = _row("BilledCost")
    with pytest.raises(FocusError) as exc:
        parse_csv(_csv(headers, values))
    assert exc.value.code == "DUPLICATE_HEADER"


def test_parse_rejects_empty_file_and_non_utf8():
    with pytest.raises(FocusError) as exc:
        parse_csv(b"")
    assert exc.value.code == "EMPTY_FILE"

    with pytest.raises(FocusError) as exc:
        parse_csv(b"\xff\xfe")
    assert exc.value.code == "INVALID_ENCODING"
