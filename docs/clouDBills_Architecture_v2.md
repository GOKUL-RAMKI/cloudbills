# clouDBills: Technical Architecture, Relational Specification, and DBMS Engineering

clouDBills is a specialized database system built to solve cloud financial governance (FinOps) challenges at the relational layer. Cloud cost management involves high-throughput time-series metrics, semi-structured billing streams, multi-tenant cost attribution graphs, and strict transactional state machines. 

The system transitions raw, unvalidated cloud provider billing records and infrastructure utilization metrics into normalized relational structures, runs declarative rule-based waste detection queries, coordinates concurrent remediation approvals, and tracks verified cost savings.

---

## 1. Problem Statement (PS)

### The Cloud Economics Problem
Modern cloud-native enterprises operate across thousands of ephemeral resources distributed over multiple accounts, regions, and cloud providers (AWS, GCP, Azure). This operational scale creates major cost inefficiencies:
- **Compute Waste:** Compute instances running at near-zero CPU and memory utilization continuously (e.g., non-production instances left running outside business hours).
- **Orphaned Storage:** Persistent volumes, block storage snapshots, and static IP addresses retained after their parent instances are terminated.
- **Attribution Gaps:** Resources deployed without organizational tags (`Owner`, `Project`, `Environment`), resulting in untracked operational costs.
- **Multi-Tenant Sharing:** Shared infrastructure (e.g., Kubernetes clusters, transit gateways, database pools) billed as a single lump sum, preventing accurate cost attribution across internal business units.
- **Workflow Integrity Failures:** Cost optimization tools often recommend stopping or resizing resources without managing conflicting concurrent approvals, dependency risks, or tracking whether planned savings are actually achieved.

### The Core DBMS Challenge
Standard application architectures offload FinOps logic to external application servers, using databases merely as persistent key-value stores. This introduces major performance bottlenecks:
- Exporting millions of unaggregated billing rows over the network to application code causes network saturation, memory bloat, and serialization overhead.
- Cloud billing schemas across AWS, GCP, and Azure lack uniformity, requiring multi-stage in-database ingestion (ELT), structural validation, and casting.
- Resource utilization data generates append-heavy, high-velocity time-series streams that degrade write throughput and bloat standard B-Trees if tables are not properly partitioned.
- Resource optimization decisions require analytical query processing (OLAP), while the approval and remediation workflow demands strict transactional integrity, serialized locking, and comprehensive auditing (OLTP).

clouDBills solves these problems by moving ingestion, validation, graph traversal, analytical rollups, and concurrency control directly into the PostgreSQL relational engine.

```
+----------------------------------------------------------------------------------------------------+
|                                    clouDBills Engine Architecture                                 |
+----------------------------------------------------------------------------------------------------+
                                                  │
                 [ Layer 1: Ingestion & Staging (Untyped ELT) ]
                 - billing_staging (Raw CSV text ingestion via COPY)
                 - Validation Stored Procedure (plpgsql casting & integrity check)
                 - billing_import_batches / billing_import_errors
                                                  │
                                                  ▼
                 [ Layer 2: Master & Organizational Entities ]
                 - cloud_providers, cloud_accounts, teams, projects
                 - environments, regions, resource_types, users, roles
                                                  │
                                                  ▼
                 [ Layer 3: Operational & Dependency Graph ]
                 - resources, resource_tags (Normalized Key-Value)
                 - resource_dependencies (Recursive CTE Directed Graph)
                 - budgets, budget_alerts
                                                  │
                                                  ▼
                 [ Layer 4: Partitioned Time-Series Engine ]
                 - billing_records (Range Partitioned by usage_start)
                 - usage_metrics (Range Partitioned by measured_at)
                 - resource_daily_summaries / Materialized Views
                                                  │
                                                  ▼
                 [ Layer 5: Optimization & Transactional Workflow ]
                 - optimization_rules (Declarative parameter JSONB)
                 - recommendations & recommendation_evidence
                 - approval_requests (SELECT FOR UPDATE row-level locking)
                 - recommendation_status_history, recommendation_actions
                 - savings_realization & immutable audit_logs
```

---

## 2. Relational Entity Directory (All 33 Entities)

The schema is organized into four architectural tiers: Master Data, Operational Inventory, Ingestion & Time-Series Facts, and Decision, Governance & Audit. Three supporting bridge/allocation entities are also included: `user_roles`, `resource_consumers`, and `allocated_costs`.

```
       +--------------------+          +---------------------+
       |  cloud_providers   |<---------|   cloud_accounts    |
       +--------------------+          +---------------------+
                                                  │
                                                  ▼
       +--------------------+          +---------------------+          +----------------------+
       |       teams        |<---------|      projects       |<---------|      resources       |
       +--------------------+          +---------------------+          +----------------------+
                                                  │                                │    │    │
                     ┌────────────────────────────┴─────────────┐                  │    │    │
                     │                                          │                  │    │    │
                     ▼                                          ▼                  │    │    │
          +----------------------+                   +---------------------+       │    │    │
          |       budgets        |                   |   allocated_costs   |<──────┘    │    │
          +----------------------+                   +---------------------+            │    │
                     │                                                                  │    │
                     ▼                                                                  │    │
          +----------------------+                   +---------------------+            │    │
          |    budget_alerts     |                   |  resource_tags      |<───────────┘    │
          +----------------------+                   +---------------------+                 │
                                                                                             ▼
                                                     +---------------------+       +----------------------+
                                                     |    usage_metrics    |       |resource_dependencies |
                                                     +---------------------+       +----------------------+
                                                                │
                                                                ▼
                                                     +---------------------+
                                                     |   recommendations   |
                                                     +---------------------+
                                                      │        │        │
                     ┌────────────────────────────────┘        │        └────────────────────────────────┐
                     ▼                                         ▼                                         ▼
          +----------------------+                   +---------------------+                   +----------------------+
          |recommendation_evidence|                  |  approval_requests  |                   | savings_realization  |
          +----------------------+                   +---------------------+                   +----------------------+
```

### Layer 1: Master and Organizational Entities

#### 1. `cloud_providers`
- **Role:** Master catalog of public cloud vendors supported by the system.
- **Primary Key:** `provider_id` (INT, Surrogate)
- **Candidate Key:** `provider_name` (VARCHAR(50), Unique)
- **Key Attributes:** `api_endpoint_url`, `is_active`
- **Relationships:** 1-to-Many with `cloud_accounts`.

#### 2. `cloud_accounts`
- **Role:** Logical subscriptions, projects, or billing accounts associated with a provider.
- **Primary Key:** `account_id` (BIGINT, Surrogate)
- **Candidate Key:** (`provider_id`, `external_account_id`)
- **Foreign Keys:** `provider_id` $\to$ `cloud_providers(provider_id)`
- **Key Attributes:** `account_name`, `external_account_id`, `currency_code` (CHAR(3)), `is_active`
- **Relationships:** 1-to-Many with `resources`, `billing_import_batches`, `billing_records`.

#### 3. `users`
- **Role:** System actors who review, configure, and approve optimization recommendations.
- **Primary Key:** `user_id` (BIGINT, Surrogate)
- **Candidate Key:** `email` (VARCHAR(255), Unique)
- **Key Attributes:** `username`, `password_hash`, `first_name`, `last_name`, `is_active`, `created_at`
- **Relationships:** 1-to-Many with `approval_requests`, `billing_import_batches`, `audit_logs`.

