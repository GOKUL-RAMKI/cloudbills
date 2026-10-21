-- =====================================================================
-- clouDBills — 05_procedures.sql
-- Write-side stored programs. Every UI mutation goes through one of these.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Resource CRUD
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION add_resource(
    p_account_id           BIGINT,
    p_project_id           BIGINT,
    p_type_id              INT,
    p_region_id            INT,
    p_external_resource_id TEXT,
    p_resource_name        TEXT,
    p_status               TEXT DEFAULT 'active',
    p_hourly_cost          NUMERIC DEFAULT 0,
    p_tags                 JSONB DEFAULT '{}'::JSONB,
    p_actor_id             BIGINT DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_resource_id BIGINT;
    k TEXT;
    v TEXT;
BEGIN
    IF p_status NOT IN ('active', 'stopped', 'deleted', 'unknown') THEN
        RAISE EXCEPTION 'Invalid resource status: %', p_status USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_actor_id::TEXT, ''), TRUE);

    INSERT INTO resources (
        account_id, project_id, type_id, region_id,
        external_resource_id, resource_name, status, hourly_cost,
        deleted_at
    ) VALUES (
        p_account_id, p_project_id, p_type_id, p_region_id,
        p_external_resource_id, p_resource_name, p_status, p_hourly_cost,
        CASE WHEN p_status = 'deleted' THEN NOW() ELSE NULL END
    )
    RETURNING resource_id INTO v_resource_id;

    IF p_tags IS NOT NULL THEN
        FOR k, v IN SELECT key, value FROM JSONB_EACH_TEXT(p_tags) LOOP
            IF TRIM(k) <> '' THEN
                INSERT INTO resource_tags (resource_id, tag_key, tag_value)
                VALUES (v_resource_id, TRIM(k), v);
            END IF;
        END LOOP;
    END IF;

    INSERT INTO resource_status_history (resource_id, previous_status, new_status, reason)
    VALUES (v_resource_id, NULL, p_status, 'Resource registered');

    RETURN v_resource_id;
END;
$$;


CREATE OR REPLACE PROCEDURE update_resource(
    p_resource_id   BIGINT,
    p_project_id    BIGINT,
    p_type_id       INT,
    p_region_id     INT,
    p_resource_name TEXT,
    p_status        TEXT,
    p_hourly_cost   NUMERIC,
    p_tags          JSONB DEFAULT NULL,
    p_actor_id      BIGINT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_old_status TEXT;
    k TEXT;
    v TEXT;
BEGIN
    SELECT status INTO v_old_status
    FROM resources
    WHERE resource_id = p_resource_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Resource % not found', p_resource_id USING ERRCODE = 'P0002';
    END IF;

    IF p_status NOT IN ('active', 'stopped', 'deleted', 'unknown') THEN
        RAISE EXCEPTION 'Invalid resource status: %', p_status USING ERRCODE = '22023';
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_actor_id::TEXT, ''), TRUE);
    PERFORM set_config('app.change_reason', 'Resource updated via UI', TRUE);

    UPDATE resources
       SET project_id     = p_project_id,
           type_id        = p_type_id,
           region_id      = p_region_id,
           resource_name  = p_resource_name,
           status         = p_status,
           hourly_cost    = p_hourly_cost,
           deleted_at     = CASE
                                WHEN p_status = 'deleted' THEN COALESCE(deleted_at, NOW())
                                ELSE NULL
                            END
     WHERE resource_id = p_resource_id;

    IF p_tags IS NOT NULL THEN
        DELETE FROM resource_tags WHERE resource_id = p_resource_id;
        FOR k, v IN SELECT key, value FROM JSONB_EACH_TEXT(p_tags) LOOP
            IF TRIM(k) <> '' THEN
                INSERT INTO resource_tags (resource_id, tag_key, tag_value)
                VALUES (p_resource_id, TRIM(k), v);
            END IF;
        END LOOP;
    END IF;
