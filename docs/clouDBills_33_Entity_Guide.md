# **clouDBills** 

## **Guide to All 33 Database Entities** 

What each entity holds, why it exists, and how it contributes to the system 

### **How to read this guide** 

For every entity, the guide explains (1) what information the table holds and (2) what job the table performs in clouDBills. PK means primary key; FK means foreign key. A composite primary key uses multiple attributes. Descriptions follow the supplied clouDBills architecture and relational schema. 

clouDBills • Entity guide  |  Page 1 

### **How the 33 entities fit together** 

Provider and organization tables identify accounts, teams and projects. Inventory tables describe resources and their dependencies. Import tables receive and validate billing data; fact tables store clean billing and telemetry. Optimization and governance tables turn evidence into recommendations, approvals, actions and savings measurements, while budget and audit tables support accountability. 

### **A. Master data and organization** 

#### **cloud_providers** 

**What it holds:** Stores provider name, API endpoint and active status. 

**What it is used for:** Identifies which cloud provider an account, region or resource type belongs to. 

#### **cloud_accounts** 

**What it holds:** Stores provider reference, account name, external account ID, currency and active status. 

**What it is used for:** Connects imported bills and resources to the correct cloud account. 

#### **users** 

**What it holds:** Stores user identity, email, username, password hash, name, active status and creation time. 

**What it is used for:** Identifies people who import data, request reviews, approve recommendations or perform actions. 

#### **roles** 

**What it holds:** Stores role names, descriptions and permission data. 

**What it is used for:** Defines permission sets that can be assigned to users. 

#### **user_roles** 

**What it holds:** Stores user-to-role assignments and assignment time; user_id + role_id form the composite PK. 

**What it is used for:** Implements many-to-many membership: one user can have several roles and each role can belong to several users. 

#### **teams** 

**What it holds:** Stores team name, cost centre, manager user and creation time. 

**What it is used for:** Represents organizational ownership for projects, budgets and cost attribution. 

#### **projects** 

**What it holds:** Stores project name, description, team, environment, active status and creation time. 

**What it is used for:** Groups cloud resources and helps attribute expenditure to an application or initiative. 

#### **environments** 

**What it holds:** Stores environment codes and whether they are production-grade. 

**What it is used for:** Distinguishes production from development or testing contexts when interpreting resource use. 

#### **regions** 

**What it holds:** Stores provider-specific region code, name, continent and active status. 

clouDBills • Entity guide  |  Page 2 

**What it is used for:** Records where a resource runs and supports region-based grouping. 

### **B. Inventory and resource relationships** 

#### **resource_types** 

**What it holds:** Stores provider-specific service/type codes and names, generic category and pricing unit. 

**What it is used for:** Maps provider-specific resource types into common categories for analysis and optimization rules. 

#### **resources** 

**What it holds:** Stores account, optional project, resource type, region, external resource ID, name, status, lifecycle timestamps and hourly cost. 

**What it is used for:** Central inventory of cloud resources; billing, usage metrics and recommendations can reference a resource. 

#### **resource_tags** 

**What it holds:** Stores individual resource tag key-value pairs; resource_id + tag_key form the composite PK. 

**What it is used for:** Keeps tags atomic and searchable, such as environment=test or owner=analytics. 

#### **resource_dependencies** 

**What it holds:** Stores directed parent-resource/child-resource links, dependency type and creation time; the two resource IDs form the composite PK. 

**What it is used for:** Represents dependencies such as a storage volume attached to a VM, preventing unsafe orphan-resource recommendations. 

#### **resource_consumers** 

**What it holds:** Stores which projects consume a shared resource and each project's allocation percentage; resource_id + project_id form the composite PK. 

**What it is used for:** Represents many-to-many shared-resource usage and supports proportional cost allocation. 

#### **resource_status_history** 

**What it holds:** Stores resource ID, previous/new status, transition time and reason. 

**What it is used for:** Preserves a timeline of resource state changes for troubleshooting and decision context. 

#### **allocated_costs** 

**What it holds:** Stores resource, project, allocation month, allocation percentage and allocated amount. 

**What it is used for:** Records how resource costs are allocated to projects; it stores financial values, not just a relationship. 

### **C. Billing ingestion and time-series facts** 

#### **billing_import_batches** 

**What it holds:** Stores account, importing user, file name/checksum, import time, row counts and batch status. 

clouDBills • Entity guide  |  Page 3 

**What it is used for:** Tracks each billing import and summarizes accepted/rejected rows so the source of records can be traced. 

#### **billing_staging** 

**What it holds:** Stores raw billing fields as TEXT: provider, external account/resource IDs, service, usage times, quantity, cost and currency. 

**What it is used for:** Landing table for bulk loading. Values can be checked and converted before being trusted as typed financial data. 