#### 4. `roles`
- **Role:** Access control definitions (e.g., FinOps Admin, Team Lead, Read-Only Auditor).
- **Primary Key:** `role_id` (SERIAL, Surrogate)
- **Candidate Key:** `role_name` (VARCHAR(50), Unique)
- **Key Attributes:** `description`, `permissions` (JSONB)
- **Relationships:** Many-to-Many with `users` (via `user_roles`).

#### 5. `user_roles`
- **Role:** Bridge table implementing the many-to-many relationship between users and roles.
- **Primary Key:** (`user_id`, `role_id`) (Composite)
- **Foreign Keys:** `user_id` → `users(user_id)`, `role_id` → `roles(role_id)`
- **Key Attributes:** `assigned_at`
- **Purpose:** Prevents duplicate role assignments while preserving normalized RBAC membership.

#### 6. `teams`
- **Role:** Internal business departments responsible for resource management and spending.
- **Primary Key:** `team_id` (BIGINT, Surrogate)
- **Candidate Key:** `team_name` (VARCHAR(100), Unique), `cost_center` (VARCHAR(50), Unique)
- **Key Attributes:** `manager_user_id` (FK to `users`), `created_at`
- **Relationships:** 1-to-Many with `projects`, `budgets`.

#### 7. `projects`
- **Role:** Logical resource groupings mapping infrastructure components to business applications.
- **Primary Key:** `project_id` (BIGINT, Surrogate)
- **Candidate Key:** (`team_id`, `project_name`, `environment_id`)
- **Foreign Keys:** `team_id` $\to$ `teams(team_id)`, `environment_id` $\to$ `environments(environment_id)`
- **Key Attributes:** `project_name`, `description`, `is_active`, `created_at`
- **Relationships:** 1-to-Many with `resources`, `budgets`, `allocated_costs`.

#### 8. `environments`
- **Role:** Deployment stages used to set rule thresholds (e.g., dev instances can be stopped outside business hours; prod instances cannot).
- **Primary Key:** `environment_id` (SERIAL, Surrogate)
- **Candidate Key:** `environment_code` (VARCHAR(30), Unique; values: `development`, `testing`, `staging`, `production`)
- **Key Attributes:** `is_production_grade` (BOOLEAN)
- **Relationships:** 1-to-Many with `projects`.

#### 9. `regions`
- **Role:** Physical geographic deployment data centers.
- **Primary Key:** `region_id` (SERIAL, Surrogate)
- **Candidate Key:** (`provider_id`, `region_code`)
- **Foreign Keys:** `provider_id` $\to$ `cloud_providers(provider_id)`
- **Key Attributes:** `region_name`, `continent`, `is_active`
- **Relationships:** 1-to-Many with `resources`.

#### 10. `resource_types`
- **Role:** Standardized resource taxonomy (e.g., compute instance, block volume, SQL database, static IP).
- **Primary Key:** `type_id` (SERIAL, Surrogate)
- **Candidate Key:** (`provider_id`, `raw_service_code`, `raw_type_name`)
- **Foreign Keys:** `provider_id` $\to$ `cloud_providers(provider_id)`
- **Key Attributes:** `generic_category` (VARCHAR(50); values: `compute`, `storage`, `network`, `database`), `pricing_unit`
- **Relationships:** 1-to-Many with `resources`.

---

### Layer 2: Operational and Inventory Entities

#### 11. `resources`
- **Role:** The core inventory entity representing individual provisioned cloud resources.
- **Primary Key:** `resource_id` (BIGINT, Surrogate)
- **Candidate Key:** (`account_id`, `external_resource_id`)
- **Foreign Keys:** 
  - `account_id` $\to$ `cloud_accounts(account_id)`
  - `project_id` $\to$ `projects(project_id)` (Nullable for unallocated/orphaned resources)
  - `type_id` $\to$ `resource_types(type_id)`
  - `region_id` $\to$ `regions(region_id)`
- **Key Attributes:** `external_resource_id`, `resource_name`, `status` (VARCHAR(30); values: `active`, `stopped`, `deleted`, `unknown`), `created_at`, `deleted_at`, `hourly_cost` (NUMERIC(12,4))
- **Check Constraints:** `(status = 'deleted' AND deleted_at IS NOT NULL) OR (status <> 'deleted' AND deleted_at IS NULL)`
- **Relationships:** 1-to-Many with `resource_tags`, `usage_metrics`, `billing_records`, `recommendations`.

#### 12. `resource_tags`
- **Role:** Normalized storage of user-defined metadata key-value tags attached to resources.
- **Primary Key:** (`resource_id`, `tag_key`) (Composite)
- **Foreign Keys:** `resource_id` $\to$ `resources(resource_id)` ON DELETE CASCADE
- **Key Attributes:** `tag_key` (VARCHAR(128)), `tag_value` (VARCHAR(256))
- **Normalization:** Enforces 1NF; eliminates comma-delimited strings and JSON parsing for basic tag filtering.

#### 13. `resource_dependencies`
- **Role:** Directed graph representation of relationships between resources (e.g., Block Volume attached to Virtual Machine).
- **Primary Key:** (`parent_resource_id`, `child_resource_id`) (Composite)
- **Foreign Keys:** 
  - `parent_resource_id` $\to$ `resources(resource_id)`
  - `child_resource_id` $\to$ `resources(resource_id)`
- **Check Constraints:** `parent_resource_id <> child_resource_id`
- **Key Attributes:** `dependency_type` (VARCHAR(50); e.g., `attached_storage`, `elastic_ip_association`, `network_interface`), `created_at`
- **Purpose:** Prevents recommendations from marking volumes as orphaned when parent dependencies exist.

#### 14. `resource_consumers`
- **Role:** Allocation bridge defining which projects consume a shared resource.
- **Primary Key:** (`resource_id`, `project_id`) (Composite)
- **Foreign Keys:** `resource_id` → `resources(resource_id)`, `project_id` → `projects(project_id)`
- **Key Attributes:** `allocation_percentage` (NUMERIC(5,2))
- **Check Constraint:** `allocation_percentage > 0 AND allocation_percentage <= 100`
- **Purpose:** Supports proportional allocation of shared infrastructure costs.

#### 15. `resource_status_history`
- **Role:** Temporal record of state transitions for inventory items.
- **Primary Key:** `status_history_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** `resource_id` $\to$ `resources(resource_id)`
- **Key Attributes:** `previous_status`, `new_status`, `transition_time`, `reason`

---

### Layer 3: Ingestion and Time-Series Fact Entities

#### 16. `billing_import_batches`
- **Role:** Metadata registry for each bulk billing file ingestion operation.
- **Primary Key:** `batch_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** 
  - `account_id` $\to$ `cloud_accounts(account_id)`
  - `imported_by` $\to$ `users(user_id)`
- **Key Attributes:** `file_name`, `file_checksum` (CHAR(64)), `imported_at`, `row_count`, `accepted_count`, `rejected_count`, `status` (`uploaded`, `validating`, `validated`, `processed`, `failed`)
- **Relationships:** 1-to-Many with `billing_staging`, `billing_records`, `billing_import_errors`.

