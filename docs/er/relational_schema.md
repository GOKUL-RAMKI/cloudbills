# clouDBills — Relational Schema (3NF, from scratch)

> Source: `docs/clouDBills_Architecture_v2.md` §2 (33 entities),
> ER `docs/er/00_full_no_attrs.mmd` (48 rels), `docs/er/ENTITY_LIST.md`.
> DDL: `schema.sql` (same folder). Postgres 15+.

**Method:** harvest ER, classify strong/weak, map 1:N (FK on N-side),
M:N (junction), assign exact Postgres types, dependency-ordered DDL.

## 1. Design decisions

- **Strong:** providers, environments, users, roles, teams, projects,
  regions, types, accounts, resources, metric_types, batches,
  billing_records*, usage_metrics*, rules, recommendations, budgets, audit_logs.
  (* composite PK includes partition key.)
- **Weak (FK in PK):** resource_tags, resource_dependencies,
  billing_staging, billing_import_errors, resource_daily_summaries.
- **M:N junctions:** user_roles, resource_consumers (composite PK).
  allocated_costs is a ledger, not pure junction (surrogate + month + amount).
- **1:1:** recommendations ↔ savings_realization (UNIQUE FK).
- **Recursive M:N:** resource_dependencies self-FK x2, CHECK parent<>child.
- **Nullable FKs (intentional):** resources.project_id (orphans),
  billing_records.resource/project, approval_requests.reviewed_by,
  audit_logs.user_id (system), budgets.project/team (one NOT NULL).
- **3NF:** teams split from projects; tags atomic; billing keeps project_id only.

## 2a. Master + inventory (visual)

| Table | Columns (PK bold, FK arrow) | Links |
|---|---|---|
| cloud_providers | **provider_id** INT, provider_name VARCHAR(50) UK, api_endpoint TEXT, is_active BOOL | 1:N accounts, regions, types |
| cloud_accounts | **account_id** BIGINT, provider_id INT f>k, account_name VARCHAR(150), external_account_id VARCHAR(100), currency CHAR(3), is_active BOOL; UK(provider,ext) | 1:N resources, batches |
| users | **user_id** BIGINT, email VARCHAR(255) UK, username VARCHAR(100), password_hash TEXT, first/last VARCHAR(100), is_active BOOL, created_at TIMESTAMPTZ | M:N roles; 1:N teams-mgr |
| roles | **role_id** SERIAL, role_name VARCHAR(50) UK, description TEXT, permissions JSONB | M:N users |
| user_roles | **user_id** BIGINT fk, **role_id** INT fk, assigned_at TIMESTAMPTZ | junction |
| teams | **team_id** BIGINT, team_name VARCHAR(100) UK, cost_center VARCHAR(50) UK, manager_user_id BIGINT fk-users, created_at TIMESTAMPTZ | 1:N projects, budgets |
| projects | **project_id** BIGINT, team_id BIGINT fk, environment_id INT fk, project_name VARCHAR(150), description TEXT, is_active BOOL, created_at TIMESTAMPTZ; UK(team,name,env) | 1:N resources, recs |
| environments | **environment_id** SERIAL, environment_code VARCHAR(30) UK, is_production_grade BOOL | 1:N projects |
| regions | **region_id** SERIAL, provider_id INT fk, region_code VARCHAR(50), region_name VARCHAR(100), continent VARCHAR(50), is_active BOOL; UK(provider,code) | 1:N resources |
| resource_types | **type_id** SERIAL, provider_id INT fk, raw_service_code VARCHAR(100), raw_type_name VARCHAR(150), generic_category VARCHAR(50), pricing_unit VARCHAR(50) | 1:N resources |
| resources | **resource_id** BIGINT, account_id fk, project_id fk NULL(orphan), type_id fk, region_id fk, external_resource_id VARCHAR(255), resource_name VARCHAR(255), status VARCHAR(30), created_at/deleted_at TIMESTAMPTZ, hourly_cost NUMERIC(12,4); UK(account,ext) | 1:N tags, metrics, recs |
| resource_tags | **resource_id** fk, **tag_key** VARCHAR(128), tag_value VARCHAR(256) | weak CASCADE |
| resource_dependencies | **parent** BIGINT fk-resources, **child** BIGINT fk-resources, dependency_type VARCHAR(50), created_at TIMESTAMPTZ; CHECK(parent<>child) | recursive M:N |
| resource_consumers | **resource_id** fk, **project_id** fk, allocation_percentage NUMERIC(5,2) 0-100 | junction M:N |
| resource_status_history | **status_history_id** BIGSERIAL, resource_id fk, previous/new VARCHAR(30), transition_time TIMESTAMPTZ, reason TEXT | history |

