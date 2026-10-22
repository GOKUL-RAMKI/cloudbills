-- =====================================================================
-- clouDBills — 02_indexes.sql
-- Extra candidate-key enforcement and query-path indexes.
-- =====================================================================

-- A budget belongs to exactly one project OR one team for a given month.
CREATE UNIQUE INDEX uq_budgets_project_month
    ON budgets(project_id, budget_month)
    WHERE project_id IS NOT NULL;

CREATE UNIQUE INDEX uq_budgets_team_month
    ON budgets(team_id, budget_month)
    WHERE project_id IS NULL AND team_id IS NOT NULL;

-- A physical file can only be imported once.
CREATE UNIQUE INDEX uq_billing_import_checksum
    ON billing_import_batches(file_checksum)
    WHERE file_checksum IS NOT NULL;

-- FOCUS line-item idempotency. The partition key is part of the index.
CREATE UNIQUE INDEX uq_billing_focus_fingerprint
    ON billing_records(account_id, source_row_fingerprint, usage_start);

CREATE INDEX idx_billing_invoice
    ON billing_records(invoice_id, invoice_detail_id);

CREATE INDEX idx_billing_charge_category
    ON billing_records(charge_category, usage_start);

-- Time-series lookups used by idle detection and rollups.
CREATE INDEX idx_usage_metrics_type_time
    ON usage_metrics(metric_type_id, measured_at);

-- Workflow queue and detail lookups.
CREATE INDEX idx_recommendations_status
    ON recommendations(status);

CREATE INDEX idx_approval_requests_recommendation
    ON approval_requests(recommendation_id);

CREATE INDEX idx_recommendation_evidence_rec
    ON recommendation_evidence(recommendation_id);

CREATE INDEX idx_recommendation_history_rec
    ON recommendation_status_history(recommendation_id);

CREATE INDEX idx_recommendation_actions_rec
    ON recommendation_actions(recommendation_id);

CREATE INDEX idx_resources_project_status
    ON resources(project_id, status);

CREATE INDEX idx_resources_type
    ON resources(type_id);