#### 17. `billing_staging`
- **Role:** Ephemeral landing table for raw, untyped CSV/JSON billing data.
- **Primary Key:** `staging_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** `batch_id` $\to$ `billing_import_batches(batch_id)`
- **Key Attributes:** All payload columns as `TEXT` (`provider_name`, `external_account_id`, `external_resource_id`, `service_name`, `usage_start_text`, `usage_end_text`, `usage_quantity_text`, `billed_cost_text`, `currency_code_text`)
- **Purpose:** Decouples ingestion from validation to support high-speed `COPY` operations.

#### 18. `billing_import_errors`
- **Role:** Quarantine log for billing rows that fail data type casting, business validations, or foreign key checks.
- **Primary Key:** `error_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** `batch_id` $\to$ `billing_import_batches(batch_id)`
- **Key Attributes:** `raw_row_index`, `raw_payload` (JSONB), `error_code`, `error_message`, `rejected_at`

#### 19. `billing_records`
- **Role:** Clean, typed, immutable financial fact table recording billed resource usage.
- **Primary Key:** (`billing_id`, `usage_start`) (Composite; required for PostgreSQL range partitioning)
- **Foreign Keys:** 
  - `account_id` $\to$ `cloud_accounts(account_id)`
  - `resource_id` $\to$ `resources(resource_id)` (Nullable)
  - `project_id` $\to$ `projects(project_id)` (Nullable)
  - `source_batch_id` $\to$ `billing_import_batches(batch_id)`
- **Key Attributes:** `service_name`, `usage_start` (TIMESTAMPTZ), `usage_end` (TIMESTAMPTZ), `usage_quantity` (NUMERIC(18,6)), `billed_cost` (NUMERIC(18,6)), `currency_code` (CHAR(3))
- **Check Constraints:** `usage_end > usage_start`, `billed_cost >= 0`, `usage_quantity >= 0`
- **Partitioning:** Declarative Range Partitioning by month on `usage_start`.

#### 20. `metric_types`
- **Role:** Metric definitions and units of measurement.
- **Primary Key:** `metric_type_id` (SERIAL, Surrogate)
- **Candidate Key:** `metric_name` (VARCHAR(80), Unique)
- **Key Attributes:** `unit` (VARCHAR(30); values: `percent`, `bytes`, `iops`, `count`), `aggregation_method` (`avg`, `max`, `sum`)
- **Relationships:** 1-to-Many with `usage_metrics`.

#### 21. `usage_metrics`
- **Role:** High-volume time-series facts for resource telemetry.
- **Primary Key:** (`metric_id`, `measured_at`) (Composite; required for range partitioning)
- **Foreign Keys:** 
  - `resource_id` $\to$ `resources(resource_id)`
  - `metric_type_id` $\to$ `metric_types(metric_type_id)`
- **Key Attributes:** `measured_at` (TIMESTAMPTZ), `metric_value` (NUMERIC(18,6))
- **Check Constraints:** `metric_value >= 0`
- **Partitioning:** Declarative Range Partitioning by month on `measured_at`.

#### 22. `resource_daily_summaries`
- **Role:** Daily aggregate table populated via rollups to optimize analytical queries without scanning raw metrics.
- **Primary Key:** (`resource_id`, `metric_type_id`, `summary_date`) (Composite)
- **Foreign Keys:** 
  - `resource_id` $\to$ `resources(resource_id)`
  - `metric_type_id` $\to$ `metric_types(metric_type_id)`
- **Key Attributes:** `avg_value` (NUMERIC(18,6)), `max_value` (NUMERIC(18,6)), `min_value` (NUMERIC(18,6)), `sample_count` (INT)

---

### Layer 4: Decision, Governance, and Audit Entities

#### 23. `optimization_rules`
- **Role:** Parameterized rules used to identify infrastructure waste.
- **Primary Key:** `rule_id` (SERIAL, Surrogate)
- **Candidate Key:** `rule_name` (VARCHAR(100), Unique)
- **Key Attributes:** `rule_type` (VARCHAR(50); e.g., `idle_compute`, `unattached_volume`, `missing_tag`), `description`, `severity` (`low`, `medium`, `high`, `critical`), `is_active`, `parameters` (JSONB)
- **JSONB Schema Example:**
  ```json
  {"threshold_cpu_percent": 5.0, "lookback_days": 21, "allowed_environments": ["development", "testing"]}
  ```

#### 24. `recommendations`
- **Role:** Actionable optimization proposals generated by detection queries.
- **Primary Key:** `recommendation_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** 
  - `rule_id` $\to$ `optimization_rules(rule_id)`
  - `resource_id` $\to$ `resources(resource_id)`
  - `project_id` $\to$ `projects(project_id)`
- **Key Attributes:** `recommendation_type`, `problem_description`, `suggested_action`, `estimated_monthly_saving` (NUMERIC(18,2)), `risk_level`, `status` (`detected`, `under_review`, `approved`, `rejected`, `deferred`, `executed`, `validated`, `closed`), `detected_at`, `expires_at`
- **Check Constraints:** `estimated_monthly_saving >= 0`
- **Relationships:** 1-to-Many with `recommendation_evidence`, `approval_requests`, `recommendation_status_history`.

#### 25. `recommendation_evidence`
- **Role:** Verifiable data points supporting a recommendation.
- **Primary Key:** `evidence_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** 
  - `recommendation_id` $\to$ `recommendations(recommendation_id)` ON DELETE CASCADE
  - `metric_type_id` $\to$ `metric_types(metric_type_id)` (Nullable)
- **Key Attributes:** `observation_start`, `observation_end`, `observed_value` (NUMERIC(18,6)), `threshold_value` (NUMERIC(18,6)), `narrative` (TEXT)

#### 26. `approval_requests`
- **Role:** Transactional entity tracking the sign-off process for recommendation execution.
- **Primary Key:** `approval_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** 
  - `recommendation_id` $\to$ `recommendations(recommendation_id)`
  - `requested_by` $\to$ `users(user_id)`
  - `reviewed_by` $\to$ `users(user_id)` (Nullable)
- **Key Attributes:** `decision` (`approved`, `rejected`, `deferred`), `comments`, `requested_at`, `reviewed_at`
- **Integrity Rule:** Partial Unique Index on `recommendation_id` where `decision IS NULL` (guarantees at most one active, pending review per recommendation).

#### 27. `recommendation_status_history`
- **Role:** Immutable audit trail tracking changes to recommendation states.
- **Primary Key:** `history_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** 
  - `recommendation_id` $\to$ `recommendations(recommendation_id)`
  - `changed_by` $\to$ `users(user_id)`
- **Key Attributes:** `old_status`, `new_status`, `changed_at`, `reason`

