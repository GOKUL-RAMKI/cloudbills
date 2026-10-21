-- =====================================================================
-- clouDBills — 04_functions.sql
-- Read-side business logic (all truth computed inside PostgreSQL).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Budget threshold evaluation. Called by the billing trigger and by the
-- API after budgets are created/updated.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION check_budget_threshold(p_budget_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    b         budgets%ROWTYPE;
    v_spend   NUMERIC(18,2);
    v_percent NUMERIC(6,2);
BEGIN
    SELECT * INTO b FROM budgets WHERE budget_id = p_budget_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT COALESCE(SUM(br.billed_cost), 0)
      INTO v_spend
      FROM billing_records br
     WHERE DATE_TRUNC('month', br.usage_start)::DATE = b.budget_month
       AND (
             (b.project_id IS NOT NULL AND br.project_id = b.project_id)
          OR (b.project_id IS NULL AND br.project_id IN (
                 SELECT pj.project_id FROM projects pj WHERE pj.team_id = b.team_id
             ))
       );

    v_percent := ROUND((v_spend / NULLIF(b.budget_amount, 0)) * 100, 2);

    IF v_percent >= b.warning_percentage THEN
        IF NOT EXISTS (
            SELECT 1 FROM budget_alerts ba
            WHERE ba.budget_id = b.budget_id
              AND ba.is_acknowledged = FALSE
        ) THEN
            INSERT INTO budget_alerts (
                budget_id, threshold_percentage_reached, current_spend, budget_amount
            ) VALUES (
                b.budget_id, LEAST(v_percent, 100), v_spend, b.budget_amount
            );
        END IF;
    END IF;
END;
$$;


-- ---------------------------------------------------------------------
-- Monthly spend for one project.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION monthly_project_cost(p_project_id BIGINT, p_month DATE)
RETURNS NUMERIC(18,6)
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(SUM(billed_cost), 0)::NUMERIC(18,6)
    FROM billing_records
    WHERE project_id = p_project_id
      AND usage_start >= DATE_TRUNC('month', p_month)
      AND usage_start <  DATE_TRUNC('month', p_month) + INTERVAL '1 month';
$$;


-- ---------------------------------------------------------------------
-- Low CPU waste detector (rule-driven, 21-day lookback by default).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION detect_idle_compute()
RETURNS TABLE (
    resource_id              BIGINT,
    resource_name            VARCHAR,
    project_id               BIGINT,
    project_name             VARCHAR,
    average_cpu_percent      NUMERIC,
    peak_cpu_percent         NUMERIC,
    sample_count             BIGINT,
    estimated_monthly_waste  NUMERIC
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_threshold NUMERIC := 5.0;
    v_days      INT := 21;
    v_fraction  NUMERIC := 0.8;
BEGIN
    SELECT
        COALESCE((parameters->>'threshold_cpu_percent')::NUMERIC, 5.0),
        COALESCE((parameters->>'lookback_days')::INT, 21),
        COALESCE((parameters->>'min_telemetry_fraction')::NUMERIC, 0.8)
      INTO v_threshold, v_days, v_fraction
      FROM optimization_rules
     WHERE rule_type = 'idle_compute'
       AND is_active = TRUE
     ORDER BY rule_id
     LIMIT 1;

    RETURN QUERY
    SELECT
        r.resource_id,
        r.resource_name,
        r.project_id,
        p.project_name,
        ROUND(AVG(um.metric_value), 2),
        ROUND(MAX(um.metric_value), 2),
        COUNT(um.metric_id),
        ROUND(r.hourly_cost * 730, 2)
    FROM resources r
    JOIN projects p ON r.project_id = p.project_id
    JOIN environments e ON p.environment_id = e.environment_id
    JOIN usage_metrics um ON um.resource_id = r.resource_id
    JOIN metric_types mt ON um.metric_type_id = mt.metric_type_id
    WHERE mt.metric_name = 'cpu_utilization'
      AND r.status = 'active'
      AND e.is_production_grade = FALSE
      AND um.measured_at >= CURRENT_TIMESTAMP - MAKE_INTERVAL(days => v_days)
      AND NOT EXISTS (
            SELECT 1 FROM recommendations rec
            WHERE rec.resource_id = r.resource_id
              AND rec.status IN ('detected', 'under_review', 'approved')
      )
    GROUP BY r.resource_id, r.resource_name, r.project_id, p.project_name, r.hourly_cost
    HAVING AVG(um.metric_value) < v_threshold
       AND COUNT(um.metric_id) >= (v_days * 24 * v_fraction)
    ORDER BY r.hourly_cost DESC;
END;
$$;


-- ---------------------------------------------------------------------
-- Recursive dependency traversal (upstream parents that block deletion).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dependency_chain(p_resource_id BIGINT)
RETURNS TABLE (
    depth_level          INT,
    dependency_type      VARCHAR,
    dependent_parent_id  BIGINT,
    parent_name          VARCHAR,
    parent_status        VARCHAR
)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE dependency_chain AS (
        SELECT
            rd.parent_resource_id,
            rd.child_resource_id,
            rd.dependency_type,
            1 AS depth_level
        FROM resource_dependencies rd
        WHERE rd.child_resource_id = p_resource_id

        UNION ALL

        SELECT
            rd.parent_resource_id,
            rd.child_resource_id,
            rd.dependency_type,
            dc.depth_level + 1
        FROM resource_dependencies rd
        JOIN dependency_chain dc ON rd.child_resource_id = dc.parent_resource_id
        WHERE dc.depth_level < 10
    )
    SELECT
        dc.depth_level,
        dc.dependency_type,
        r.resource_id,
        r.resource_name,
        r.status
    FROM dependency_chain dc
    JOIN resources r ON dc.parent_resource_id = r.resource_id
    ORDER BY dc.depth_level;
$$;


-- ---------------------------------------------------------------------
-- Storage resources with no active parent attachment.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION orphaned_volumes()
RETURNS TABLE (
    resource_id             BIGINT,
    resource_name           VARCHAR,
    project_id              BIGINT,
    project_name            VARCHAR,
    region_code             VARCHAR,
    status                  VARCHAR,
    hourly_cost             NUMERIC,
    estimated_monthly_waste NUMERIC
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        r.resource_id,
        r.resource_name,
        r.project_id,
        p.project_name,
        rg.region_code,
        r.status,
        r.hourly_cost,
        ROUND(r.hourly_cost * 730, 2)
    FROM resources r
    JOIN resource_types rt ON r.type_id = rt.type_id
    JOIN regions rg ON r.region_id = rg.region_id
    LEFT JOIN projects p ON r.project_id = p.project_id
    WHERE rt.generic_category = 'storage'
      AND r.status IN ('active', 'stopped')
      AND NOT EXISTS (
            SELECT 1
            FROM resource_dependencies rd
            JOIN resources parent ON parent.resource_id = rd.parent_resource_id
            WHERE rd.child_resource_id = r.resource_id
              AND parent.status = 'active'
      )
    ORDER BY r.hourly_cost DESC;
$$;


-- ---------------------------------------------------------------------
-- Active resources missing one or more mandatory FinOps tags.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION missing_tags()
RETURNS TABLE (
    resource_id   BIGINT,
    resource_name VARCHAR,
    project_id    BIGINT,
    project_name  VARCHAR,
    missing_keys  TEXT[]
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        r.resource_id,
        r.resource_name,
        r.project_id,
        p.project_name,
        ARRAY(
            SELECT k
            FROM UNNEST(ARRAY['owner', 'project', 'environment']) AS k
            WHERE NOT EXISTS (
                SELECT 1 FROM resource_tags t
                WHERE t.resource_id = r.resource_id
                  AND t.tag_key = k
            )
        ) AS missing_keys
    FROM resources r
    LEFT JOIN projects p ON r.project_id = p.project_id
    WHERE r.status = 'active'
      AND EXISTS (
            SELECT 1
            FROM UNNEST(ARRAY['owner', 'project', 'environment']) AS k
            WHERE NOT EXISTS (
                SELECT 1 FROM resource_tags t
                WHERE t.resource_id = r.resource_id
                  AND t.tag_key = k
            )
      )
    ORDER BY r.resource_id;
$$;


-- ---------------------------------------------------------------------
-- Single JSON payload for the dashboard.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_dashboard_summary()
RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_result JSONB;
BEGIN
    SELECT JSONB_BUILD_OBJECT(
        'total_projects',        (SELECT COUNT(*) FROM projects WHERE is_active),
        'total_resources',       (SELECT COUNT(*) FROM resources),
        'active_resources',      (SELECT COUNT(*) FROM resources WHERE status = 'active'),
        'monthly_spend',         (SELECT COALESCE(SUM(billed_cost), 0)
                                    FROM billing_records
                                   WHERE DATE_TRUNC('month', usage_start)::DATE = DATE_TRUNC('month', CURRENT_DATE)::DATE),
        'previous_month_spend',  (SELECT COALESCE(SUM(billed_cost), 0)
                                    FROM billing_records
                                   WHERE DATE_TRUNC('month', usage_start)::DATE = (DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month')::DATE),
        'estimated_savings',     (SELECT COALESCE(SUM(estimated_monthly_saving), 0)
                                    FROM recommendations
                                   WHERE status IN ('detected', 'under_review', 'approved', 'executed')),
        'realized_savings',      (SELECT COALESCE(SUM(actual_saving), 0) FROM savings_realization),
        'pending_approvals',     (SELECT COUNT(*) FROM approval_requests WHERE decision IS NULL),
        'open_alerts',           (SELECT COUNT(*) FROM budget_alerts WHERE is_acknowledged = FALSE),
        'idle_candidates',       (SELECT COUNT(*) FROM detect_idle_compute()),
        'orphaned_volumes',      (SELECT COUNT(*) FROM orphaned_volumes()),
        'missing_tags',          (SELECT COUNT(*) FROM missing_tags()),
        'recommendations_by_status',
            (SELECT COALESCE(JSONB_OBJECT_AGG(status, cnt), '{}'::JSONB)
               FROM (SELECT status, COUNT(*) AS cnt FROM recommendations GROUP BY status) s),
        'spend_by_month',
            (SELECT COALESCE(JSONB_AGG(JSONB_BUILD_OBJECT('month', month_key, 'amount', amount) ORDER BY month_key), '[]'::JSONB)
               FROM (SELECT TO_CHAR(DATE_TRUNC('month', usage_start), 'YYYY-MM') AS month_key,
                            SUM(billed_cost) AS amount
                       FROM billing_records
                      GROUP BY 1) s),
        'top_projects',
            (SELECT COALESCE(JSONB_AGG(JSONB_BUILD_OBJECT(
                        'project_id', project_id,
                        'project_name', project_name,
                        'team_name', team_name,
                        'amount', amount) ORDER BY amount DESC), '[]'::JSONB)
               FROM (SELECT b.project_id, p.project_name, t.team_name, SUM(b.billed_cost) AS amount
                       FROM billing_records b
                       JOIN projects p ON b.project_id = p.project_id
                       JOIN teams t ON p.team_id = t.team_id
                      WHERE DATE_TRUNC('month', b.usage_start)::DATE = DATE_TRUNC('month', CURRENT_DATE)::DATE
                      GROUP BY b.project_id, p.project_name, t.team_name
                      ORDER BY amount DESC
                      LIMIT 5) s),
        'recent_recommendations',
            (SELECT COALESCE(JSONB_AGG(JSONB_BUILD_OBJECT(
                        'recommendation_id', recommendation_id,
                        'resource_name', resource_name,
                        'project_name', project_name,
                        'recommendation_type', recommendation_type,
                        'estimated_monthly_saving', estimated_monthly_saving,
                        'risk_level', risk_level,
                        'status', status,
                        'detected_at', detected_at) ORDER BY detected_at DESC), '[]'::JSONB)
               FROM (SELECT * FROM v_recommendation_queue ORDER BY detected_at DESC LIMIT 8) s)
    ) INTO v_result;

    RETURN v_result;
END;
$$;


-- ---------------------------------------------------------------------
-- Shared multi-tenant cost allocation (materialised by the seed/API).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION shared_cost_allocation(p_month DATE)
RETURNS TABLE (
    spend_month        DATE,
    resource_id        BIGINT,
    shared_infra_name  VARCHAR,
    project_id         BIGINT,
    consumer_project   VARCHAR,
    responsible_team   VARCHAR,
    allocation_percentage NUMERIC,
    allocated_amount   NUMERIC
)
LANGUAGE sql
STABLE
AS $$
    WITH raw_shared_spend AS (
        SELECT
            b.resource_id,
            DATE_TRUNC('month', b.usage_start)::DATE AS spend_month,
            SUM(b.billed_cost) AS total_shared_cost
        FROM billing_records b
        JOIN resources r ON b.resource_id = r.resource_id
        WHERE r.project_id IS NULL
          AND DATE_TRUNC('month', b.usage_start)::DATE = DATE_TRUNC('month', p_month)::DATE
        GROUP BY b.resource_id, DATE_TRUNC('month', b.usage_start)
    )
    SELECT
        rss.spend_month,
        rss.resource_id,
        r.resource_name,
        p.project_id,
        p.project_name,
        t.team_name,
        rc.allocation_percentage,
        ROUND(rss.total_shared_cost * (rc.allocation_percentage / 100.0), 2) AS allocated_amount
    FROM raw_shared_spend rss
    JOIN resources r ON rss.resource_id = r.resource_id
    JOIN resource_consumers rc ON r.resource_id = rc.resource_id
    JOIN projects p ON rc.project_id = p.project_id
    JOIN teams t ON p.team_id = t.team_id
    ORDER BY rss.spend_month DESC, allocated_amount DESC;
$$;