## 2b. Facts + governance (visual)

| Table | Columns | Links |
|---|---|---|
| billing_import_batches | **batch_id** BIGSERIAL, account_id fk, imported_by fk-users, file_name TEXT, file_checksum CHAR(64), imported_at TIMESTAMPTZ, row/accepted/rejected INT, status VARCHAR(20) | 1:N staging, errors, records |
| billing_staging | **staging_id** BIGSERIAL, batch_id fk, payload all TEXT | weak landing |
| billing_import_errors | **error_id** BIGSERIAL, batch_id fk, raw_row_index INT, raw_payload JSONB, error_code/message, rejected_at TIMESTAMPTZ | weak |
| billing_records | (**billing_id** BIGINT, **usage_start** TIMESTAMPTZ PARTITION), account_id fk, resource_id fk NULL, project_id fk NULL, source_batch_id fk, service_name VARCHAR(100), usage_end TIMESTAMPTZ, qty/cost NUMERIC(18,6), currency CHAR(3) | fact partitioned |
| metric_types | **metric_type_id** SERIAL, metric_name VARCHAR(80) UK, unit VARCHAR(30), aggregation_method VARCHAR(10) | 1:N metrics |
| usage_metrics | (**metric_id** BIGINT, **measured_at** TIMESTAMPTZ PARTITION), resource_id fk, metric_type_id fk, metric_value NUMERIC(18,6) | fact partitioned |
| resource_daily_summaries | **resource_id** fk, **metric_type_id** fk, **summary_date** DATE, avg/max/min NUMERIC(18,6), sample_count INT | rollup |
| optimization_rules | **rule_id** SERIAL, rule_name VARCHAR(100) UK, rule_type VARCHAR(50), description TEXT, severity VARCHAR(20), is_active BOOL, parameters JSONB | 1:N recs |
| recommendations | **recommendation_id** BIGSERIAL, rule_id fk, resource_id fk, project_id fk, type VARCHAR(80), problem/suggested TEXT, saving NUMERIC(18,2), risk VARCHAR(20), status VARCHAR(20), detected/expires TIMESTAMPTZ | 1:N evidence, approvals |
| recommendation_evidence | **evidence_id** BIGSERIAL, recommendation_id fk CASCADE, metric_type_id fk NULL, obs_start/end TIMESTAMPTZ, observed/threshold NUMERIC(18,6), narrative TEXT | weak |
| approval_requests | **approval_id** BIGSERIAL, recommendation_id fk, requested_by fk-users, reviewed_by fk-users NULL, decision VARCHAR(20) NULL=pending, comments TEXT, requested/reviewed TIMESTAMPTZ; PARTIAL UNIQUE(rec) WHERE pending | workflow |
| recommendation_status_history | **history_id** BIGSERIAL, recommendation_id fk, changed_by fk-users, old/new VARCHAR(30), changed_at TIMESTAMPTZ, reason TEXT | log |
| recommendation_actions | **action_id** BIGSERIAL, recommendation_id fk, executed_by fk-users, action_type VARCHAR(40), execution_status VARCHAR(20), started/completed TIMESTAMPTZ, error_payload JSONB | ops log |
| savings_realization | **realization_id** BIGSERIAL, recommendation_id fk UNIQUE, baseline/actual/est/actual NUMERIC(18,2), pct NUMERIC(6,2), start/end DATE, calculated_at TIMESTAMPTZ | 1:1 ledger |
| allocated_costs | **allocated_cost_id** BIGSERIAL, resource_id fk, project_id fk, allocation_month DATE, pct NUMERIC(5,2), amount NUMERIC(18,2) | ledger |
| budgets | **budget_id** BIGSERIAL, project_id fk NULL, team_id fk NULL, budget_month DATE, budget_amount NUMERIC(18,2), warning_percentage NUMERIC(5,2); CHECK(one NOT NULL) | 1:N alerts |
| budget_alerts | **alert_id** BIGSERIAL, budget_id fk, triggered_at TIMESTAMPTZ, threshold_pct NUMERIC(5,2), current_spend/amount NUMERIC(18,2), is_acknowledged BOOL | weak |
| audit_logs | **audit_id** BIGSERIAL, user_id fk NULL, action_type/entity_type VARCHAR(80), entity_id BIGINT, old/new JSONB, action_timestamp TIMESTAMPTZ, client_ip INET | append-only |