#### 28. `recommendation_actions`
- **Role:** Log of operational changes executed based on approved recommendations.
- **Primary Key:** `action_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** 
  - `recommendation_id` $\to$ `recommendations(recommendation_id)`
  - `executed_by` $\to$ `users(user_id)`
- **Key Attributes:** `action_type` (`stop_instance`, `terminate_instance`, `detach_volume`, `delete_volume`), `execution_status` (`in_progress`, `succeeded`, `failed`), `started_at`, `completed_at`, `error_payload` (JSONB)

#### 29. `savings_realization`
- **Role:** Financial ledger measuring actual savings against initial estimates after an action is executed.
- **Primary Key:** `realization_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** `recommendation_id` $\to$ `recommendations(recommendation_id)`
- **Key Attributes:** `baseline_monthly_cost` (NUMERIC(18,2)), `actual_monthly_cost` (NUMERIC(18,2)), `estimated_saving` (NUMERIC(18,2)), `actual_saving` (NUMERIC(18,2)), `realization_percentage` (NUMERIC(6,2)), `measurement_start` (DATE), `measurement_end` (DATE), `calculated_at`
- **Integrity Rule:** `measurement_end > measurement_start`

#### 30. `allocated_costs`
- **Role:** Materialized allocation ledger recording shared-resource cost assigned to a consuming project.
- **Primary Key:** `allocated_cost_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** `resource_id` → `resources(resource_id)`, `project_id` → `projects(project_id)`
- **Key Attributes:** `allocation_month` (DATE), `allocation_percentage` (NUMERIC(5,2)), `allocated_amount` (NUMERIC(18,2))
- **Purpose:** Preserves the calculated point-in-time allocation instead of recomputing historical allocations from mutable consumer mappings.

#### 31. `budgets`
- **Role:** Financial spend allocations assigned to teams or projects over a target billing period.
- **Primary Key:** `budget_id` (BIGSERIAL, Surrogate)
- **Candidate Key:** (`project_id`, `team_id`, `budget_month`)
- **Foreign Keys:** 
  - `project_id` $\to$ `projects(project_id)` (Nullable)
  - `team_id` $\to$ `teams(team_id)` (Nullable)
- **Check Constraints:** 
  - `(project_id IS NOT NULL OR team_id IS NOT NULL)` (Budget must belong to an entity)
  - `budget_amount > 0`
  - `warning_percentage > 0 AND warning_percentage <= 100`
- **Key Attributes:** `budget_month` (DATE), `budget_amount` (NUMERIC(18,2)), `warning_percentage` (NUMERIC(5,2))

#### 32. `budget_alerts`
- **Role:** Notifications generated when spending trends exceed defined budget thresholds.
- **Primary Key:** `alert_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** `budget_id` $\to$ `budgets(budget_id)`
- **Key Attributes:** `triggered_at`, `threshold_percentage_reached` (NUMERIC(5,2)), `current_spend` (NUMERIC(18,2)), `budget_amount` (NUMERIC(18,2)), `is_acknowledged` (BOOLEAN)

#### 33. `audit_logs`
- **Role:** Centralized, append-only log recording modifications to critical tables.
- **Primary Key:** `audit_id` (BIGSERIAL, Surrogate)
- **Foreign Keys:** `user_id` $\to$ `users(user_id)` (Nullable for background operations)
- **Key Attributes:** `action_type` (VARCHAR(80)), `entity_type` (VARCHAR(80)), `entity_id` (BIGINT), `old_state` (JSONB), `new_state` (JSONB), `action_timestamp` (TIMESTAMPTZ), `client_ip` (INET)

---

## 3. Relational Theory, Dependencies, and Normalization

A common issue in database design is denormalizing schemas too early to make reporting queries easier. clouDBills uses a Third Normal Form (3NF) design across all transactional tables, using controlled denormalization only through indexed Materialized Views.

```
+---------------------------------------------------------------------------------------------------+
|                                 Functional Dependency Progression                                 |
+---------------------------------------------------------------------------------------------------+

[ Unnormalized Raw Log ]
  file_row -> provider, account, project, team, resource, tags_string, cpu, disk, cost

                                                 │
                                 (Apply 1NF: Eliminate Multivalued Tags)
                                                 │
                                                 ▼
[ 1st Normal Form (1NF) ]
  resource_tags (resource_id, tag_key -> tag_value)
  Composite Primary Key eliminates comma-separated lists.

                                                 │
                                 (Apply 2NF: Remove Partial Dependencies)
                                                 │
                                                 ▼
[ 2nd Normal Form (2NF) ]
  Separate composite candidate keys:
  (account_id, external_resource_id) uniquely identifies attributes.
  Provider details depend only on provider_id, not on (provider_id, account_id).

                                                 │
                               (Apply 3NF: Remove Transitive Dependencies)
                                                 │
                                                 ▼
[ 3rd Normal Form (3NF) ]
  Remove: resource -> project_id -> team_id -> cost_center
  billing_records retains ONLY project_id.
  teams and cost_centers are decoupled from resources and billing rows.
```

### Functional Dependency (FD) Analysis

#### Entity: `projects` and `teams`
- Consider the unnormalized structure: `(project_id, project_name, team_id, team_name, cost_center)`
- Functional Dependencies:
  - $FD_1: \text{project\_id} \rightarrow \text{project\_name}, \text{team\_id}$
  - $FD_2: \text{team\_id} \rightarrow \text{team\_name}, \text{cost\_center}$
- Analysis:
  - The candidate key is $\text{project\_id}$.
  - In $FD_2$, $\text{team\_name}$ and $\text{cost\_center}$ are determined by $\text{team\_id}$, which is not a superkey of the relation.
  - This transitive dependency violates 3NF and causes update anomalies: changing a team's cost center requires updating every related project row.
- **Resolution:** Decompose into `projects` ($\text{project\_id}, \text{project\_name}, \text{team\_id}$) and `teams` ($\text{team\_id}, \text{team\_name}, \text{cost\_center}$). Both relations now satisfy BCNF because every determinant is a candidate key.

#### Entity: `resource_tags`
- Storing tags as delimited strings (`"env=prod;owner=db_team;tier=1"`) violates 1NF because the field contains non-atomic, repeating values.
- Moving tags to a JSON block inside the `resources` table satisfies 1NF technically, but prevents index-backed foreign key checks and standard unique constraints on tag keys.
- **Resolution:** Normalize into `resource_tags` with the composite primary key $(\text{resource\_id}, \text{tag\_key})$.
  - Dependency: $(\text{resource\_id}, \text{tag\_key}) \rightarrow \text{tag\_value}$.
  - This satisfies BCNF: the determinant is the candidate key, eliminating multivalued dependencies (4NF compliant).

#### Entity: `billing_records`
- Raw billing CSV exports often include redundant account, project, and team metadata:
  $$\text{record} \rightarrow (\text{external\_resource\_id}, \text{account\_name}, \text{provider\_name}, \text{team\_name}, \text{cost})$$
- This creates multiple transitive dependencies:
  - $\text{billing\_id} \rightarrow \text{resource\_id}$
  - $\text{resource\_id} \rightarrow \text{account\_id} \rightarrow \text{provider\_id} \rightarrow \text{provider\_name}$
  - $\text{resource\_id} \rightarrow \text{project\_id} \rightarrow \text{team\_id}$
- **Resolution:** Store direct references in `billing_records` while treating `project_id` as **point-in-time billing attribution**. The resource's current project may change after the billing event; retaining the attributed project on the immutable fact preserves historical financial ownership. This is controlled denormalization for temporal correctness, not duplication of current master data.
  $$\text{billing\_records}(\underline{\text{billing\_id}, \text{usage\_start}}, \text{account\_id}, \text{resource\_id}, \text{project\_id}, \text{billed\_cost}, \dots)$$