END;
$$;


CREATE OR REPLACE PROCEDURE delete_resource(
    p_resource_id BIGINT,
    p_actor_id    BIGINT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_status TEXT;
BEGIN
    SELECT status INTO v_status
    FROM resources
    WHERE resource_id = p_resource_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Resource % not found', p_resource_id USING ERRCODE = 'P0002';
    END IF;

    IF v_status = 'deleted' THEN
        RAISE EXCEPTION 'Resource % is already deleted', p_resource_id USING ERRCODE = 'P0002';
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_actor_id::TEXT, ''), TRUE);
    PERFORM set_config('app.change_reason', 'Soft-deleted via UI', TRUE);

    UPDATE resources
       SET status = 'deleted',
           deleted_at = NOW()
     WHERE resource_id = p_resource_id;
END;
$$;


CREATE OR REPLACE PROCEDURE add_resource_tag(
    p_resource_id BIGINT,
    p_tag_key     TEXT,
    p_tag_value   TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO resource_tags (resource_id, tag_key, tag_value)
    VALUES (p_resource_id, TRIM(p_tag_key), p_tag_value)
    ON CONFLICT (resource_id, tag_key)
    DO UPDATE SET tag_value = EXCLUDED.tag_value;
END;
$$;


CREATE OR REPLACE PROCEDURE delete_resource_tag(
    p_resource_id BIGINT,
    p_tag_key     TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    DELETE FROM resource_tags
    WHERE resource_id = p_resource_id
      AND tag_key = TRIM(p_tag_key);

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Tag % not found on resource %', p_tag_key, p_resource_id USING ERRCODE = 'P0002';
    END IF;
END;
$$;


-- ---------------------------------------------------------------------
-- Budget CRUD
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_budget(
    p_project_id      BIGINT,
    p_team_id         BIGINT,
    p_budget_month    DATE,
    p_budget_amount   NUMERIC,
    p_warning_percent NUMERIC DEFAULT 80,
    p_actor_id        BIGINT DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_budget_id BIGINT;
BEGIN
    IF (p_project_id IS NULL) = (p_team_id IS NULL) THEN
        RAISE EXCEPTION 'A budget must target exactly one project or one team' USING ERRCODE = '23514';
    END IF;

    IF p_budget_amount IS NULL OR p_budget_amount <= 0 THEN
        RAISE EXCEPTION 'Budget amount must be greater than zero' USING ERRCODE = '23514';
    END IF;

    IF p_warning_percent IS NULL OR p_warning_percent <= 0 OR p_warning_percent > 100 THEN
        RAISE EXCEPTION 'Warning percentage must be between 0 and 100' USING ERRCODE = '23514';
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_actor_id::TEXT, ''), TRUE);

    INSERT INTO budgets (project_id, team_id, budget_month, budget_amount, warning_percentage)
    VALUES (p_project_id, p_team_id, DATE_TRUNC('month', p_budget_month)::DATE,
            p_budget_amount, p_warning_percent)
    RETURNING budget_id INTO v_budget_id;

    PERFORM check_budget_threshold(v_budget_id);
    RETURN v_budget_id;
END;
$$;


CREATE OR REPLACE PROCEDURE update_budget(
    p_budget_id       BIGINT,
    p_budget_amount   NUMERIC,
    p_warning_percent NUMERIC,
    p_actor_id        BIGINT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_budget_amount IS NULL OR p_budget_amount <= 0 THEN
        RAISE EXCEPTION 'Budget amount must be greater than zero' USING ERRCODE = '23514';
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_actor_id::TEXT, ''), TRUE);

    UPDATE budgets
       SET budget_amount = p_budget_amount,
           warning_percentage = p_warning_percent
     WHERE budget_id = p_budget_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Budget % not found', p_budget_id USING ERRCODE = 'P0002';
    END IF;

    PERFORM check_budget_threshold(p_budget_id);
