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
-- ---------------------------------------------------------------------
-- FOCUS helpers. Account ownership is resolved from the invoice issuer's
-- billing account and, when present, the cost-owning sub-account.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION focus_provider_id(p_provider_name TEXT)
RETURNS INT
LANGUAGE sql
STABLE
AS $$
    SELECT provider_id
    FROM cloud_providers
    WHERE LOWER(BTRIM(provider_name)) = LOWER(BTRIM(p_provider_name))
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION focus_account_id(
    p_provider_id INT,
    p_billing_account_id TEXT,
    p_sub_account_id TEXT
)
RETURNS BIGINT
LANGUAGE sql
STABLE
AS $$
    SELECT account_id
    FROM cloud_accounts
    WHERE provider_id = p_provider_id
      AND external_account_id = COALESCE(NULLIF(BTRIM(p_sub_account_id), ''),
                                          NULLIF(BTRIM(p_billing_account_id), ''))
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION focus_json_object(p_payload JSONB, p_key TEXT)
RETURNS JSONB
LANGUAGE plpgsql
IMMUTABLE
AS $$
BEGIN
    IF p_payload IS NULL OR NOT (p_payload ? p_key) THEN
        RETURN NULL;
    END IF;
    IF NULLIF(BTRIM(p_payload->>p_key), '') IS NULL THEN
        RETURN NULL;
    END IF;
    IF JSONB_TYPEOF(p_payload->p_key) = 'string' THEN
        RETURN (p_payload->>p_key)::JSONB;
    END IF;
    RETURN p_payload->p_key;
END;
$$;