## 3. Relational notation (standard)

```text
cloud_providers(provider_id, provider_name[UK], api_endpoint_url, is_active)
cloud_accounts(account_id, provider_id[FK], account_name, external_account_id, currency_code, is_active)
users(user_id, email[UK], username, password_hash, first_name, last_name, is_active, created_at)
roles(role_id, role_name[UK], description, permissions)
user_roles(user_id[PK,FK], role_id[PK,FK], assigned_at)
teams(team_id, team_name[UK], cost_center[UK], manager_user_id[FK], created_at)
projects(project_id, team_id[FK], environment_id[FK], project_name, description, is_active, created_at)
environments(environment_id, environment_code[UK], is_production_grade)
regions(region_id, provider_id[FK], region_code, region_name, continent, is_active)
resource_types(type_id, provider_id[FK], raw_service_code, raw_type_name, generic_category, pricing_unit)
resources(resource_id, account_id[FK], project_id[FK NULL], type_id[FK], region_id[FK], external_resource_id, resource_name, status, created_at, deleted_at, hourly_cost)
resource_tags(resource_id[PK,FK], tag_key[PK], tag_value)
resource_dependencies(parent_resource_id[PK,FK], child_resource_id[PK,FK], dependency_type, created_at)
resource_consumers(resource_id[PK,FK], project_id[PK,FK], allocation_percentage)
resource_status_history(status_history_id, resource_id[FK], previous_status, new_status, transition_time, reason)
billing_import_batches(batch_id, account_id[FK], imported_by[FK], file_name, file_checksum, imported_at, row_count, accepted_count, rejected_count, status)
billing_staging(staging_id, batch_id[FK], provider_name, external_account_id, external_resource_id, service_name, usage_start_text, usage_end_text, usage_quantity_text, billed_cost_text, currency_code_text)
billing_import_errors(error_id, batch_id[FK], raw_row_index, raw_payload, error_code, error_message, rejected_at)
billing_records(billing_id[PK], usage_start[PK], account_id[FK], resource_id[FK NULL], project_id[FK NULL], source_batch_id[FK], service_name, usage_end, usage_quantity, billed_cost, currency_code)
usage_metrics(metric_id[PK], measured_at[PK], resource_id[FK], metric_type_id[FK], metric_value)
metric_types(metric_type_id, metric_name[UK], unit, aggregation_method)
resource_daily_summaries(resource_id[PK,FK], metric_type_id[PK,FK], summary_date[PK], avg_value, max_value, min_value, sample_count)
optimization_rules(rule_id, rule_name[UK], rule_type, description, severity, is_active, parameters)
recommendations(recommendation_id, rule_id[FK], resource_id[FK], project_id[FK], recommendation_type, problem_description, suggested_action, estimated_monthly_saving, risk_level, status, detected_at, expires_at)
```

```text
recommendation_evidence(evidence_id, recommendation_id[FK], metric_type_id[FK NULL], observation_start, observation_end, observed_value, threshold_value, narrative)
approval_requests(approval_id, recommendation_id[FK], requested_by[FK], reviewed_by[FK NULL], decision, comments, requested_at, reviewed_at)
recommendation_status_history(history_id, recommendation_id[FK], changed_by[FK], old_status, new_status, changed_at, reason)
recommendation_actions(action_id, recommendation_id[FK], executed_by[FK], action_type, execution_status, started_at, completed_at, error_payload)
savings_realization(realization_id, recommendation_id[FK UNIQUE], baseline_monthly_cost, actual_monthly_cost, estimated_saving, actual_saving, realization_percentage, measurement_start, measurement_end, calculated_at)
allocated_costs(allocated_cost_id, resource_id[FK], project_id[FK], allocation_month, allocation_percentage, allocated_amount)
budgets(budget_id, project_id[FK NULL], team_id[FK NULL], budget_month, budget_amount, warning_percentage)
budget_alerts(alert_id, budget_id[FK], triggered_at, threshold_percentage_reached, current_spend, budget_amount, is_acknowledged)
audit_logs(audit_id, user_id[FK NULL], action_type, entity_type, entity_id, old_state, new_state, action_timestamp, client_ip)
```

**Verify:** `SELECT count(*) FROM (...) tables` = 33. Raw DDL in `schema.sql`.
**Review Focus:** nullable project FK (orphans), partial unique approval, partition PKs, budget XOR check, savings UNIQUE.
