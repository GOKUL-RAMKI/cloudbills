# clouDBills ER - Mermaid Views

Rendered with `mermaid-diagrams` skill (`erDiagram`).

> Full attribute list lives in `docs/clouDBills_Architecture_v2.md` Section 2.

## The 2 requested outputs
- `00_full_no_attrs.mmd` - SINGLE diagram, relationships only, 48 rels, 0 attribute blocks. Paste into https://mermaid.live for clean overview.
- `ENTITY_LIST.md` - 33 entities + attributes separately, NOT as graph. Text reference.
- `00_full_with_attrs.mmd` - SINGLE diagram WITH attributes in graph, 33 blocks, 328 lines. Dense by design - zoom in Live Editor.

## Layer splits (readable review)
- `01_master_org.mmd` - providers, accounts, users/roles, teams/projects/environments, regions, types
- `02_inventory_graph.mmd` - resources, tags (1NF), dependencies self-graph, consumers/allocated_costs, status history
- `03_ingestion_timeseries.mmd` - batches/staging/errors/records (RANGE partitioned), metric_types/metrics, daily summaries
- `04_governance_workflow.mmd` - rules/recommendations/evidence/approvals(FOR UPDATE)/history/actions/savings/budgets/alerts/audit

## How to preview
Paste any `.mmd` into https://mermaid.live, or open in VS Code with Mermaid extension.

## Key
- `||--o{` = one-to-many, `|o--o{` = optional one-to-many, `|o--||` = optional one-to-one
- `PK` surrogate vs `FK PK` composite bridge vs `UK` candidate key
- Recursive: `RESOURCES -> RESOURCE_DEPENDENCIES -> RESOURCES` enforces `parent<>child`
- Partial unique (not drawable): `UNIQUE(recommendation_id) WHERE decision IS NULL`
- Partition PKs: `(billing_id, usage_start)`, `(metric_id, measured_at)`
