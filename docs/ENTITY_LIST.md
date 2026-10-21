# clouDBills - Entities and Attributes (separate from graph)

Source: docs/clouDBills_Architecture_v2.md Section 2. 33 entities.
PK = Primary Key, FK = Foreign Key, UK = Unique / Candidate Key.

## CLOUD_PROVIDERS

```text
CLOUD_PROVIDERS {
int provider_id PK
varchar provider_name UK
varchar api_endpoint_url
boolean is_active
}
```

## CLOUD_ACCOUNTS

```text
CLOUD_ACCOUNTS {
bigint account_id PK
int provider_id FK
varchar account_name
varchar external_account_id
char currency_code
boolean is_active
}
```

## USERS

```text
USERS {
bigint user_id PK
varchar email UK
varchar username
varchar password_hash
varchar first_name
varchar last_name
boolean is_active
timestamp created_at
}
```

## ROLES

```text
ROLES {
int role_id PK
varchar role_name UK
varchar description
jsonb permissions
}
```

## USER_ROLES

```text
USER_ROLES {
bigint user_id FK PK
int role_id FK PK
timestamp assigned_at
}
```

## TEAMS

```text
TEAMS {
bigint team_id PK
varchar team_name UK
varchar cost_center UK
bigint manager_user_id FK
timestamp created_at
}
```

## PROJECTS

```text
PROJECTS {
bigint project_id PK
bigint team_id FK
int environment_id FK
varchar project_name
varchar description
boolean is_active
timestamp created_at
}
```

## ENVIRONMENTS

```text
ENVIRONMENTS {
int environment_id PK
varchar environment_code UK
boolean is_production_grade
}
```

## REGIONS

```text
REGIONS {
int region_id PK
int provider_id FK
varchar region_code
varchar region_name
varchar continent
boolean is_active
}
```

## RESOURCE_TYPES

```text
RESOURCE_TYPES {
int type_id PK
int provider_id FK
varchar raw_service_code
varchar raw_type_name
varchar generic_category
varchar pricing_unit
}
```

## RESOURCES

```text
RESOURCES {
bigint resource_id PK
bigint account_id FK
bigint project_id FK
}
```

## BUDGETS

```text
BUDGETS {
bigint budget_id PK
bigint project_id FK
bigint team_id FK
}
```

## RESOURCE_TAGS

```text
RESOURCE_TAGS {
bigint resource_id FK PK
varchar tag_key PK
varchar tag_value
}
```

## RESOURCE_DEPENDENCIES

```text
RESOURCE_DEPENDENCIES {
bigint parent_resource_id FK PK
bigint child_resource_id FK PK
varchar dependency_type "attached_storage,elastic_ip,network_interface"
timestamp created_at
}
```

## RESOURCE_CONSUMERS

```text
RESOURCE_CONSUMERS {
bigint resource_id FK PK
bigint project_id FK PK
decimal allocation_percentage "0-100"
}
```

## RESOURCE_STATUS_HISTORY

```text
RESOURCE_STATUS_HISTORY {
bigint status_history_id PK
bigint resource_id FK
varchar previous_status
varchar new_status
timestamp transition_time
varchar reason
}
```

## ALLOCATED_COSTS

```text
ALLOCATED_COSTS {
bigint allocated_cost_id PK
bigint resource_id FK
bigint project_id FK
date allocation_month
decimal allocation_percentage
decimal allocated_amount
}
```

## BILLING_IMPORT_BATCHES

```text
BILLING_IMPORT_BATCHES {
bigint batch_id PK
bigint account_id FK
bigint imported_by FK
varchar file_name
char file_checksum
timestamp imported_at
int row_count
int accepted_count
int rejected_count
varchar status "uploaded,validating,validated,processed,failed"
}
```

## BILLING_STAGING

```text
BILLING_STAGING {
bigint staging_id PK
bigint batch_id FK
text provider_name
text external_account_id
text external_resource_id
text service_name
text usage_start_text
text usage_end_text
text usage_quantity_text
text billed_cost_text
text currency_code_text
}
```

## BILLING_IMPORT_ERRORS

