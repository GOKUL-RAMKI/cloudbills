"""Small request helpers shared by the API blueprints."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import abort, request


def json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="A JSON object body is required")
    return data


def required(data: dict, *keys: str) -> None:
    missing = [key for key in keys if data.get(key) in (None, "")]
    if missing:
        abort(400, description=f"Missing required field(s): {', '.join(missing)}")


def actor_id(data: dict, key: str = "user_id", default: int = 1) -> int:
    value = data.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        abort(400, description=f"{key} must be an integer")


def int_arg(name: str, default: int | None = None, minimum: int | None = None,
            maximum: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        abort(400, description=f"Query parameter {name} must be an integer")
    if minimum is not None and value < minimum:
        abort(400, description=f"Query parameter {name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        value = maximum
    return value


def decimal_value(data: dict, key: str, required_field: bool = True) -> Decimal | None:
    raw = data.get(key)
    if raw in (None, ""):
        if required_field:
            abort(400, description=f"Missing required field: {key}")
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        abort(400, description=f"{key} must be a numeric value")