---

## 4. Advanced DBMS Concepts Implemented

```
+--------------------------------------------------------------------------------------------------+
|                            PostgreSQL Advanced Database Mechanisms                               |
+--------------------------------------------------------------------------------------------------+

 1. Declarative Range Partitioning
    billing_records ──► [2026_09] [2026_10] [2026_11]
    Query Engine: Run-time Partition Pruning (eliminates unqueried monthly tables from execution plan)

 2. Relational ELT Staging Pipeline
    billing_staging (Raw TEXT) ──► Validation Proc ──► Type Casting ──► billing_records (Clean)
                                                 └──► Check Violations ──► billing_import_errors

 3. Materialized Views with Concurrent Refresh
    Aggregated views over historical partitions refreshed via REFRESH MATERIALIZED VIEW CONCURRENTLY

 4. Concurrency Control & Row-Level Locking
    Session A: SELECT ... FOR UPDATE (Acquires Exclusive Row Lock) ──► State: 'approved'
    Session B: Waits on lock ──► Reads committed state 'approved' ──► Aborts duplicate approval

 5. Declarative Constraints & Triggers
    - Partial Unique Indexes (at most one pending approval request per recommendation)
    - Domain Check Constraints (non-negative currency, valid state enumerations)
    - Trigger Functions (automatic audit logging with JSONB old/new deltas)
```

### Declarative Range Partitioning and Partition Pruning
The `billing_records` and `usage_metrics` tables handle millions of rows per month. Storing this data in a single table degrades performance:
- B-Tree indexes expand beyond available shared memory buffers (`shared_buffers`), requiring random disk reads for lookups.
- Deleting expired billing data via `DELETE` causes table bloat and requires expensive `VACUUM` passes.

clouDBills implements declarative range partitioning keyed on timestamp values:

```sql
CREATE TABLE billing_records (
    billing_id BIGSERIAL,
    account_id BIGINT NOT NULL REFERENCES cloud_accounts(account_id),
    resource_id BIGINT REFERENCES resources(resource_id),
    project_id BIGINT REFERENCES projects(project_id),
    service_name VARCHAR(100) NOT NULL,
    usage_start TIMESTAMPTZ NOT NULL,
    usage_end TIMESTAMPTZ NOT NULL,
    usage_quantity NUMERIC(18,6) NOT NULL CHECK (usage_quantity >= 0),
    billed_cost NUMERIC(18,6) NOT NULL CHECK (billed_cost >= 0),
    currency_code CHAR(3) NOT NULL,
    source_batch_id BIGINT NOT NULL REFERENCES billing_import_batches(batch_id),
    PRIMARY KEY (billing_id, usage_start),
    CHECK (usage_end > usage_start)
) PARTITION BY RANGE (usage_start);

-- Monthly Sub-Partition Allocations
CREATE TABLE billing_records_2026_09 PARTITION OF billing_records
    FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');

CREATE TABLE billing_records_2026_10 PARTITION OF billing_records
    FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');

CREATE TABLE billing_records_2026_11 PARTITION OF billing_records
    FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');

-- Defensive catch-all for dates outside currently provisioned monthly partitions.
-- A scheduled partition-maintenance job should create the correct monthly partition
-- before high-volume ingestion; the DEFAULT partition prevents otherwise valid rows
-- from failing solely because a future partition has not yet been created.
CREATE TABLE billing_records_default PARTITION OF billing_records DEFAULT;
```

When analytical queries specify bounds on `usage_start`, the PostgreSQL query planner evaluates constraint expressions and eliminates non-matching partitions (partition pruning). 

```text
Query: SELECT SUM(billed_cost) FROM billing_records WHERE usage_start >= '2026-10-01' AND usage_start < '2026-11-01';

Execution Plan (Without Pruning):
Append -> Seq Scan on billing_records_2026_09 + 2026_10 + 2026_11 (Expensive)

Execution Plan (With Run-time Pruning):
Seq Scan on billing_records_2026_10 (Partitions 2026_09 and 2026_11 completely omitted)
```

Data retention can be managed using simple DDL operations: dropping an expired partition (`DROP TABLE billing_records_2024_01;`) instantly frees disk space without generating transaction log bloat.

### Staging, Validation, and ELT Ingestion Pipelines
To handle semi-structured, dirty billing exports without failing transactions, the database uses a two-tier ELT design:

```sql
-- Untyped Ingestion Landing
CREATE TABLE billing_staging (
    staging_id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES billing_import_batches(batch_id),
    provider_name TEXT,
    external_account_id TEXT,
    external_resource_id TEXT,
    service_name TEXT,
    usage_start_text TEXT,
    usage_end_text TEXT,
    usage_quantity_text TEXT,
    billed_cost_text TEXT,
    currency_code TEXT
);
```

The database validates and transforms this data internally using a stored procedure:

```sql
CREATE OR REPLACE PROCEDURE process_billing_batch(p_batch_id BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    v_accepted INT := 0;
    v_rejected INT := 0;
    r RECORD;
    v_account_id BIGINT;
    v_resource_id BIGINT;
    v_project_id BIGINT;
    v_start TIMESTAMPTZ;
    v_end TIMESTAMPTZ;
    v_qty NUMERIC(18,6);
    v_cost NUMERIC(18,6);
BEGIN
    UPDATE billing_import_batches SET status = 'validating' WHERE batch_id = p_batch_id;

    FOR r IN SELECT * FROM billing_staging WHERE batch_id = p_batch_id LOOP
        BEGIN
            -- 1. Validate and Cast Data Types
            v_start := r.usage_start_text::TIMESTAMPTZ;
            v_end := r.usage_end_text::TIMESTAMPTZ;
            v_qty := r.usage_quantity_text::NUMERIC(18,6);
            v_cost := r.billed_cost_text::NUMERIC(18,6);

            IF v_end <= v_start OR v_cost < 0 OR v_qty < 0 THEN
                RAISE EXCEPTION 'Constraint violation: Check bounds or values failed.';
            END IF;

            -- 2. Validate Foreign Keys
            SELECT a.account_id INTO v_account_id
            FROM cloud_accounts a
            JOIN cloud_providers p ON a.provider_id = p.provider_id
            WHERE p.provider_name = r.provider_name 
              AND a.external_account_id = r.external_account_id;

            IF v_account_id IS NULL THEN
                RAISE EXCEPTION 'Foreign key error: Account % not found for provider %', 
                    r.external_account_id, r.provider_name;
            END IF;

            -- 3. Resolve Resource and Project References
            SELECT resource_id, project_id INTO v_resource_id, v_project_id
            FROM resources
            WHERE account_id = v_account_id AND external_resource_id = r.external_resource_id;

            -- 4. Insert into the Target Partitioned Table
            INSERT INTO billing_records (
                account_id, resource_id, project_id, service_name,
                usage_start, usage_end, usage_quantity, billed_cost,
                currency_code, source_batch_id
            ) VALUES (
                v_account_id, v_resource_id, v_project_id, r.service_name,
                v_start, v_end, v_qty, v_cost,
                UPPER(TRIM(r.currency_code)), p_batch_id
            );

            v_accepted := v_accepted + 1;

        EXCEPTION WHEN OTHERS THEN
            -- Isolate errors to quarantine table without failing the transaction
            INSERT INTO billing_import_errors (
                batch_id, raw_row_index, raw_payload, error_code, error_message
            ) VALUES (
                p_batch_id, r.staging_id, to_jsonb(r), SQLSTATE, SQLERRM
            );
            v_rejected := v_rejected + 1;
        END;
    END LOOP;

    -- Update batch metrics and purge staging rows
    UPDATE billing_import_batches
    SET accepted_count = v_accepted,
        rejected_count = v_rejected,
        status = CASE WHEN v_rejected > 0 AND v_accepted = 0 THEN 'failed' ELSE 'processed' END
    WHERE batch_id = p_batch_id;

    DELETE FROM billing_staging WHERE batch_id = p_batch_id;
END;
$$;
```