END;
$$;


CREATE OR REPLACE PROCEDURE delete_budget(
    p_budget_id BIGINT,
    p_actor_id  BIGINT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM set_config('app.current_user_id', COALESCE(p_actor_id::TEXT, ''), TRUE);

    DELETE FROM budgets WHERE budget_id = p_budget_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Budget % not found', p_budget_id USING ERRCODE = 'P0002';
    END IF;
END;
$$;


CREATE OR REPLACE PROCEDURE acknowledge_budget_alert(
    p_alert_id BIGINT,
    p_actor_id BIGINT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM set_config('app.current_user_id', COALESCE(p_actor_id::TEXT, ''), TRUE);

    UPDATE budget_alerts
       SET is_acknowledged = TRUE
     WHERE alert_id = p_alert_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Budget alert % not found', p_alert_id USING ERRCODE = 'P0002';
    END IF;
END;
$$;


-- ---------------------------------------------------------------------
-- Approval workflow
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE submit_recommendation_for_review(
    p_recommendation_id BIGINT,
    p_user_id           BIGINT,
    p_comments          TEXT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_status TEXT;
BEGIN
    SELECT status INTO v_status
    FROM recommendations
    WHERE recommendation_id = p_recommendation_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Recommendation % not found', p_recommendation_id USING ERRCODE = 'P0002';
    END IF;

    IF v_status <> 'detected' THEN
        RAISE EXCEPTION 'Recommendation % is in state "%", expected "detected"',
            p_recommendation_id, v_status USING ERRCODE = 'P0001';
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_user_id::TEXT, ''), TRUE);
    PERFORM set_config('app.change_reason', COALESCE(p_comments, 'Submitted for review'), TRUE);

    INSERT INTO approval_requests (recommendation_id, requested_by, decision)
    VALUES (p_recommendation_id, p_user_id, NULL);

    UPDATE recommendations
       SET status = 'under_review'
     WHERE recommendation_id = p_recommendation_id;
END;
$$;


CREATE OR REPLACE PROCEDURE approve_optimization_recommendation(
    p_recommendation_id BIGINT,
    p_admin_user_id     BIGINT,
    p_comments          TEXT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_status TEXT;
BEGIN
    -- Pessimistic lock: a second concurrent approval waits here, then sees
    -- the committed 'approved' state and aborts with "Concurrency Conflict".
    SELECT status INTO v_status
    FROM recommendations
    WHERE recommendation_id = p_recommendation_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Recommendation % not found', p_recommendation_id USING ERRCODE = 'P0002';
    END IF;

    IF v_status NOT IN ('under_review', 'detected') THEN
        RAISE EXCEPTION 'Concurrency Conflict: Recommendation is currently in state "%". Expected "under_review".',
            v_status USING ERRCODE = 'P0001';
    END IF;

    PERFORM 1
    FROM approval_requests
    WHERE recommendation_id = p_recommendation_id
      AND decision IS NULL
    FOR UPDATE;

    IF FOUND THEN
        UPDATE approval_requests
           SET reviewed_by = p_admin_user_id,
               decision = 'approved',
               comments = p_comments,
               reviewed_at = CURRENT_TIMESTAMP
         WHERE recommendation_id = p_recommendation_id
           AND decision IS NULL;
    ELSIF v_status = 'detected' THEN
        INSERT INTO approval_requests (
            recommendation_id, requested_by, reviewed_by, decision, comments, reviewed_at
        ) VALUES (
            p_recommendation_id, p_admin_user_id, p_admin_user_id, 'approved', p_comments, CURRENT_TIMESTAMP
        );
    ELSE
        RAISE EXCEPTION 'No pending approval request exists for recommendation %', p_recommendation_id
            USING ERRCODE = 'P0002';
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_admin_user_id::TEXT, ''), TRUE);
    PERFORM set_config('app.change_reason', COALESCE(p_comments, 'Approved via UI'), TRUE);

    UPDATE recommendations
       SET status = 'approved'
     WHERE recommendation_id = p_recommendation_id;
