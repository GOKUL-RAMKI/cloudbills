-- =====================================================================
-- clouDBills — 06_triggers.sql
-- Three business-rule triggers + resource status timeline trigger.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Immutable audit trail for recommendations (INSERT/UPDATE/DELETE).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION log_audit_trail_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_id BIGINT;
    v_action  TEXT;
    v_entity  BIGINT;
BEGIN
    v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::BIGINT;

    IF TG_OP = 'DELETE' THEN
        v_action := 'DELETE_RECOMMENDATION';
        v_entity := (TO_JSONB(OLD)->>'recommendation_id')::BIGINT;

        INSERT INTO audit_logs (
            user_id, action_type, entity_type, entity_id, old_state, action_timestamp
        ) VALUES (
            v_user_id, v_action, TG_TABLE_NAME, v_entity, TO_JSONB(OLD), CURRENT_TIMESTAMP
        );
        RETURN OLD;
    END IF;

    v_entity := COALESCE(
        (TO_JSONB(NEW)->>'recommendation_id')::BIGINT,
        (TO_JSONB(NEW)->>'resource_id')::BIGINT,
        (TO_JSONB(NEW)->>'project_id')::BIGINT,
        (TO_JSONB(NEW)->>'budget_id')::BIGINT
    );

    IF TG_OP = 'INSERT' THEN
        v_action := 'CREATE_RECOMMENDATION';
        INSERT INTO audit_logs (
            user_id, action_type, entity_type, entity_id, new_state, action_timestamp
        ) VALUES (
            v_user_id, v_action, TG_TABLE_NAME, v_entity, TO_JSONB(NEW), CURRENT_TIMESTAMP
        );
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF OLD.status IS DISTINCT FROM NEW.status THEN
            v_action := CASE NEW.status
                WHEN 'under_review' THEN 'SUBMIT_RECOMMENDATION'
                WHEN 'approved'     THEN 'APPROVE_RECOMMENDATION'
                WHEN 'rejected'     THEN 'REJECT_RECOMMENDATION'
                WHEN 'deferred'     THEN 'DEFER_RECOMMENDATION'
                WHEN 'executed'     THEN 'EXECUTE_RECOMMENDATION'
                WHEN 'validated'    THEN 'VALIDATE_SAVINGS'
                WHEN 'closed'       THEN 'CLOSE_RECOMMENDATION'
                ELSE 'UPDATE_RECOMMENDATION'
            END;
        ELSE
            v_action := 'UPDATE_RECOMMENDATION';
        END IF;

        INSERT INTO audit_logs (
            user_id, action_type, entity_type, entity_id,
            old_state, new_state, action_timestamp
        ) VALUES (
            v_user_id, v_action, TG_TABLE_NAME, v_entity,
            TO_JSONB(OLD), TO_JSONB(NEW), CURRENT_TIMESTAMP
        );
        RETURN NEW;
    END IF;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_recommendations ON recommendations;
CREATE TRIGGER trg_audit_recommendations
AFTER INSERT OR UPDATE OR DELETE ON recommendations
FOR EACH ROW EXECUTE FUNCTION log_audit_trail_trigger();


-- ---------------------------------------------------------------------
-- 2. Automatic recommendation status history.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_status_history_fn()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO recommendation_status_history (
        recommendation_id, changed_by, old_status, new_status, reason
    ) VALUES (
        NEW.recommendation_id,
        NULLIF(current_setting('app.current_user_id', TRUE), '')::BIGINT,
        OLD.status,
        NEW.status,
        NULLIF(current_setting('app.change_reason', TRUE), '')
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_status_history ON recommendations;
CREATE TRIGGER trg_status_history
AFTER UPDATE OF status ON recommendations
FOR EACH ROW
WHEN (OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION trg_status_history_fn();


-- ---------------------------------------------------------------------
-- 3. Budget threshold alert on new billing facts.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_budget_alert_fn()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    b RECORD;
BEGIN
    FOR b IN
        SELECT bd.budget_id
        FROM budgets bd
        WHERE bd.budget_month = DATE_TRUNC('month', NEW.usage_start)::DATE
          AND (
                (bd.project_id IS NOT NULL AND bd.project_id = NEW.project_id)
             OR (bd.project_id IS NULL AND bd.team_id = (
                    SELECT pj.team_id FROM projects pj WHERE pj.project_id = NEW.project_id
                ))
          )
    LOOP
        PERFORM check_budget_threshold(b.budget_id);
    END LOOP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_budget_alert ON billing_records;
CREATE TRIGGER trg_budget_alert
AFTER INSERT ON billing_records
FOR EACH ROW EXECUTE FUNCTION trg_budget_alert_fn();


-- ---------------------------------------------------------------------
-- Supporting trigger: resource lifecycle timeline.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_resource_status_history_fn()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO resource_status_history (
        resource_id, previous_status, new_status, reason
    ) VALUES (
        NEW.resource_id,
        OLD.status,
        NEW.status,
        NULLIF(current_setting('app.change_reason', TRUE), '')
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_resource_status_history ON resources;
CREATE TRIGGER trg_resource_status_history
AFTER UPDATE OF status ON resources
FOR EACH ROW
WHEN (OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION trg_resource_status_history_fn();


-- ---------------------------------------------------------------------
-- 4. FOCUS staging and billing fingerprinting.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION focus_payload_fingerprint(p_payload JSONB)
RETURNS CHAR(32)
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT MD5((COALESCE(p_payload, '{}'::JSONB) - '_extra')::TEXT);
$$;

CREATE OR REPLACE FUNCTION trg_focus_staging_fingerprint_fn()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.source_row_fingerprint := focus_payload_fingerprint(NEW.raw_payload);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_focus_staging_fingerprint ON billing_staging;
CREATE TRIGGER trg_focus_staging_fingerprint
BEFORE INSERT OR UPDATE OF raw_payload ON billing_staging
FOR EACH ROW EXECUTE FUNCTION trg_focus_staging_fingerprint_fn();

CREATE OR REPLACE FUNCTION trg_billing_focus_fingerprint_fn()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.source_row_fingerprint IS NULL
       AND NEW.focus_payload IS NOT NULL
       AND NEW.focus_payload <> '{}'::JSONB THEN
        NEW.source_row_fingerprint := focus_payload_fingerprint(NEW.focus_payload);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_billing_focus_fingerprint ON billing_records;
CREATE TRIGGER trg_billing_focus_fingerprint
BEFORE INSERT ON billing_records
FOR EACH ROW EXECUTE FUNCTION trg_billing_focus_fingerprint_fn();


-- ---------------------------------------------------------------------
-- 5. Audit every billing import batch transition.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_billing_batch_audit_fn()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO audit_logs (
        user_id, action_type, entity_type, entity_id,
        old_state, new_state, action_timestamp
    ) VALUES (
        COALESCE(NEW.imported_by, NULLIF(current_setting('app.current_user_id', TRUE), '')::BIGINT),
        CASE WHEN TG_OP = 'INSERT' THEN 'CREATE_BILLING_IMPORT' ELSE 'UPDATE_BILLING_IMPORT' END,
        'billing_import_batches',
        NEW.batch_id,
        CASE WHEN TG_OP = 'UPDATE' THEN TO_JSONB(OLD) ELSE NULL END,
        TO_JSONB(NEW),
        CURRENT_TIMESTAMP
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_billing_batch_audit ON billing_import_batches;
CREATE TRIGGER trg_billing_batch_audit
AFTER INSERT OR UPDATE ON billing_import_batches
FOR EACH ROW EXECUTE FUNCTION trg_billing_batch_audit_fn();