### Ingestion Design Trade-off

The validation procedure intentionally processes staging rows inside a PL/pgSQL loop so that each invalid row can be quarantined independently without aborting the entire batch. This favors **row-level error isolation and demonstrability** over maximum bulk-throughput.

For production-scale ingestion, the validation step should be evolved toward a set-based `INSERT ... SELECT` pipeline with typed conversion helpers or validated staging columns, while retaining the quarantine pattern for rejected rows.

### Materialized Views and Caching Strategies
Complex analytical aggregations across millions of rows can be slow during interactive reporting. The schema caches these aggregates using Materialized Views with unique composite indices, allowing background refreshes that do not block concurrent reads:

```sql
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
GROUP BY p.project_id, p.project_name, t.team_name, DATE_TRUNC('month', b.usage_start), b.currency_code;

-- Required for non-blocking CONCURRENTLY refresh operations
CREATE UNIQUE INDEX idx_mv_monthly_project_cost_pk
ON mv_monthly_project_cost(project_id, billing_month, currency_code);

-- Refresh invocation
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_project_cost;
```

### Concurrency Control and Transaction Isolation
When an administrator reviews a recommendation to terminate an idle server, race conditions can occur. If two administrators approve the same recommendation simultaneously, both transactions might attempt to issue deletion commands, creating inconsistent state:

$$\text{User A: Read State} \rightarrow \text{User B: Read State} \rightarrow \text{User A: Approve} \rightarrow \text{User B: Approve}$$

clouDBills solves this race condition using pessimistic row-level locking (`SELECT ... FOR UPDATE`) inside an explicit transaction block:

```sql
-- Create the pending approval request when a recommendation enters review.
INSERT INTO approval_requests (
    recommendation_id, requested_by, decision, requested_at
)
VALUES (
    :recommendation_id, :requesting_user_id, NULL, CURRENT_TIMESTAMP
);

-- At most one pending request can exist for a recommendation.

CREATE OR REPLACE PROCEDURE approve_optimization_recommendation(
    p_recommendation_id BIGINT,
    p_admin_user_id BIGINT,
    p_comments TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_current_status VARCHAR(30);
BEGIN
    -- 1. Acquire an exclusive row-level lock on the target recommendation
    SELECT status INTO v_current_status
    FROM recommendations
    WHERE recommendation_id = p_recommendation_id
    FOR UPDATE;

    -- If another transaction modified this row, we see its committed state
    IF v_current_status IS NULL THEN
        RAISE EXCEPTION 'Recommendation ID % does not exist.', p_recommendation_id;
    END IF;

    IF v_current_status <> 'under_review' AND v_current_status <> 'detected' THEN
        RAISE EXCEPTION 'Concurrency Conflict: Recommendation is currently in state "%". Expected "under_review".', v_current_status;
    END IF;

    -- 2. Lock and validate the active approval request.
    -- A pending request is created when the recommendation enters review.
    -- The partial unique index guarantees at most one pending request.
    PERFORM 1
    FROM approval_requests
    WHERE recommendation_id = p_recommendation_id
      AND decision IS NULL
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'No pending approval request exists for recommendation %.', p_recommendation_id;
    END IF;

    -- 3. Resolve the existing pending request.
    UPDATE approval_requests
    SET reviewed_by = p_admin_user_id,
        decision = 'approved',
        comments = p_comments,
        reviewed_at = CURRENT_TIMESTAMP
    WHERE recommendation_id = p_recommendation_id
      AND decision IS NULL;

    -- 4. Transition the recommendation state
    UPDATE recommendations
    SET status = 'approved'
    WHERE recommendation_id = p_recommendation_id;

    -- 5. Append to the immutable status history log
    INSERT INTO recommendation_status_history (
        recommendation_id, old_status, new_status, changed_by, reason
    ) VALUES (
        p_recommendation_id, v_current_status, 'approved', p_admin_user_id, p_comments
    );

    -- 6. Record the action in the system audit log
    INSERT INTO audit_logs (
        user_id, action_type, entity_type, entity_id, new_state
    ) VALUES (
        p_admin_user_id, 'APPROVE_RECOMMENDATION', 'recommendations', p_recommendation_id,
        jsonb_build_object('status', 'approved', 'comments', p_comments)
    );
END;
$$;
```

If Transaction B attempts to run `approve_optimization_recommendation` while Transaction A holds the lock, Transaction B halts until Transaction A executes `COMMIT`. Once Transaction A commits, Transaction B acquires the lock, re-reads the row, discovers `status = 'approved'`, and immediately aborts with an exception, preventing duplicate actions.

### Indexing Strategies: B-Tree, Composite, and Partial
Indexes speed up read operations but add write overhead on insert-heavy tables. clouDBills uses targeted indexing strategies designed for specific query patterns:

```sql
-- 1. Composite Index ordered by high-cardinality equality, then range
CREATE INDEX idx_billing_project_time
ON billing_records(project_id, usage_start);

-- 2. Partial/Filtered Index for finding untagged resources
-- Only indexes rows that match the predicate, keeping index size small
CREATE INDEX idx_resources_active_missing_owner
ON resources(resource_id)
WHERE status = 'active';

-- 3. Partial Unique Index to guarantee only one pending review per recommendation
CREATE UNIQUE INDEX idx_one_pending_approval_per_rec
ON approval_requests(recommendation_id)
WHERE decision IS NULL;

-- 4. B-Tree Covering Index using INCLUDE to satisfy index-only scans
CREATE INDEX idx_rec_perf_covering
ON recommendations(status, risk_level)
INCLUDE (estimated_monthly_saving);
```

---

## 5. Core SQL Implementation (Complex Queries and Procedures)

### Waste Detection: Low-CPU Idle Compute Query
This query uses Common Table Expressions (CTEs) and conditional aggregations to identify servers averaging less than 5% CPU utilization over a 21-day observation window in non-production environments.