END;
$$;


CREATE OR REPLACE PROCEDURE reject_optimization_recommendation(
    p_recommendation_id BIGINT,
    p_admin_user_id     BIGINT,
    p_comments          TEXT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_status TEXT;
BEGIN
    SELECT status INTO v_status
    FROM recommendations
    WHERE recommendation_id = p_recommendation_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Recommendation % not found', p_recommendation_id USING ERRCODE = 'P0002';
    END IF;

    IF v_status NOT IN ('under_review', 'detected') THEN
        RAISE EXCEPTION 'Concurrency Conflict: Recommendation is currently in state "%". Expected "under_review".',
            v_status USING ERRCODE = 'P0001';
    END IF;

    PERFORM 1
    FROM approval_requests
    WHERE recommendation_id = p_recommendation_id
      AND decision IS NULL
    FOR UPDATE;

    IF FOUND THEN
        UPDATE approval_requests
           SET reviewed_by = p_admin_user_id,
               decision = 'rejected',
               comments = p_comments,
               reviewed_at = CURRENT_TIMESTAMP
         WHERE recommendation_id = p_recommendation_id
           AND decision IS NULL;
    ELSE
        INSERT INTO approval_requests (
            recommendation_id, requested_by, reviewed_by, decision, comments, reviewed_at
        ) VALUES (
            p_recommendation_id, p_admin_user_id, p_admin_user_id, 'rejected', p_comments, CURRENT_TIMESTAMP
        );
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_admin_user_id::TEXT, ''), TRUE);
    PERFORM set_config('app.change_reason', COALESCE(p_comments, 'Rejected via UI'), TRUE);

    UPDATE recommendations
       SET status = 'rejected'
     WHERE recommendation_id = p_recommendation_id;
END;
$$;


