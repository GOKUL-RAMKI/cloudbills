"""Blueprint registration."""

from flask import Flask

from routes.billing import bp as billing_bp
from routes.budgets import bp as budgets_bp
from routes.dashboard import bp as dashboard_bp
from routes.meta import bp as meta_bp
from routes.recommendations import bp as recommendations_bp
from routes.resources import bp as resources_bp

BLUEPRINTS = [
    meta_bp,
    dashboard_bp,
    resources_bp,
    recommendations_bp,
    billing_bp,
    budgets_bp,
]


def register_blueprints(app: Flask) -> None:
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint, url_prefix="/api")