```sql
WITH rule_config AS (
    SELECT
        COALESCE((parameters->>'threshold_cpu_percent')::NUMERIC, 5.0) AS threshold_cpu,
        COALESCE((parameters->>'lookback_days')::INT, 21) AS lookback_days,
        COALESCE((parameters->>'min_telemetry_fraction')::NUMERIC, 0.8) AS min_telemetry_fraction
    FROM optimization_rules
    WHERE rule_type = 'idle_compute'
      AND is_active = TRUE
    ORDER BY rule_id
    LIMIT 1
),
metric_eval AS (
    SELECT
        r.resource_id,
        r.resource_name,
        r.project_id,
        r.hourly_cost,
        AVG(um.metric_value) AS avg_cpu,
        MAX(um.metric_value) AS max_cpu,
        COUNT(um.metric_id) AS total_samples,
        rc.lookback_days,
        rc.threshold_cpu
    FROM resources r
    JOIN projects p ON r.project_id = p.project_id
    JOIN environments e ON p.environment_id = e.environment_id
    JOIN usage_metrics um ON r.resource_id = um.resource_id
    JOIN metric_types mt ON um.metric_type_id = mt.metric_type_id
    CROSS JOIN rule_config rc
    WHERE mt.metric_name = 'cpu_utilization'
      AND r.status = 'active'
      AND e.is_production_grade = FALSE
      AND um.measured_at >= CURRENT_TIMESTAMP - make_interval(days => rc.lookback_days)
    GROUP BY r.resource_id, r.resource_name, r.project_id, r.hourly_cost,
             rc.lookback_days, rc.threshold_cpu, rc.min_telemetry_fraction
    HAVING AVG(um.metric_value) < threshold_cpu
       AND COUNT(um.metric_id) >= (lookback_days * 24 * min_telemetry_fraction)
)
SELECT
    me.resource_id,
    me.resource_name,
    me.project_id,
    ROUND(me.avg_cpu, 2) AS average_cpu_percent,
    ROUND(me.max_cpu, 2) AS peak_cpu_percent,
    ROUND(me.hourly_cost * 730, 2) AS estimated_monthly_waste
FROM metric_eval me
LEFT JOIN recommendations rec ON me.resource_id = rec.resource_id
    AND rec.status IN ('detected', 'under_review', 'approved')
WHERE rec.recommendation_id IS NULL;
```

### Recursive Dependency Graph Traversal
Before recommending that an unused resource be deleted, the system evaluates its dependency graph to ensure no active resources depend on it. This query uses a Recursive CTE to traverse dependency relationships and identify parent attachments:

```sql
WITH RECURSIVE dependency_chain AS (
    -- Anchor Member: Select the initial target candidate resource
    SELECT
        rd.parent_resource_id,
        rd.child_resource_id,
        rd.dependency_type,
        1 AS depth_level
    FROM resource_dependencies rd
    WHERE rd.child_resource_id = 40592 -- Target resource candidate for termination

    UNION ALL

    -- Recursive Member: Traverse upstream to find all parent dependencies
    SELECT
        rd.parent_resource_id,
        rd.child_resource_id,
        rd.dependency_type,
        dc.depth_level + 1
    FROM resource_dependencies rd
    JOIN dependency_chain dc ON rd.child_resource_id = dc.parent_resource_id
    WHERE dc.depth_level < 10 -- Prevent infinite cycles in malformed graphs
)
SELECT
    dc.depth_level,
    dc.dependency_type,
    r.resource_id AS dependent_parent_id,
    r.resource_name AS parent_name,
    r.status AS parent_status
FROM dependency_chain dc
JOIN resources r ON dc.parent_resource_id = r.resource_id
WHERE r.status = 'active'; -- Returns active parents that block safe termination
```

### Shared Multi-Tenant Cost Allocation Query
Cloud platforms often bill shared resources (such as Kubernetes clusters or shared databases) to a single infrastructure account. This query divides those shared costs among consuming projects based on defined allocation percentages:

```sql
WITH raw_shared_spend AS (
    SELECT
        b.resource_id,
        DATE_TRUNC('month', b.usage_start)::DATE AS spend_month,
        SUM(b.billed_cost) AS total_shared_cost
    FROM billing_records b
    JOIN resources r ON b.resource_id = r.resource_id
    WHERE r.project_id IS NULL -- Unassigned or globally shared infrastructure
    GROUP BY b.resource_id, DATE_TRUNC('month', b.usage_start)
)
SELECT
    rss.spend_month,
    r.resource_name AS shared_infra_name,
    p.project_name AS consumer_project,
    t.team_name AS responsible_team,
    rc.allocation_percentage,
    ROUND(rss.total_shared_cost * (rc.allocation_percentage / 100.0), 2) AS allocated_amount
FROM raw_shared_spend rss
JOIN resources r ON rss.resource_id = r.resource_id
JOIN resource_consumers rc ON r.resource_id = rc.resource_id
JOIN projects p ON rc.project_id = p.project_id
JOIN teams t ON p.team_id = t.team_id
ORDER BY rss.spend_month DESC, allocated_amount DESC;
```

---

## 6. Execution Plan Analysis: Index & Partition Validation

To demonstrate database performance optimization, run query execution plans using `EXPLAIN (ANALYZE, BUFFERS)` to compare scans across unindexed, indexed, and partitioned data structures.

```
+---------------------------------------------------------------------------------------------------+
|                        Execution Plan Performance Comparison (PostgreSQL)                         |
+---------------------------------------------------------------------------------------------------+

Scenario A: Unindexed Heap Table Scan
----------------------------------------------------------------------------------------------------
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM billing_records WHERE project_id = 42 AND usage_start >= '2026-10-01';

-> Seq Scan on billing_records (cost=0.00..84520.10 rows=480 width=128)
     Filter: ((project_id = 42) AND (usage_start >= '2026-10-01'::timestamptz))
     Rows Removed by Filter: 2,450,000
   Buffers: shared hit=1240 read=32450
   Execution Time: 412.384 ms  <-- [High Latency: Reads entire heap from disk]


Scenario B: Pruned Partition with Composite B-Tree Index Scan
----------------------------------------------------------------------------------------------------
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM billing_records WHERE project_id = 42 AND usage_start >= '2026-10-01' AND usage_start < '2026-11-01';

-> Append (cost=0.42..24.85 rows=480 width=128)
     -> Index Scan using idx_billing_p2026_10_project_time on billing_records_2026_10
          Index Cond: ((project_id = 42) AND (usage_start >= '2026-10-01') AND (usage_start < '2026-11-01'))
        Buffers: shared hit=18 read=2
   Execution Time: 1.142 ms    <-- [Low Latency: Partition pruned + Index hit]
```

### Plan Analysis Insights
1. **Buffer Read Reductions:** In Scenario A, the query engine scans 33,690 disk blocks because it evaluates every record in the table sequentially. In Scenario B, the engine accesses only 20 blocks by using an index lookup on the relevant partition.
2. **Elimination of Filter Overhead:** Scenario A removes 2.45 million rows via runtime filtering. Scenario B reads only the matching rows directly from the leaf nodes of the B-Tree index.
3. **Partition Pruning Impact:** The partition bounds in the `WHERE` clause prune the 2026_09 and 2026_11 tables from the query plan entirely, reducing memory and buffer usage during the scan.

---

## 7. Security, Auditing, and Multi-Tenancy