CREATE OR REPLACE FUNCTION start_focus_batch(
    p_file_name TEXT,
    p_file_checksum TEXT,
    p_row_count INT,
    p_header_map JSONB,
    p_imported_by BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_batch_id BIGINT;
BEGIN
    SELECT batch_id INTO v_batch_id
    FROM billing_import_batches
    WHERE file_checksum = p_file_checksum;

    IF v_batch_id IS NOT NULL THEN
        RETURN JSONB_BUILD_OBJECT('duplicate', TRUE, 'batch_id', v_batch_id);
    END IF;

    INSERT INTO billing_import_batches (
        imported_by, file_name, file_checksum, row_count, header_map, status
    ) VALUES (
        p_imported_by, p_file_name, p_file_checksum, p_row_count,
        COALESCE(p_header_map, '{}'::JSONB), 'uploaded'
    )
    ON CONFLICT (file_checksum) WHERE file_checksum IS NOT NULL DO NOTHING
    RETURNING batch_id INTO v_batch_id;

    IF v_batch_id IS NULL THEN
        SELECT batch_id INTO v_batch_id
        FROM billing_import_batches
        WHERE file_checksum = p_file_checksum;
        RETURN JSONB_BUILD_OBJECT('duplicate', TRUE, 'batch_id', v_batch_id);
    END IF;

    RETURN JSONB_BUILD_OBJECT('duplicate', FALSE, 'batch_id', v_batch_id);
END;
$$;


-- ---------------------------------------------------------------------
-- ELT: validate and load a FOCUS staging batch. The explicit cursor gives
-- the loader one row at a time so a bad row can be quarantined without
-- aborting the whole batch.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION load_focus_billing_batch(p_batch_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    c_rows CURSOR FOR
        SELECT row_index, raw_payload
        FROM billing_staging
        WHERE batch_id = p_batch_id
        ORDER BY row_index;

    v_row RECORD;
    v_payload JSONB;
    v_provider TEXT;
    v_billing_account TEXT;
    v_sub_account TEXT;
    v_currency CHAR(3);
    v_service_name TEXT;
    v_service_category TEXT;
    v_charge_category TEXT;
    v_charge_class TEXT;
    v_provider_id INT;
    v_account_id BIGINT;
    v_resource_id BIGINT;
    v_project_id BIGINT;
    v_charge_start TIMESTAMPTZ;
    v_charge_end TIMESTAMPTZ;
    v_billing_start TIMESTAMPTZ;
    v_billing_end TIMESTAMPTZ;
    v_pricing_quantity NUMERIC(28,10);
    v_billed_cost NUMERIC(28,10);
    v_contracted_cost NUMERIC(28,10);
    v_effective_cost NUMERIC(28,10);
    v_list_cost NUMERIC(28,10);
    v_tags JSONB;
    v_fingerprint CHAR(32);
    v_inserted INT;
    v_accepted INT := 0;
    v_rejected INT := 0;
    v_duplicate INT := 0;
    v_source_total NUMERIC(28,10) := 0;
    v_accepted_total NUMERIC(28,10) := 0;
    v_rejected_total NUMERIC(28,10) := 0;
    v_duplicate_total NUMERIC(28,10) := 0;
    v_batch_currency CHAR(3);
    v_currency_count INT;
    v_unknown_accounts INT;
    v_pilot_currency CHAR(3);
BEGIN
    PERFORM 1
    FROM billing_import_batches
    WHERE batch_id = p_batch_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Billing batch % not found', p_batch_id USING ERRCODE = 'P0002';
    END IF;

    SELECT COUNT(DISTINCT UPPER(BTRIM(raw_payload->>'BillingCurrency')))
      INTO v_currency_count
      FROM billing_staging
     WHERE batch_id = p_batch_id
       AND NULLIF(BTRIM(raw_payload->>'BillingCurrency'), '') IS NOT NULL;

    SELECT UPPER(BTRIM(raw_payload->>'BillingCurrency'))
      INTO v_batch_currency
      FROM billing_staging
     WHERE batch_id = p_batch_id
       AND NULLIF(BTRIM(raw_payload->>'BillingCurrency'), '') IS NOT NULL
     LIMIT 1;

    v_pilot_currency := COALESCE(NULLIF(current_setting('app.pilot_currency', TRUE), ''), 'USD');

    IF v_currency_count > 1 THEN
        UPDATE billing_import_batches SET status = 'failed' WHERE batch_id = p_batch_id;
        RETURN JSONB_BUILD_OBJECT(
            'error', 'Mixed BillingCurrency values are not allowed',
            'code', 'MIXED_CURRENCY'
        );
    END IF;

    IF v_batch_currency IS NOT NULL AND v_batch_currency <> v_pilot_currency THEN
        UPDATE billing_import_batches SET status = 'failed' WHERE batch_id = p_batch_id;
        RETURN JSONB_BUILD_OBJECT(
            'error', FORMAT('BillingCurrency %s does not match pilot currency %s',
                            v_batch_currency, v_pilot_currency),
            'code', 'CURRENCY_MISMATCH'
        );
    END IF;

    SELECT COUNT(*)
      INTO v_unknown_accounts
      FROM billing_staging s
     WHERE s.batch_id = p_batch_id
       AND NOT (s.raw_payload ? '_parse_error')
       AND (
            focus_provider_id(s.raw_payload->>'ServiceProviderName') IS NULL
            OR focus_account_id(
                   focus_provider_id(s.raw_payload->>'ServiceProviderName'),
                   s.raw_payload->>'BillingAccountId',
                   NULLIF(BTRIM(s.raw_payload->>'SubAccountId'), '')
               ) IS NULL
       );

    IF v_unknown_accounts > 0 THEN
        UPDATE billing_import_batches SET status = 'failed' WHERE batch_id = p_batch_id;
        RETURN JSONB_BUILD_OBJECT(
            'error', FORMAT('%s row(s) reference an unknown provider or account', v_unknown_accounts),
            'code', 'UNKNOWN_ACCOUNT',
            'rows', v_unknown_accounts
        );
    END IF;

    UPDATE billing_import_batches
       SET status = 'validating',
           billing_currency = v_batch_currency
     WHERE batch_id = p_batch_id;

    OPEN c_rows;

    LOOP
        FETCH c_rows INTO v_row;
        EXIT WHEN NOT FOUND;

        BEGIN
            v_payload := v_row.raw_payload;
            v_billed_cost := NULL;

            v_provider := NULLIF(BTRIM(v_payload->>'ServiceProviderName'), '');
            v_billing_account := NULLIF(BTRIM(v_payload->>'BillingAccountId'), '');
            v_sub_account := NULLIF(BTRIM(v_payload->>'SubAccountId'), '');
            v_currency := UPPER(BTRIM(v_payload->>'BillingCurrency'))::CHAR(3);
            v_service_name := NULLIF(BTRIM(v_payload->>'ServiceName'), '');
            v_service_category := NULLIF(BTRIM(v_payload->>'ServiceCategory'), '');
            v_charge_category := NULLIF(BTRIM(v_payload->>'ChargeCategory'), '');
            v_charge_class := NULLIF(BTRIM(v_payload->>'ChargeClass'), '');
            v_charge_start := (v_payload->>'ChargePeriodStart')::TIMESTAMPTZ;
            v_charge_end := (v_payload->>'ChargePeriodEnd')::TIMESTAMPTZ;
            v_billing_start := (v_payload->>'BillingPeriodStart')::TIMESTAMPTZ;
            v_billing_end := (v_payload->>'BillingPeriodEnd')::TIMESTAMPTZ;
            v_pricing_quantity := (v_payload->>'PricingQuantity')::NUMERIC(28,10);
            v_billed_cost := (v_payload->>'BilledCost')::NUMERIC(28,10);
            v_contracted_cost := (v_payload->>'ContractedCost')::NUMERIC(28,10);
            v_effective_cost := (v_payload->>'EffectiveCost')::NUMERIC(28,10);
            v_list_cost := (v_payload->>'ListCost')::NUMERIC(28,10);
            v_tags := focus_json_object(v_payload, 'Tags');

            IF v_tags IS NOT NULL AND JSONB_TYPEOF(v_tags) <> 'object' THEN
                RAISE EXCEPTION 'Tags must be a JSON object' USING ERRCODE = '23514';
            END IF;

            IF v_currency IS NOT NULL
               AND LENGTH(UPPER(BTRIM(v_payload->>'BillingCurrency'))) <> 3 THEN
                RAISE EXCEPTION 'BillingCurrency must be a three-letter code'
                    USING ERRCODE = '23514';
            END IF;

            IF v_provider IS NULL
               OR v_billing_account IS NULL
               OR NULLIF(BTRIM(v_payload->>'BillingAccountName'), '') IS NULL
               OR v_currency IS NULL
               OR v_charge_start IS NULL
               OR v_charge_end IS NULL
               OR v_billing_start IS NULL
               OR v_billing_end IS NULL
               OR v_service_name IS NULL
               OR v_service_category IS NULL
               OR v_charge_category IS NULL
               OR NULLIF(BTRIM(v_payload->>'ChargeDescription'), '') IS NULL
               OR NULLIF(BTRIM(v_payload->>'HostProviderName'), '') IS NULL
               OR NULLIF(BTRIM(v_payload->>'InvoiceIssuerName'), '') IS NULL
               OR NULLIF(BTRIM(v_payload->>'PricingUnit'), '') IS NULL
               OR v_pricing_quantity IS NULL
               OR v_billed_cost IS NULL
               OR v_contracted_cost IS NULL
               OR v_effective_cost IS NULL
               OR v_list_cost IS NULL THEN
                RAISE EXCEPTION 'Missing mandatory FOCUS value' USING ERRCODE = '23502';
            END IF;

            IF v_charge_end <= v_charge_start THEN
                RAISE EXCEPTION 'ChargePeriodEnd must be after ChargePeriodStart'
                    USING ERRCODE = '23514';
            END IF;

            IF v_billing_end <= v_billing_start THEN
                RAISE EXCEPTION 'BillingPeriodEnd must be after BillingPeriodStart'
                    USING ERRCODE = '23514';
            END IF;

            IF v_charge_category NOT IN ('Usage','Purchase','Tax','Credit','Adjustment') THEN
                RAISE EXCEPTION 'Unknown ChargeCategory %', v_charge_category
                    USING ERRCODE = '23514';
            END IF;

            v_provider_id := focus_provider_id(v_provider);
            IF v_provider_id IS NULL THEN
                RAISE EXCEPTION 'Unknown provider %', v_provider USING ERRCODE = '23503';
            END IF;

            v_account_id := focus_account_id(v_provider_id, v_billing_account, v_sub_account);
            IF v_account_id IS NULL THEN
                RAISE EXCEPTION 'Unknown account % / sub-account % for provider %',
                    v_billing_account, v_sub_account, v_provider USING ERRCODE = '23503';
            END IF;

            SELECT resource_id, project_id
              INTO v_resource_id, v_project_id
              FROM resources
             WHERE account_id = v_account_id
               AND external_resource_id = NULLIF(BTRIM(v_payload->>'ResourceId'), '');

            v_fingerprint := focus_payload_fingerprint(v_payload);

            INSERT INTO billing_records (
                account_id, resource_id, project_id, source_batch_id,
                source_row_index, source_row_fingerprint, service_name,
                usage_start, usage_end, usage_quantity, billed_cost,
                contracted_cost, effective_cost, list_cost, currency_code,
                billing_period_start, billing_period_end, charge_category,
                charge_class, charge_description, service_provider_name,
                host_provider_name, invoice_issuer_name, invoice_id,
                invoice_detail_id, billing_account_external_id,
                sub_account_external_id, tags, focus_payload
            ) VALUES (
                v_account_id, v_resource_id, v_project_id, p_batch_id,
                v_row.row_index, v_fingerprint, v_service_name,
                v_charge_start, v_charge_end, v_pricing_quantity, v_billed_cost,
                v_contracted_cost, v_effective_cost, v_list_cost, v_currency,
                v_billing_start, v_billing_end, v_charge_category,
                v_charge_class, NULLIF(BTRIM(v_payload->>'ChargeDescription'), ''),
                v_provider, NULLIF(BTRIM(v_payload->>'HostProviderName'), ''),
                NULLIF(BTRIM(v_payload->>'InvoiceIssuerName'), ''),
                NULLIF(BTRIM(v_payload->>'InvoiceId'), ''),
                NULLIF(BTRIM(v_payload->>'InvoiceDetailId'), ''),
                v_billing_account, v_sub_account, v_tags, v_payload
            )
            ON CONFLICT (account_id, source_row_fingerprint, usage_start) DO NOTHING;

            GET DIAGNOSTICS v_inserted = ROW_COUNT;

            v_source_total := v_source_total + v_billed_cost;
            IF v_inserted = 0 THEN
                v_duplicate := v_duplicate + 1;
                v_duplicate_total := v_duplicate_total + v_billed_cost;
            ELSE
                v_accepted := v_accepted + 1;
                v_accepted_total := v_accepted_total + v_billed_cost;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            INSERT INTO billing_import_errors (
                batch_id, raw_row_index, raw_payload, error_code, error_message
            ) VALUES (
                p_batch_id, v_row.row_index, v_row.raw_payload, SQLSTATE, SQLERRM
            );
            v_rejected := v_rejected + 1;
            IF v_billed_cost IS NOT NULL THEN
                v_rejected_total := v_rejected_total + v_billed_cost;
                v_source_total := v_source_total + v_billed_cost;
            END IF;
        END;
    END LOOP;

    CLOSE c_rows;

    UPDATE billing_import_batches
       SET row_count = v_accepted + v_rejected + v_duplicate,
           accepted_count = v_accepted,
           rejected_count = v_rejected,
           duplicate_count = v_duplicate,
           source_total = v_source_total,
           accepted_total = v_accepted_total,
           rejected_total = v_rejected_total,
           duplicate_total = v_duplicate_total,
           billing_currency = v_batch_currency,
           status = CASE
                        WHEN v_rejected > 0 AND v_accepted = 0 AND v_duplicate = 0 THEN 'failed'
                        ELSE 'processed'
                    END
     WHERE batch_id = p_batch_id;

    DELETE FROM billing_import_account_summary WHERE batch_id = p_batch_id;
    INSERT INTO billing_import_account_summary (
        batch_id, account_id, line_item_count, accepted_total, currency_code
    )
    SELECT
        p_batch_id, br.account_id, COUNT(*), SUM(br.billed_cost), br.currency_code
    FROM billing_records br
    WHERE br.source_batch_id = p_batch_id
    GROUP BY br.account_id, br.currency_code;

    RETURN JSONB_BUILD_OBJECT(
        'batch_id', p_batch_id,
        'accepted', v_accepted,
        'rejected', v_rejected,
        'duplicate', v_duplicate,
        'source_total', v_source_total,
        'accepted_total', v_accepted_total,
        'rejected_total', v_rejected_total,
        'duplicate_total', v_duplicate_total,
        'billing_currency', v_batch_currency,
        'status', CASE WHEN v_rejected > 0 AND v_accepted = 0 AND v_duplicate = 0
                       THEN 'failed' ELSE 'processed' END
    );
END;
$$;


CREATE OR REPLACE PROCEDURE refresh_materialized_views()
LANGUAGE plpgsql
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_project_cost;
END;
$$;
