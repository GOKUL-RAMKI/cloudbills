# clouDBills — Tech Stack & Implementation Approaches

> Scope: 33 entity sets (locked, discussed with professor).
> Sources: `docs/Package guidelines.md`, `docs/clouDBills_Architecture_v2.md`,
> `docs/er/relational_schema.md`, `docs/er/schema.sql` (33 tables, 308 lines).

## 1. Decision — 3-Tier Architecture

| Tier | Tech (locked) | Owns |
|---|---|---|
| Tier 1 Presentation | HTML + CSS + vanilla JS `fetch()` | Pages only: dashboard, resources CRUD, approvals, budgets. No DB creds. |
| Tier 2 Application | Python **Flask 3.x** + `psycopg3` pool | Routing, shallow validation, auth id, `CALL proc()` / `SELECT func()`, CSV upload, pagination, HTTP mapping. |
| Tier 3 Data | **PostgreSQL 15+** + `plpgsql` | All truth: constraints, 6 procs, 4 funcs, 3 triggers, partitioning, indexes, MV. |

**Why Flask (not FastAPI):** team already knows Flask; CRUD-via-procs is identical
(`CALL approve_optimization_recommendation(%s,%s,%s)`); viva-answerable.
FastAPI is the newer async standard but adds Pydantic/DI/async surface with no
extra marks — guideline scores procs + triggers + 3NF + CRUD-from-UI.

**Entity count 33:** retained full FinOps scope (org → inventory → ingestion →
governance → audit) after discussion with professor. No 8–10 trim.

## 2. Tier 2 — Flask ops (orchestrate, never enforce truth)

- Routes: `GET /api/resources`, `POST /api/resources`, `POST /api/recommendations/<id>/approve|reject`,
  `GET /api/billing?project=&month=`, `POST /api/batches/upload`, `GET /api/budgets/alerts`.
- Each route: JSON in → required-field check → `CALL`/`SELECT` → `commit/rollback` → JSON out.
  HTTP map: unique → 409, FK → 400, `Concurrency Conflict` → 409, missing → 404.
- `backend/db.py`: `psycopg_pool.ConnectionPool`, single `get_conn()` helper.
- `backend/sql/*.sql`: versioned procs/funcs/triggers; DDL source = `docs/er/schema.sql`.
- Upload: accept CSV → `COPY billing_staging FROM STDIN (CSV)` → `CALL validate_billing_batch(batch_id)`.
- Never in Tier 2: cost math, approval state checks, orphan/tag logic, savings calc.
## 3. Tier 3 — PostgreSQL ops (all business truth, plpgsql)

**Constraints:** PK/FK with `ON UPDATE CASCADE / ON DELETE CASCADE|RESTRICT`,
`CHECK (billed_cost>=0, usage_end>usage_start, parent<>child, allocation 0-100,
budget_amount>0, project OR team NOT NULL)`, partial `UNIQUE(recommendation_id)
WHERE decision IS NULL`, `NUMERIC(18,6)` money, `JSONB`, `INET`, `TIMESTAMPTZ`.

**Procedures (writes, called from Flask):**
- `add_resource(...)` → `resources + resource_tags`
- `validate_billing_batch(batch_id)` → TEXT → `billing_records`, quarantine → `billing_import_errors`
- `approve_optimization_recommendation(rec_id, admin_id, comments)` → `SELECT ... FOR UPDATE`,
  state `under_review`, one-pending check, `UPDATE approval + recommendation`, `INSERT history + audit`
- `execute_recommendation_action(...)`, `record_savings(...)`, `create_budget(...)`

**Functions (reads):**
- `monthly_project_cost(p_id, month)` → `SUM(billed_cost)`
- `detect_idle_compute()` → CTE avg CPU <5% over 21d
- `dependency_chain(res_id)` → recursive CTE on `resource_dependencies`
- `orphaned_volumes()` / `missing_tags()` → anti-join / `NOT EXISTS`

**Triggers (guideline: 2+, we do 3):**
- `trg_audit_recommendations` → `audit_logs(old/new JSONB)`
- `trg_budget_alert` → spend > `warning%` → `budget_alerts`
- `trg_status_history` → auto `recommendation_status_history`

**Perf:** `PARTITION BY RANGE(usage_start/measured_at)` + pruning,
composite `(project_id, usage_start)`, partial `WHERE status='active'`,
`mv_monthly_project_cost REFRESH CONCURRENTLY`, `EXPLAIN(ANALYZE,BUFFERS)` proof.

## 4. Implementation approaches to mind

1. UI → Flask → DB only. JS `fetch('/api/...')`, never direct PG.
2. Every CRUD button maps to a proc (Create resource, Read billing func,
   Update approval proc, Delete tag via `add_resource` inverse).
3. Seed rich data: 3 providers, 5+ accounts, 20+ resources, 100+ billing rows,
   5+ recommendations to demo all flows.
4. Approve flow: JS POST → Flask `CALL` → Tier3 `FOR UPDATE` → 200 or 409.
5. Verify: `/api` Swagger-style list, 33 tables exist, 2 triggers fire,
   concurrent approves → one `Concurrency Conflict`.

## 5. Viva cheat-sheet

1. Why 3-tier? UI holds no creds; logic in Flask + procs.
2. Why psycopg3? Current driver, `COPY`, server cursors for billing bulk.
3. Why raw SQL not ORM? Guideline requires procs/functions; ORM hides them.
4. Approve race? `FOR UPDATE` + partial-unique pending.
5. Flask vs FastAPI? "FastAPI newer async standard; chose Flask for
   familiarity — proc calls identical."
6. Why 33 entities? Full FinOps coverage, discussed with professor.