```
+---------------------------------------------------------------------------------------------------+
|                            PostgreSQL Enterprise Security Layers                                  |
+---------------------------------------------------------------------------------------------------+

 1. Identity & Role-Based Access Control (RBAC)
    - finops_analyst  ──► Granted SELECT on inventory, metrics, and recommendations
    - finops_admin    ──► Granted EXECUTE on approval and execution stored procedures
    - etl_service     ──► Granted INSERT/UPDATE on staging and import batch tables

 2. Row-Level Security (RLS) - Tenant Isolation
    - projects, budgets, and recommendations enforce:
      USING (team_id = NULLIF(current_setting('app.current_team_id', true), '')::BIGINT)

 3. Automated Trigger-Based Audit Trails
    - Target Table Mutation (INSERT, UPDATE, DELETE)
    - Trigger executes log_audit_trail_trigger()
    - Inserts user context, timestamps, and row states into audit_logs
```

### Row-Level Security (RLS) for Multi-Tenancy
To prevent cross-tenant data access in multi-team environments, clouDBills enables Row-Level Security on core organizational tables:

```sql
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE budgets ENABLE ROW LEVEL SECURITY;

-- Restrict access so team members can only view projects assigned to their team
CREATE POLICY team_isolation_policy ON projects
    FOR ALL
    TO finops_analyst, finops_admin
    USING (team_id = NULLIF(current_setting('app.current_team_id', true), '')::BIGINT)
    WITH CHECK (team_id = NULLIF(current_setting('app.current_team_id', true), '')::BIGINT);

CREATE POLICY team_budget_isolation_policy ON budgets
    FOR ALL
    TO finops_analyst, finops_admin
    USING (
        COALESCE(
            team_id,
            (SELECT p.team_id FROM projects p WHERE p.project_id = budgets.project_id)
        ) = NULLIF(current_setting('app.current_team_id', true), '')::BIGINT
    )
    WITH CHECK (
        COALESCE(
            team_id,
            (SELECT p.team_id FROM projects p WHERE p.project_id = budgets.project_id)
        ) = NULLIF(current_setting('app.current_team_id', true), '')::BIGINT
    );

ALTER TABLE recommendations ENABLE ROW LEVEL SECURITY;

CREATE POLICY team_recommendation_isolation_policy ON recommendations
    FOR ALL
    TO finops_analyst, finops_admin
    USING (
        EXISTS (
            SELECT 1
            FROM projects p
            WHERE p.project_id = recommendations.project_id
              AND p.team_id = NULLIF(current_setting('app.current_team_id', true), '')::BIGINT
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM projects p
            WHERE p.project_id = recommendations.project_id
              AND p.team_id = NULLIF(current_setting('app.current_team_id', true), '')::BIGINT
        )
    );
```

### Automated Audit Trigger
This trigger automatically captures changes made to system entities and writes the previous and updated row values as JSONB payloads into an append-only audit log:

```sql
CREATE OR REPLACE FUNCTION log_audit_trail_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_id BIGINT;
BEGIN
    -- Extract the acting user ID from the session context, if available
    v_user_id := NULLIF(current_setting('app.current_user_id', true), '')::BIGINT;

    IF (TG_OP = 'DELETE') THEN
        INSERT INTO audit_logs (
            user_id, action_type, entity_type, entity_id, old_state, action_timestamp
        ) VALUES (
            v_user_id, 'DELETE', TG_TABLE_NAME,
            COALESCE((to_jsonb(OLD)->>'recommendation_id')::BIGINT,
                     (to_jsonb(OLD)->>'resource_id')::BIGINT,
                     (to_jsonb(OLD)->>'project_id')::BIGINT,
                     (to_jsonb(OLD)->>'budget_id')::BIGINT,
                     (to_jsonb(OLD)->>'batch_id')::BIGINT,
                     (to_jsonb(OLD)->>'user_id')::BIGINT),
            to_jsonb(OLD), CURRENT_TIMESTAMP
        );
        RETURN OLD;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO audit_logs (
            user_id, action_type, entity_type, entity_id, old_state, new_state, action_timestamp
        ) VALUES (
            v_user_id, 'UPDATE', TG_TABLE_NAME,
            COALESCE((to_jsonb(NEW)->>'recommendation_id')::BIGINT,
                     (to_jsonb(NEW)->>'resource_id')::BIGINT,
                     (to_jsonb(NEW)->>'project_id')::BIGINT,
                     (to_jsonb(NEW)->>'budget_id')::BIGINT,
                     (to_jsonb(NEW)->>'batch_id')::BIGINT,
                     (to_jsonb(NEW)->>'user_id')::BIGINT),
            to_jsonb(OLD), to_jsonb(NEW), CURRENT_TIMESTAMP
        );
        RETURN NEW;
    ELSIF (TG_OP = 'INSERT') THEN
        INSERT INTO audit_logs (
            user_id, action_type, entity_type, entity_id, new_state, action_timestamp
        ) VALUES (
            v_user_id, 'INSERT', TG_TABLE_NAME,
            COALESCE((to_jsonb(NEW)->>'recommendation_id')::BIGINT,
                     (to_jsonb(NEW)->>'resource_id')::BIGINT,
                     (to_jsonb(NEW)->>'project_id')::BIGINT,
                     (to_jsonb(NEW)->>'budget_id')::BIGINT,
                     (to_jsonb(NEW)->>'batch_id')::BIGINT,
                     (to_jsonb(NEW)->>'user_id')::BIGINT),
            to_jsonb(NEW), CURRENT_TIMESTAMP
        );
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$;

-- Attach the audit trigger to sensitive configuration and recommendation tables
CREATE TRIGGER trg_audit_recommendations
AFTER INSERT OR UPDATE OR DELETE ON recommendations
FOR EACH ROW EXECUTE FUNCTION log_audit_trail_trigger();
```

---

## 8. Summary of DBMS Concepts Demonstrated

This system design demonstrates the following DBMS concepts directly in the architecture or SQL shown above:

| Category | DBMS Technical Concepts Demonstrated |
| :--- | :--- |
| **Relational Data Modeling** | 3NF decomposition, BCNF examples, 1NF/4NF discussion for resource tags, surrogate vs. composite primary keys, self-referencing foreign keys, dependency-graph modeling. |
| **Integrity Constraints** | Composite candidate keys, domain/check constraints, referential integrity, `ON DELETE CASCADE`, partial unique indexes, conditional validation. |
| **Storage & Performance** | Declarative range partitioning, partition pruning, B-Tree composite indexes, partial indexes, covering indexes with `INCLUDE`, materialized views with concurrent refresh. |
| **Query Processing** | CTEs, recursive CTEs, aggregation, `HAVING`, multi-table joins, execution-plan analysis with `EXPLAIN (ANALYZE, BUFFERS)`. |
| **Transactions & Concurrency** | ACID transaction boundaries, MVCC concepts, pessimistic row-level locking with `SELECT ... FOR UPDATE`, race-condition prevention, rollback/error handling. |
| **Database Programmability** | PL/pgSQL procedures, trigger functions, exception handling, staging-to-fact ELT processing. |
| **Security & Governance** | Database roles, Row-Level Security (RLS), tenant isolation, trigger-based audit logging, session-context-based authorization. |

**Scope note:** Window functions, relational division, exclusion constraints, explicit isolation-level demonstrations, savepoints, and statement-level triggers are intentionally not claimed here because they are not demonstrated by a concrete example in this document.
