# Safety design

## Guarantees

1. **Read-only.** The guard's first check requires the statement to be a
   single `SELECT`. `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`,
   `CREATE`, `PRAGMA`, `ATTACH` and friends are rejected by keyword, and
   stacked statements (`;`) are rejected structurally.
2. **No hallucinated schema.** Every table/column reference is checked
   against `DatabaseSchema`. Unknown identifiers fail closed.
3. **No injection.** The parser emits parameterized queries only; user
   input travels in `params`, never in the SQL string.
4. **Bounded output.** A `LIMIT` cap (default 1000) is injected when the
   query has none.
5. **Bounded runtime.** Queries run under a SQLite progress-handler
   deadline (default 5s); overruns raise `QueryTimeout`.
6. **PII by role.** `analyst` (default) cannot select `customers.email`
   / `customers.phone`; `viewer` additionally cannot select
   `customers.name`. `admin` is unrestricted.

## What could still go wrong

- **Template coverage.** The parser only understands its intent
  templates; anything else clarifies rather than answers. In production
  this is where an LLM parser plugs in — behind the same guard.
- **Semantic misread.** "Revenue" here means `SUM(orders.total)`.
  Ambiguous business terms need a metric dictionary in production.
- **LIKE wildcards / regex DoS.** Not applicable: no user text reaches
  the SQL string.
- **Side channels.** Row counts and error messages can leak schema
  shape; acceptable for an internal analytics tool, not for a
  multi-tenant API without further hardening.