CREATE OR REPLACE PROCEDURE execute_recommendation_action(
    p_recommendation_id BIGINT,
    p_user_id           BIGINT,
    p_action_type       TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_status      TEXT;
    v_resource_id BIGINT;
BEGIN
    IF p_action_type NOT IN (
        'stop_instance', 'terminate_instance', 'detach_volume',
        'delete_volume', 'release_ip', 'start_instance', 'resize_instance'
    ) THEN
        RAISE EXCEPTION 'Unsupported action type: %', p_action_type USING ERRCODE = '22023';
    END IF;

    SELECT status, resource_id
      INTO v_status, v_resource_id
      FROM recommendations
     WHERE recommendation_id = p_recommendation_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Recommendation % not found', p_recommendation_id USING ERRCODE = 'P0002';
    END IF;

    IF v_status <> 'approved' THEN
        RAISE EXCEPTION 'Concurrency Conflict: Recommendation is currently in state "%". Expected "approved".',
            v_status USING ERRCODE = 'P0001';
    END IF;

    PERFORM set_config('app.current_user_id', COALESCE(p_user_id::TEXT, ''), TRUE);
    PERFORM set_config('app.change_reason', 'Remediation action executed', TRUE);

    INSERT INTO recommendation_actions (
        recommendation_id, executed_by, action_type, execution_status,
        started_at, completed_at
    ) VALUES (
        p_recommendation_id, p_user_id, p_action_type, 'succeeded',
        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    );

    IF p_action_type IN ('stop_instance') THEN
        UPDATE resources SET status = 'stopped', deleted_at = NULL
        WHERE resource_id = v_resource_id AND status <> 'deleted';
    ELSIF p_action_type IN ('terminate_instance', 'delete_volume') THEN
        UPDATE resources SET status = 'deleted', deleted_at = COALESCE(deleted_at, NOW())
        WHERE resource_id = v_resource_id;
    ELSIF p_action_type = 'detach_volume' THEN
        DELETE FROM resource_dependencies WHERE child_resource_id = v_resource_id;
    ELSIF p_action_type = 'release_ip' THEN
        DELETE FROM resource_dependencies WHERE child_resource_id = v_resource_id;
        UPDATE resources SET status = 'deleted', deleted_at = COALESCE(deleted_at, NOW())
        WHERE resource_id = v_resource_id;
    END IF;

    UPDATE recommendations
       SET status = 'executed'
     WHERE recommendation_id = p_recommendation_id;
END;
$$;


CREATE OR REPLACE PROCEDURE record_savings(
    p_recommendation_id BIGINT,
    p_baseline_cost     NUMERIC,
    p_actual_cost       NUMERIC,
    p_start             DATE,
    p_end               DATE,
    p_user_id           BIGINT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_status     TEXT;
    v_estimated  NUMERIC(18,2);
    v_actual_sav NUMERIC(18,2);
    v_percent    NUMERIC(6,2);
BEGIN
    IF p_end <= p_start THEN
        RAISE EXCEPTION 'Measurement end must be after start' USING ERRCODE = '23514';
    END IF;

    SELECT status, estimated_monthly_saving
      INTO v_status, v_estimated
      FROM recommendations
     WHERE recommendation_id = p_recommendation_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Recommendation % not found', p_recommendation_id USING ERRCODE = 'P0002';
    END IF;

    IF v_status NOT IN ('approved', 'executed') THEN
        RAISE EXCEPTION 'Recommendation % is in state "%", expected "executed"',
            p_recommendation_id, v_status USING ERRCODE = 'P0001';
    END IF;

    v_actual_sav := p_baseline_cost - p_actual_cost;
    v_percent := ROUND(v_actual_sav / NULLIF(v_estimated, 0) * 100, 2);

    PERFORM set_config('app.current_user_id', COALESCE(p_user_id::TEXT, ''), TRUE);
    PERFORM set_config('app.change_reason', 'Savings measured and validated', TRUE);

    INSERT INTO savings_realization (
        recommendation_id, baseline_monthly_cost, actual_monthly_cost,
        estimated_saving, actual_saving, realization_percentage,
        measurement_start, measurement_end
    ) VALUES (
        p_recommendation_id, p_baseline_cost, p_actual_cost,
        v_estimated, v_actual_sav, v_percent,
        p_start, p_end
    )
    ON CONFLICT (recommendation_id) DO UPDATE
       SET baseline_monthly_cost = EXCLUDED.baseline_monthly_cost,
           actual_monthly_cost = EXCLUDED.actual_monthly_cost,
           estimated_saving = EXCLUDED.estimated_saving,
           actual_saving = EXCLUDED.actual_saving,
           realization_percentage = EXCLUDED.realization_percentage,
           measurement_start = EXCLUDED.measurement_start,
           measurement_end = EXCLUDED.measurement_end,
           calculated_at = CURRENT_TIMESTAMP;

    UPDATE recommendations
       SET status = 'validated'
     WHERE recommendation_id = p_recommendation_id;
END;
$$;


-- ---------------------------------------------------------------------
-- ELT: validate a staging batch, quarantine bad rows, load good ones.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION validate_billing_batch(p_batch_id BIGINT)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_accepted       INT := 0;
    v_rejected       INT := 0;
    v_row            RECORD;
    v_batch_account  BIGINT;
    v_account_id     BIGINT;
    v_resource_id    BIGINT;
    v_project_id     BIGINT;
    v_start          TIMESTAMPTZ;
    v_end            TIMESTAMPTZ;
    v_qty            NUMERIC(18,6);
    v_cost           NUMERIC(18,6);
    v_currency       CHAR(3);
BEGIN
    SELECT account_id INTO v_batch_account
    FROM billing_import_batches
    WHERE batch_id = p_batch_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Billing batch % not found', p_batch_id USING ERRCODE = 'P0002';
    END IF;

    UPDATE billing_import_batches SET status = 'validating' WHERE batch_id = p_batch_id;

    FOR v_row IN
        SELECT * FROM billing_staging
        WHERE batch_id = p_batch_id
        ORDER BY staging_id
    LOOP
        BEGIN
            v_start := v_row.usage_start_text::TIMESTAMPTZ;
            v_end := v_row.usage_end_text::TIMESTAMPTZ;
            v_qty := v_row.usage_quantity_text::NUMERIC(18,6);
            v_cost := v_row.billed_cost_text::NUMERIC(18,6);

            IF v_start IS NULL OR v_end IS NULL THEN
                RAISE EXCEPTION 'Missing usage interval' USING ERRCODE = '23502';
            END IF;
            IF v_end <= v_start THEN
                RAISE EXCEPTION 'usage_end must be after usage_start' USING ERRCODE = '23514';
            END IF;
            IF v_cost < 0 OR v_qty < 0 THEN
                RAISE EXCEPTION 'Negative cost or quantity' USING ERRCODE = '23514';
            END IF;
            IF v_row.service_name IS NULL OR TRIM(v_row.service_name) = '' THEN
                RAISE EXCEPTION 'Missing service_name' USING ERRCODE = '23502';
            END IF;

            SELECT a.account_id INTO v_account_id
            FROM cloud_accounts a
            JOIN cloud_providers cp ON a.provider_id = cp.provider_id
            WHERE LOWER(cp.provider_name) = LOWER(TRIM(v_row.provider_name))
              AND a.external_account_id = TRIM(v_row.external_account_id);

            IF v_account_id IS NULL THEN
                RAISE EXCEPTION 'Unknown account % for provider %',
                    v_row.external_account_id, v_row.provider_name USING ERRCODE = '23503';
            END IF;
            IF v_account_id <> v_batch_account THEN
                RAISE EXCEPTION 'Row account % does not match batch account %',
                    v_account_id, v_batch_account USING ERRCODE = '23503';
            END IF;

            SELECT resource_id, project_id
              INTO v_resource_id, v_project_id
              FROM resources
             WHERE account_id = v_account_id
               AND external_resource_id = TRIM(v_row.external_resource_id);

            v_currency := COALESCE(NULLIF(UPPER(TRIM(v_row.currency_code_text)), ''), 'USD');

            INSERT INTO billing_records (
                account_id, resource_id, project_id, service_name,
                usage_start, usage_end, usage_quantity, billed_cost,
                currency_code, source_batch_id
            ) VALUES (
                v_account_id, v_resource_id, v_project_id, TRIM(v_row.service_name),
                v_start, v_end, v_qty, v_cost,
                v_currency, p_batch_id
            );

            v_accepted := v_accepted + 1;
        EXCEPTION WHEN OTHERS THEN
            INSERT INTO billing_import_errors (
                batch_id, raw_row_index, raw_payload, error_code, error_message
            ) VALUES (
                p_batch_id, v_row.staging_id, TO_JSONB(v_row), SQLSTATE, SQLERRM
            );
            v_rejected := v_rejected + 1;
        END;
    END LOOP;

    UPDATE billing_import_batches
       SET row_count = v_accepted + v_rejected,
           accepted_count = v_accepted,
           rejected_count = v_rejected,
           status = CASE
                        WHEN v_rejected > 0 AND v_accepted = 0 THEN 'failed'
                        ELSE 'processed'
                    END
     WHERE batch_id = p_batch_id;

    DELETE FROM billing_staging WHERE batch_id = p_batch_id;

    RETURN FORMAT('Batch %s: %s accepted, %s rejected', p_batch_id, v_accepted, v_rejected);
END;
$$;


CREATE OR REPLACE PROCEDURE refresh_materialized_views()
LANGUAGE plpgsql
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_project_cost;
END;
$$;