#### **billing_import_errors** 

**What it holds:** Stores batch, raw row index/payload, error code, message and rejection time. 

**What it is used for:** Quarantines invalid rows so malformed dates, costs or unresolved references can be investigated rather than silently discarded. 

#### **billing_records** 

**What it holds:** Stores validated account, optional resource/project attribution, source batch, service, usage interval, quantity, billed cost and currency. Composite PK: billing_id + usage_start. 

**What it is used for:** Trusted financial fact table for cost totals, trends and savings comparisons. Partitioned by usage_start. 

#### **metric_types** 

**What it holds:** Stores metric name, unit and aggregation method. 

**What it is used for:** Defines what a telemetry value means, for example CPU percentage, bytes or IOPS, and how it should be aggregated. 

#### **usage_metrics** 

**What it holds:** Stores resource, metric type, timestamp and measured value. Composite PK: metric_id + measured_at. 

**What it is used for:** Stores time-series utilization evidence used by optimization rules. Partitioned by measured_at. 

#### **resource_daily_summaries** 

**What it holds:** Stores daily average, maximum, minimum and sample count for each resource and metric type; key is resource_id + metric_type_id + summary_date. 

**What it is used for:** Makes daily utilization analysis faster without repeatedly scanning all raw measurements. 

### **D. Optimization, budgets, approvals and audit** 

#### **optimization_rules** 

**What it holds:** Stores rule name/type, description, severity, active status and configurable parameters. 

**What it is used for:** Defines conditions used to detect possible waste, such as low utilization over a configured observation window. 

#### **recommendations** 

**What it holds:** Stores rule, resource and project references, problem description, suggested action, estimated monthly saving, risk, status and timestamps. 

clouDBills • Entity guide  |  Page 4 

**What it is used for:** Represents an actionable finding and connects the detected issue to the proposed response. 

#### **recommendation_evidence** 

**What it holds:** Stores metric type, observation window, observed value, threshold and narrative for a recommendation. 

**What it is used for:** Explains why a recommendation was generated so reviewers can inspect evidence rather than trust an unexplained claim. 

#### **approval_requests** 

**What it holds:** Stores recommendation, requester, optional reviewer, decision, comments and request/review timestamps. 

**What it is used for:** Records whether a proposed action is approved or rejected; the workflow can protect concurrent decisions. 

#### **recommendation_status_history** 

**What it holds:** Stores recommendation ID, optional actor, old/new status, time and reason. 

**What it is used for:** Preserves the lifecycle of a recommendation from detection through review and subsequent stages. 

#### **recommendation_actions** 

**What it holds:** Stores recommendation, optional executor, action type/status, start/end times and error payload. 

**What it is used for:** Records what action was attempted and whether execution succeeded or failed; a recommendation itself is only a proposal. 

#### **savings_realization** 

**What it holds:** Stores baseline and actual monthly costs, estimated/actual savings, realization percentage, measurement period and calculation time; recommendation_id is unique. 

**What it is used for:** Measures financial results after an action and distinguishes projected savings from observed savings. 

#### **budgets** 

**What it holds:** Stores monthly budget amount and warning percentage, associated with a project, a team or the relevant scope. 

**What it is used for:** Defines spending limits against which actual costs can be compared. 

#### **budget_alerts** 

**What it holds:** Stores budget, trigger time, threshold reached, current spend, budget amount and acknowledgement status. 

**What it is used for:** Records when spending crosses a warning level and whether the alert has been acknowledged. 

#### **audit_logs** 

**What it holds:** Stores action type, entity type/ID, optional user, old/new state, timestamp and client IP. 

**What it is used for:** Provides a trace of important changes and helps answer who or what changed a record and when. 

clouDBills • Entity guide  |  Page 5 

### **End-to-end example** 

A billing CSV is registered in billing_import_batches and loaded into billing_staging. The validation process checks and converts each row. Invalid rows are recorded in billing_import_errors; valid rows are inserted into billing_records with internal account/resource references where available. Usage measurements are stored in usage_metrics and can be summarized in resource_daily_summaries. An optimization_rules entry evaluates the data and may create a recommendations record. Supporting observations go into recommendation_evidence. An approval decision is stored in approval_requests; an attempted action is recorded in recommendation_actions. After the measurement period, savings_realization stores the calculated financial result, while audit_logs records relevant changes. 

#### **The key distinction to remember** 

A table stores a particular kind of fact; it does not necessarily make the decision itself. billing_records stores what was charged, usage_metrics stores observed resource usage, optimization_rules defines conditions to evaluate, recommendations stores the proposed response, and savings_realization records the measured financial outcome. 

clouDBills • Entity guide  |  Page 6 