```text
BILLING_IMPORT_ERRORS {
bigint error_id PK
bigint batch_id FK
int raw_row_index
jsonb raw_payload
varchar error_code
varchar error_message
timestamp rejected_at
}
```

## BILLING_RECORDS

```text
BILLING_RECORDS {
bigint billing_id PK
timestamp usage_start PK "partition key"
bigint account_id FK
bigint resource_id FK "NULLABLE"
bigint project_id FK "NULLABLE"
bigint source_batch_id FK
varchar service_name
timestamp usage_end
decimal usage_quantity
decimal billed_cost
char currency_code
}
```

## METRIC_TYPES

```text
METRIC_TYPES {
int metric_type_id PK
varchar metric_name UK
varchar unit "percent,bytes,iops,count"
varchar aggregation_method "avg,max,sum"
}
```

## USAGE_METRICS

```text
USAGE_METRICS {
bigint metric_id PK
timestamp measured_at PK "partition key"
bigint resource_id FK
int metric_type_id FK
decimal metric_value
}
```

## RESOURCE_DAILY_SUMMARIES

```text
RESOURCE_DAILY_SUMMARIES {
bigint resource_id FK PK
int metric_type_id FK PK
date summary_date PK
decimal avg_value
decimal max_value
decimal min_value
int sample_count
}
```

## OPTIMIZATION_RULES

```text
OPTIMIZATION_RULES {
int rule_id PK
varchar rule_name UK
varchar rule_type "idle_compute,unattached_volume,missing_tag"
varchar description
varchar severity "low,medium,high,critical"
boolean is_active
jsonb parameters
}
```

## RECOMMENDATIONS

```text
RECOMMENDATIONS {
bigint recommendation_id PK
int rule_id FK
bigint resource_id FK
bigint project_id FK
varchar recommendation_type
varchar problem_description
varchar suggested_action
decimal estimated_monthly_saving
varchar risk_level
varchar status "detected,under_review,approved,rejected,deferred,executed,validated,closed"
timestamp detected_at
timestamp expires_at
}
```

## RECOMMENDATION_EVIDENCE

```text
RECOMMENDATION_EVIDENCE {
bigint evidence_id PK
bigint recommendation_id FK
int metric_type_id FK "NULLABLE"
timestamp observation_start
timestamp observation_end
decimal observed_value
decimal threshold_value
text narrative
}
```

## APPROVAL_REQUESTS

```text
APPROVAL_REQUESTS {
bigint approval_id PK
bigint recommendation_id FK
bigint requested_by FK
bigint reviewed_by FK "NULLABLE"
varchar decision "approved,rejected,deferred,NULL=pending"
varchar comments
timestamp requested_at
timestamp reviewed_at
}
```

## RECOMMENDATION_STATUS_HISTORY

```text
RECOMMENDATION_STATUS_HISTORY {
bigint history_id PK
bigint recommendation_id FK
bigint changed_by FK
varchar old_status
varchar new_status
timestamp changed_at
varchar reason
}
```

## RECOMMENDATION_ACTIONS

```text
RECOMMENDATION_ACTIONS {
bigint action_id PK
bigint recommendation_id FK
bigint executed_by FK
varchar action_type "stop,terminate,detach,delete"
varchar execution_status "in_progress,succeeded,failed"
timestamp started_at
timestamp completed_at
jsonb error_payload
}
```

## SAVINGS_REALIZATION

```text
SAVINGS_REALIZATION {
bigint realization_id PK
bigint recommendation_id FK
decimal baseline_monthly_cost
decimal actual_monthly_cost
decimal estimated_saving
decimal actual_saving
decimal realization_percentage
date measurement_start
date measurement_end
timestamp calculated_at
}
```

## BUDGET_ALERTS

```text
BUDGET_ALERTS {
bigint alert_id PK
bigint budget_id FK
timestamp triggered_at
decimal threshold_percentage_reached
decimal current_spend
decimal budget_amount
boolean is_acknowledged
}
```

## AUDIT_LOGS

```text
AUDIT_LOGS {
bigint audit_id PK
bigint user_id FK "NULLABLE for system"
varchar action_type
varchar entity_type
bigint entity_id
jsonb old_state
jsonb new_state
timestamp action_timestamp
varchar client_ip
}
```
