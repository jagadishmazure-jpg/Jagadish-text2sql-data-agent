# Implementation guide: from offline build to Azure

> Status: **planned**. Nothing here has been deployed. The offline build in this
> repository is what exists today; this guide is how it would be taken to a
> real Azure environment.

## Azure service mapping

| Offline component | Azure service (planned) | Notes |
|---|---|---|
| SQLite sample DB | Azure SQL Database or Fabric Warehouse | Read-only login via managed identity |
| Template parser | Azure OpenAI with the schema description as context, emitting SQL + params | Guard stays as the validation gate |
| Role column rules | Entra ID group claims mapped to roles, plus database row-level security | Defence in depth |
| Agent host | Azure Container Apps | Behind API Management |
| Evaluation | CI job on every prompt or model change | Fail the build below the agreed accuracy |

## Steps

1. Point `run_question` at Azure SQL with a contained, read-only database user bound to a managed identity.
2. Add the LLM generator that receives `schema.describe_for_prompt()` and must return SQL plus parameters.
3. Keep `validate_sql` as the gate; port its checks to the target dialect and add tests.
4. Map Entra ID groups to roles and add row-level security in the database.
5. Grow the gold set with questions written by someone other than the developer; gate releases on it.

## Guardrails to keep when going live

- Read-only: one SELECT per request; DDL/DML keywords and stacked statements are rejected before execution.
- Identifiers must exist in the declared schema; invented tables or columns are refused.
- Role-based column allowlist keeps PII (email, phone) away from analyst and viewer roles.
- User values are bound parameters, never interpolated into SQL.
- Row cap (1000) and a query timeout bound every execution.
- Ambiguous questions get a clarify response instead of a guess.

## What to measure before calling it production-ready

- Execution accuracy on an independent gold set.
- Guard block rate on a hostile-query suite (target: every case blocked).
- Share of clarify responses and user satisfaction with them.
