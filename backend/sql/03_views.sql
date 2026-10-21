-- =====================================================================
-- clouDBills — 03_views.sql
-- Read models: aggregation cache, inventory, workflow queue, budgets.
-- =====================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_monthly_project_cost CASCADE;

CREATE MATERIALIZED VIEW mv_monthly_project_cost AS
SELECT
    p.project_id,
    p.project_name,
    t.team_name,
    DATE_TRUNC('month', b.usage_start)::DATE AS billing_month,
    b.currency_code,
    COUNT(DISTINCT b.resource_id) AS distinct_resources_billed,
    SUM(b.billed_cost) AS total_aggregate_cost
FROM billing_records b
JOIN projects p ON b.project_id = p.project_id
JOIN teams t ON p.team_id = t.team_id
GROUP BY p.project_id, p.project_name, t.team_name,
         DATE_TRUNC('month', b.usage_start), b.currency_code;

-- Required for REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX idx_mv_monthly_project_cost_pk
    ON mv_monthly_project_cost(project_id, billing_month, currency_code);

CREATE INDEX idx_mv_monthly_project_cost_month
    ON mv_monthly_project_cost(billing_month);

CREATE OR REPLACE VIEW v_resource_inventory AS
SELECT
    r.resource_id,
    r.external_resource_id,
    r.resource_name,
    r.status,
    r.hourly_cost,
    r.created_at,
    r.deleted_at,
    r.account_id,
    a.account_name,
    a.external_account_id,
    a.currency_code,
    cp.provider_id,
    cp.provider_name,
    r.project_id,
    p.project_name,
    t.team_id,
    t.team_name,
    r.type_id,
    rt.raw_type_name,
    rt.generic_category,
    rt.pricing_unit,
    r.region_id,
    rg.region_code,
    rg.region_name,
    COALESCE(tg.tags, '{}'::JSONB) AS tags
FROM resources r
JOIN cloud_accounts a ON r.account_id = a.account_id
JOIN cloud_providers cp ON a.provider_id = cp.provider_id
LEFT JOIN projects p ON r.project_id = p.project_id
LEFT JOIN teams t ON p.team_id = t.team_id
JOIN resource_types rt ON r.type_id = rt.type_id
JOIN regions rg ON r.region_id = rg.region_id
LEFT JOIN LATERAL (
    SELECT JSONB_OBJECT_AGG(rtag.tag_key, rtag.tag_value) AS tags
    FROM resource_tags rtag
    WHERE rtag.resource_id = r.resource_id
) tg ON TRUE;

CREATE OR REPLACE VIEW v_recommendation_queue AS
SELECT
    rec.recommendation_id,
    rec.rule_id,
    rule.rule_name,
    rule.rule_type,
    rule.severity,
    rec.resource_id,
    r.resource_name,
    r.external_resource_id,
    r.status AS resource_status,
    rec.project_id,
    p.project_name,
    t.team_name,
    rec.recommendation_type,
    rec.problem_description,
    rec.suggested_action,
    rec.estimated_monthly_saving,
    rec.risk_level,
    rec.status,
    rec.detected_at,
    rec.expires_at,
    appr.approval_id AS pending_approval_id
FROM recommendations rec
JOIN optimization_rules rule ON rec.rule_id = rule.rule_id
JOIN resources r ON rec.resource_id = r.resource_id
JOIN projects p ON rec.project_id = p.project_id
JOIN teams t ON p.team_id = t.team_id
LEFT JOIN LATERAL (
    SELECT ar.approval_id
    FROM approval_requests ar
    WHERE ar.recommendation_id = rec.recommendation_id
      AND ar.decision IS NULL
    LIMIT 1
) appr ON TRUE;

CREATE OR REPLACE VIEW v_budget_utilization AS
SELECT
    b.budget_id,
    b.project_id,
    p.project_name,
    b.team_id,
    t.team_name,
    b.budget_month,
    b.budget_amount,
    b.warning_percentage,
    COALESCE(s.current_spend, 0)::NUMERIC(18,2) AS current_spend,
    ROUND(COALESCE(s.current_spend, 0) / NULLIF(b.budget_amount, 0) * 100, 2) AS utilization_percentage,
    CASE
        WHEN COALESCE(s.current_spend, 0) >= b.budget_amount THEN 'over_budget'
        WHEN COALESCE(s.current_spend, 0) >= b.budget_amount * b.warning_percentage / 100 THEN 'warning'
        ELSE 'healthy'
    END AS budget_status
FROM budgets b
LEFT JOIN projects p ON b.project_id = p.project_id
LEFT JOIN teams t ON b.team_id = t.team_id
LEFT JOIN LATERAL (
    SELECT SUM(br.billed_cost) AS current_spend
    FROM billing_records br
    WHERE DATE_TRUNC('month', br.usage_start)::DATE = b.budget_month
      AND (
            (b.project_id IS NOT NULL AND br.project_id = b.project_id)
         OR (b.project_id IS NULL AND br.project_id IN (
                SELECT pj.project_id FROM projects pj WHERE pj.team_id = b.team_id
            ))
      )
) s ON TRUE;
