-- =====================================================================
-- clouDBills — Tier 3 Data Layer
-- 01_schema.sql : 33 entity relational schema (3NF) + range partitioning
-- Target: PostgreSQL 15+
-- =====================================================================

-- ---------- Layer 1: Master data & organisation ----------

CREATE TABLE cloud_providers (
    provider_id        SERIAL PRIMARY KEY,
    provider_name      VARCHAR(50) UNIQUE NOT NULL,
    api_endpoint_url   TEXT,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE environments (
    environment_id        SERIAL PRIMARY KEY,
    environment_code      VARCHAR(30) UNIQUE NOT NULL,
    is_production_grade   BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE users (
    user_id        BIGSERIAL PRIMARY KEY,
    email          VARCHAR(255) UNIQUE NOT NULL,
    username       VARCHAR(100) NOT NULL,
    password_hash  TEXT NOT NULL,
    first_name     VARCHAR(100),
    last_name      VARCHAR(100),
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE roles (
    role_id      SERIAL PRIMARY KEY,
    role_name    VARCHAR(50) UNIQUE NOT NULL,
    description  TEXT,
    permissions  JSONB NOT NULL DEFAULT '{}'
);

-- M:N junction — (user_id, role_id) is the composite primary key
CREATE TABLE user_roles (
    user_id      BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role_id      INT NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE teams (
    team_id          BIGSERIAL PRIMARY KEY,
    team_name        VARCHAR(100) UNIQUE NOT NULL,
    cost_center      VARCHAR(50) UNIQUE NOT NULL,
    manager_user_id  BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3NF: team_name / cost_center live in teams, NOT here (no transitive dependency)
CREATE TABLE projects (
    project_id      BIGSERIAL PRIMARY KEY,
    team_id         BIGINT NOT NULL REFERENCES teams(team_id) ON DELETE RESTRICT,
    environment_id  INT NOT NULL REFERENCES environments(environment_id) ON DELETE RESTRICT,
    project_name    VARCHAR(150) NOT NULL,
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (team_id, project_name, environment_id)
);

CREATE TABLE regions (
    region_id    SERIAL PRIMARY KEY,
    provider_id  INT NOT NULL REFERENCES cloud_providers(provider_id) ON DELETE RESTRICT,
    region_code  VARCHAR(50) NOT NULL,
    region_name  VARCHAR(100) NOT NULL,
    continent    VARCHAR(50),
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (provider_id, region_code)
);

CREATE TABLE resource_types (
    type_id           SERIAL PRIMARY KEY,
    provider_id       INT NOT NULL REFERENCES cloud_providers(provider_id) ON DELETE RESTRICT,
    raw_service_code  VARCHAR(100) NOT NULL,
    raw_type_name     VARCHAR(150) NOT NULL,
    generic_category  VARCHAR(50) CHECK (generic_category IN ('compute','storage','network','database')),
    pricing_unit      VARCHAR(50),
    UNIQUE (provider_id, raw_service_code, raw_type_name)
);

CREATE TABLE cloud_accounts (
    account_id           BIGSERIAL PRIMARY KEY,
    provider_id          INT NOT NULL REFERENCES cloud_providers(provider_id) ON DELETE RESTRICT,
    account_name         VARCHAR(150) NOT NULL,
    external_account_id  VARCHAR(100) NOT NULL,
    currency_code        CHAR(3) NOT NULL DEFAULT 'USD',
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (provider_id, external_account_id)
);

-- ---------- Layer 2: Operational inventory & dependency graph ----------

CREATE TABLE resources (
    resource_id           BIGSERIAL PRIMARY KEY,
    account_id            BIGINT NOT NULL REFERENCES cloud_accounts(account_id) ON DELETE RESTRICT,
    project_id            BIGINT REFERENCES projects(project_id) ON DELETE SET NULL,
    type_id               INT NOT NULL REFERENCES resource_types(type_id) ON DELETE RESTRICT,
    region_id             INT NOT NULL REFERENCES regions(region_id) ON DELETE RESTRICT,
    external_resource_id  VARCHAR(255) NOT NULL,
    resource_name         VARCHAR(255) NOT NULL,
    status                VARCHAR(30) NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active','stopped','deleted','unknown')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at            TIMESTAMPTZ,
    hourly_cost           NUMERIC(12,4) NOT NULL DEFAULT 0 CHECK (hourly_cost >= 0),
    CHECK ((status = 'deleted' AND deleted_at IS NOT NULL)
        OR (status <> 'deleted' AND deleted_at IS NULL)),
    UNIQUE (account_id, external_resource_id)
);

-- Weak entity: tag_key in the PK keeps the relation in 1NF (atomic values)
CREATE TABLE resource_tags (
    resource_id  BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    tag_key      VARCHAR(128) NOT NULL,
    tag_value    VARCHAR(256) NOT NULL,
    PRIMARY KEY (resource_id, tag_key)
);

-- Recursive self-referencing M:N dependency graph
CREATE TABLE resource_dependencies (
    parent_resource_id  BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    child_resource_id   BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    dependency_type     VARCHAR(50) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (parent_resource_id, child_resource_id),
    CHECK (parent_resource_id <> child_resource_id)
);

-- M:N junction for shared multi-tenant infrastructure
CREATE TABLE resource_consumers (
    resource_id           BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    project_id            BIGINT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    allocation_percentage NUMERIC(5,2) NOT NULL
                           CHECK (allocation_percentage > 0 AND allocation_percentage <= 100),
    PRIMARY KEY (resource_id, project_id)
);

CREATE TABLE resource_status_history (
    status_history_id  BIGSERIAL PRIMARY KEY,
    resource_id        BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    previous_status    VARCHAR(30),
    new_status         VARCHAR(30) NOT NULL,
    transition_time    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason             TEXT
);

-- ---------- Layer 3: Ingestion & time-series facts ----------

CREATE TABLE metric_types (
    metric_type_id     SERIAL PRIMARY KEY,
    metric_name        VARCHAR(80) UNIQUE NOT NULL,
    unit               VARCHAR(30) CHECK (unit IN ('percent','bytes','iops','count')),
    aggregation_method VARCHAR(10) CHECK (aggregation_method IN ('avg','max','sum'))
);

CREATE TABLE billing_import_batches (
    batch_id        BIGSERIAL PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES cloud_accounts(account_id) ON DELETE RESTRICT,
    imported_by     BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    file_name       TEXT NOT NULL,
    file_checksum   CHAR(64),
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    row_count       INT NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    accepted_count  INT NOT NULL DEFAULT 0,
    rejected_count  INT NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'uploaded'
                    CHECK (status IN ('uploaded','validating','validated','processed','failed'))
);

-- Untyped ELT landing zone: every column is raw TEXT
CREATE TABLE billing_staging (
    staging_id           BIGSERIAL PRIMARY KEY,
    batch_id             BIGINT NOT NULL REFERENCES billing_import_batches(batch_id) ON DELETE CASCADE,
    provider_name        TEXT,
    external_account_id  TEXT,
    external_resource_id TEXT,
    service_name         TEXT,
    usage_start_text     TEXT,
    usage_end_text       TEXT,
    usage_quantity_text  TEXT,
    billed_cost_text     TEXT,
    currency_code_text   TEXT
);

-- Quarantine table for rows that fail validation
CREATE TABLE billing_import_errors (
    error_id       BIGSERIAL PRIMARY KEY,
    batch_id       BIGINT NOT NULL REFERENCES billing_import_batches(batch_id) ON DELETE CASCADE,
    raw_row_index  INT NOT NULL,
    raw_payload    JSONB NOT NULL,
    error_code     VARCHAR(50),
    error_message  TEXT,
    rejected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Range-partitioned fact table. The primary key includes the partition key.
CREATE TABLE billing_records (
    billing_id      BIGINT GENERATED ALWAYS AS IDENTITY,
    usage_start     TIMESTAMPTZ NOT NULL,
    account_id      BIGINT NOT NULL REFERENCES cloud_accounts(account_id) ON DELETE RESTRICT,
    resource_id     BIGINT REFERENCES resources(resource_id) ON DELETE SET NULL,
    project_id      BIGINT REFERENCES projects(project_id) ON DELETE SET NULL,
    source_batch_id BIGINT NOT NULL REFERENCES billing_import_batches(batch_id) ON DELETE RESTRICT,
    service_name    VARCHAR(100) NOT NULL,
    usage_end       TIMESTAMPTZ NOT NULL,
    usage_quantity  NUMERIC(18,6) NOT NULL CHECK (usage_quantity >= 0),
    billed_cost     NUMERIC(18,6) NOT NULL CHECK (billed_cost >= 0),
    currency_code   CHAR(3) NOT NULL,
    PRIMARY KEY (billing_id, usage_start),
    CHECK (usage_end > usage_start)
) PARTITION BY RANGE (usage_start);

-- Monthly sub-partitions for the billing horizon (2026-09 .. 2026-12)
CREATE TABLE billing_records_2026_09 PARTITION OF billing_records
    FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');
CREATE TABLE billing_records_2026_10 PARTITION OF billing_records
    FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');
CREATE TABLE billing_records_2026_11 PARTITION OF billing_records
    FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');
CREATE TABLE billing_records_2026_12 PARTITION OF billing_records
    FOR VALUES FROM ('2026-12-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');
-- Defensive catch-all so future rows never fail solely due to a missing partition
CREATE TABLE billing_records_default PARTITION OF billing_records DEFAULT;

CREATE TABLE usage_metrics (
    metric_id       BIGINT GENERATED ALWAYS AS IDENTITY,
    measured_at     TIMESTAMPTZ NOT NULL,
    resource_id     BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    metric_type_id  INT NOT NULL REFERENCES metric_types(metric_type_id) ON DELETE RESTRICT,
    metric_value    NUMERIC(18,6) NOT NULL CHECK (metric_value >= 0),
    PRIMARY KEY (metric_id, measured_at)
) PARTITION BY RANGE (measured_at);

CREATE TABLE usage_metrics_2026_09 PARTITION OF usage_metrics
    FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');
CREATE TABLE usage_metrics_2026_10 PARTITION OF usage_metrics
    FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');
CREATE TABLE usage_metrics_2026_11 PARTITION OF usage_metrics
    FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');
CREATE TABLE usage_metrics_2026_12 PARTITION OF usage_metrics
    FOR VALUES FROM ('2026-12-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');
CREATE TABLE usage_metrics_default PARTITION OF usage_metrics DEFAULT;

CREATE TABLE resource_daily_summaries (
    resource_id     BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    metric_type_id  INT NOT NULL REFERENCES metric_types(metric_type_id) ON DELETE RESTRICT,
    summary_date    DATE NOT NULL,
    avg_value       NUMERIC(18,6),
    max_value       NUMERIC(18,6),
    min_value       NUMERIC(18,6),
    sample_count    INT CHECK (sample_count >= 0),
    PRIMARY KEY (resource_id, metric_type_id, summary_date)
);

-- ---------- Layer 4: Decision, governance & audit ----------

CREATE TABLE optimization_rules (
    rule_id       SERIAL PRIMARY KEY,
    rule_name     VARCHAR(100) UNIQUE NOT NULL,
    rule_type     VARCHAR(50) NOT NULL,
    description   TEXT,
    severity      VARCHAR(20) CHECK (severity IN ('low','medium','high','critical')),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    parameters    JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE recommendations (
    recommendation_id       BIGSERIAL PRIMARY KEY,
    rule_id                 INT NOT NULL REFERENCES optimization_rules(rule_id) ON DELETE RESTRICT,
    resource_id             BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    project_id              BIGINT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    recommendation_type     VARCHAR(80) NOT NULL,
    problem_description     TEXT NOT NULL,
    suggested_action        TEXT NOT NULL,
    estimated_monthly_saving NUMERIC(18,2) NOT NULL DEFAULT 0
                            CHECK (estimated_monthly_saving >= 0),
    risk_level              VARCHAR(20),
    status                  VARCHAR(20) NOT NULL DEFAULT 'detected'
                            CHECK (status IN ('detected','under_review','approved','rejected',
                                              'deferred','executed','validated','closed')),
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ
);

CREATE TABLE recommendation_evidence (
    evidence_id        BIGSERIAL PRIMARY KEY,
    recommendation_id  BIGINT NOT NULL REFERENCES recommendations(recommendation_id) ON DELETE CASCADE,
    metric_type_id     INT REFERENCES metric_types(metric_type_id) ON DELETE SET NULL,
    observation_start  TIMESTAMPTZ NOT NULL,
    observation_end    TIMESTAMPTZ NOT NULL,
    observed_value     NUMERIC(18,6),
    threshold_value    NUMERIC(18,6),
    narrative          TEXT,
    CHECK (observation_end > observation_start)
);

CREATE TABLE approval_requests (
    approval_id        BIGSERIAL PRIMARY KEY,
    recommendation_id  BIGINT NOT NULL REFERENCES recommendations(recommendation_id) ON DELETE CASCADE,
    requested_by       BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    reviewed_by        BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    decision           VARCHAR(20) CHECK (decision IN ('approved','rejected','deferred')),
    comments           TEXT,
    requested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at        TIMESTAMPTZ
);

-- At most ONE pending (decision IS NULL) request per recommendation
CREATE UNIQUE INDEX idx_one_pending_approval_per_rec
    ON approval_requests(recommendation_id) WHERE decision IS NULL;

CREATE TABLE recommendation_status_history (
    history_id         BIGSERIAL PRIMARY KEY,
    recommendation_id  BIGINT NOT NULL REFERENCES recommendations(recommendation_id) ON DELETE CASCADE,
    changed_by         BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    old_status         VARCHAR(30),
    new_status         VARCHAR(30) NOT NULL,
    changed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason             TEXT
);

CREATE TABLE recommendation_actions (
    action_id          BIGSERIAL PRIMARY KEY,
    recommendation_id  BIGINT NOT NULL REFERENCES recommendations(recommendation_id) ON DELETE CASCADE,
    executed_by        BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    action_type        VARCHAR(40) NOT NULL,
    execution_status   VARCHAR(20) NOT NULL DEFAULT 'in_progress',
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    error_payload      JSONB
);

CREATE TABLE savings_realization (
    realization_id       BIGSERIAL PRIMARY KEY,
    recommendation_id    BIGINT NOT NULL UNIQUE REFERENCES recommendations(recommendation_id) ON DELETE CASCADE,
    baseline_monthly_cost NUMERIC(18,2) NOT NULL,
    actual_monthly_cost   NUMERIC(18,2) NOT NULL,
    estimated_saving      NUMERIC(18,2) NOT NULL,
    actual_saving         NUMERIC(18,2) NOT NULL,
    realization_percentage NUMERIC(6,2),
    measurement_start     DATE NOT NULL,
    measurement_end       DATE NOT NULL,
    calculated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (measurement_end > measurement_start)
);

-- Allocated cost ledger: preserves the point-in-time allocation snapshot
CREATE TABLE allocated_costs (
    allocated_cost_id    BIGSERIAL PRIMARY KEY,
    resource_id          BIGINT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    project_id           BIGINT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    allocation_month     DATE NOT NULL,
    allocation_percentage NUMERIC(5,2) NOT NULL
                          CHECK (allocation_percentage > 0 AND allocation_percentage <= 100),
    allocated_amount     NUMERIC(18,2) NOT NULL CHECK (allocated_amount >= 0)
);

CREATE TABLE budgets (
    budget_id         BIGSERIAL PRIMARY KEY,
    project_id        BIGINT REFERENCES projects(project_id) ON DELETE CASCADE,
    team_id           BIGINT REFERENCES teams(team_id) ON DELETE CASCADE,
    budget_month      DATE NOT NULL,
    budget_amount     NUMERIC(18,2) NOT NULL CHECK (budget_amount > 0),
    warning_percentage NUMERIC(5,2) NOT NULL DEFAULT 80
                       CHECK (warning_percentage > 0 AND warning_percentage <= 100),
    CHECK (project_id IS NOT NULL OR team_id IS NOT NULL)
);

CREATE TABLE budget_alerts (
    alert_id                   BIGSERIAL PRIMARY KEY,
    budget_id                  BIGINT NOT NULL REFERENCES budgets(budget_id) ON DELETE CASCADE,
    triggered_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    threshold_percentage_reached NUMERIC(5,2) NOT NULL,
    current_spend              NUMERIC(18,2) NOT NULL,
    budget_amount              NUMERIC(18,2) NOT NULL,
    is_acknowledged            BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE audit_logs (
    audit_id         BIGSERIAL PRIMARY KEY,
    user_id          BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    action_type      VARCHAR(80) NOT NULL,
    entity_type      VARCHAR(80) NOT NULL,
    entity_id        BIGINT,
    old_state        JSONB,
    new_state        JSONB,
    action_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    client_ip        INET
);

-- ---------- Indexing strategy ----------

-- Composite B-Tree: high-cardinality equality first, then range
CREATE INDEX idx_billing_project_time ON billing_records(project_id, usage_start);
CREATE INDEX idx_billing_account_time  ON billing_records(account_id, usage_start);
CREATE INDEX idx_usage_metrics_resource_time ON usage_metrics(resource_id, measured_at);

-- Partial / filtered index: only live rows, keeping the index small
CREATE INDEX idx_resources_active_missing_owner
    ON resources(resource_id) WHERE status = 'active';

-- Covering B-Tree using INCLUDE: enables index-only scans
CREATE INDEX idx_rec_perf_covering
    ON recommendations(status, risk_level) INCLUDE (estimated_monthly_saving);

CREATE INDEX idx_recommendations_project_status ON recommendations(project_id, status);
CREATE INDEX idx_resource_consumers_project ON resource_consumers(project_id);
CREATE INDEX idx_allocated_costs_project_month ON allocated_costs(project_id, allocation_month);
CREATE INDEX idx_billing_records_batch ON billing_records(source_batch_id);
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_resource_tags_key ON resource_tags(tag_key);
CREATE INDEX idx_budgets_team_month ON budgets(team_id, budget_month);
CREATE INDEX idx_budgets_project_month ON budgets(project_id, budget_month);
