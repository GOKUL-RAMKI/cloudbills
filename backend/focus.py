"""FOCUS 1.4 Cost and Usage CSV contract and parser."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Iterable


FOCUS_COLUMNS = (
    "AllocatedMethodId",
    "AllocatedMethodDetails",
    "AllocatedResourceId",
    "AllocatedResourceName",
    "AllocatedTags",
    "AvailabilityZone",
    "BilledCost",
    "BillingAccountId",
    "BillingAccountName",
    "BillingAccountType",
    "BillingCurrency",
    "BillingPeriodEnd",
    "BillingPeriodStart",
    "CapacityReservationId",
    "CapacityReservationStatus",
    "ChargeCategory",
    "ChargeClass",
    "ChargeDescription",
    "ChargeFrequency",
    "ChargePeriodEnd",
    "ChargePeriodStart",
    "CommitmentDiscountCategory",
    "CommitmentDiscountId",
    "CommitmentDiscountName",
    "CommitmentDiscountQuantity",
    "CommitmentDiscountStatus",
    "CommitmentDiscountType",
    "CommitmentDiscountUnit",
    "CommitmentProgramEligibilityDetails",
    "ConsumedQuantity",
    "ConsumedUnit",
    "ContractApplied",
    "ContractedCost",
    "ContractedUnitPrice",
    "EffectiveCost",
    "HostProviderName",
    "InvoiceDetailId",
    "InvoiceId",
    "InvoiceIssuerName",
    "ListCost",
    "ListUnitPrice",
    "PricingCategory",
    "PricingCurrency",
    "PricingCurrencyContractedUnitPrice",
    "PricingCurrencyEffectiveCost",
    "PricingCurrencyListUnitPrice",
    "PricingQuantity",
    "PricingUnit",
    "RegionId",
    "RegionName",
    "ResourceId",
    "ResourceName",
    "ResourceType",
    "ServiceCategory",
    "ServiceName",
    "ServiceProviderName",
    "ServiceSubcategory",
    "SkuId",
    "SkuMeter",
    "SkuPriceDetails",
    "SkuPriceId",
    "SubAccountId",
    "SubAccountName",
    "SubAccountType",
    "Tags",
)

MANDATORY_COLUMNS = frozenset(
    {
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
    }
)

JSON_COLUMNS = frozenset(
    {
        "AllocatedTags",
        "AllocatedMethodDetails",
        "CommitmentProgramEligibilityDetails",
        "ContractApplied",
        "SkuPriceDetails",
        "Tags",
    }
)

COLUMN_ALIASES = {
    "ProviderName": "ServiceProviderName",
}

_NORMALIZED_LOOKUP = {
    re.sub(r"[^a-z0-9]", "", column.lower()): column for column in FOCUS_COLUMNS
}
_ALIAS_LOOKUP = {
    re.sub(r"[^a-z0-9]", "", alias.lower()): target
    for alias, target in COLUMN_ALIASES.items()
}


class FocusError(ValueError):
    """A structural FOCUS file error."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass
class FocusRow:
    index: int
    values: dict[str, object]
    error: str | None = None


@dataclass
class FocusFile:
    raw_headers: list[str]
    mapping: dict[str, str | None]
    unknown_columns: list[str]
    rows: list[FocusRow] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def valid_rows(self) -> Iterable[FocusRow]:
        return (row for row in self.rows if row.error is None)


def canonical_header(value: str) -> str | None:
    key = re.sub(r"[^a-z0-9]", "", (value or "").lstrip("\ufeff").strip().lower())
    if not key:
        return None
    return _ALIAS_LOOKUP.get(key) or _NORMALIZED_LOOKUP.get(key)


def parse_csv(raw: bytes, *, require_all_required: bool = True) -> FocusFile:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FocusError("INVALID_ENCODING", "CSV must be UTF-8 encoded") from exc

    reader = csv.reader(io.StringIO(text))
    try:
        raw_headers = next(reader)
    except StopIteration as exc:
        raise FocusError("EMPTY_FILE", "CSV is empty") from exc

    raw_headers = [header.strip() for header in raw_headers]
    if not any(raw_headers):
        raise FocusError("EMPTY_HEADER", "CSV header row is empty")

    mapping: dict[str, str | None] = {}
    seen: dict[str, str] = {}
    unknown: list[str] = []
    for raw_header in raw_headers:
        canonical = canonical_header(raw_header)
        mapping[raw_header] = canonical
        if canonical is None:
            unknown.append(raw_header)
            continue
        if canonical in seen:
            raise FocusError(
                "DUPLICATE_HEADER",
                f"Columns '{seen[canonical]}' and '{raw_header}' both map to {canonical}",
                column=canonical,
            )
        seen[canonical] = raw_header

    missing = sorted(MANDATORY_COLUMNS - set(seen))
    if require_all_required and missing:
        raise FocusError(
            "MISSING_REQUIRED_COLUMN",
            f"Missing required FOCUS columns: {', '.join(missing)}",
            columns=missing,
        )

    rows: list[FocusRow] = []
    for row_index, raw_row in enumerate(reader, start=2):
        if not raw_row or all(not cell.strip() for cell in raw_row):
            continue
        values: dict[str, object] = {}
        error = None
        if len(raw_row) != len(raw_headers):
            error = (
                f"Row has {len(raw_row)} fields; header has {len(raw_headers)}"
            )
            values["_parse_error"] = error
        else:
            extras: dict[str, str] = {}
            for raw_header, cell in zip(raw_headers, raw_row):
                canonical = mapping[raw_header]
                value = cell.strip()
                if canonical is None:
                    if value:
                        extras[raw_header] = value
                else:
                    values[canonical] = value
            if extras:
                values["_extra"] = extras
        rows.append(FocusRow(index=row_index, values=values, error=error))

    if not rows:
        raise FocusError("NO_DATA_ROWS", "CSV contains no data rows")

    return FocusFile(
        raw_headers=raw_headers,
        mapping=mapping,
        unknown_columns=unknown,
        rows=rows,
        missing_required=missing,
    )
