# Jagadish-text2sql-data-agent

Natural language to SQL over structured data — addressing the explicitly
hard **text-to-SQL** problem: schema grounding, safe execution, query
validation, and result explanation.

## The problem

LLMs are fluent at prose and shaky at rows and columns. Point one at a
database and you get three failure modes: hallucinated tables/columns,
unbounded or destructive queries, and answers with no cited numbers.
This repo makes text-to-SQL production-safe with a deterministic core:

- **Schema grounding** — the parser may only reference tables/columns
  declared in `text2sql/schema.py`; the guard re-validates every query.
- **Template parsing** — intents (aggregations, filters, joins, top-N)
  map to *parameterized* SQL. User values are never interpolated.
- **Safety guard** — read-only enforcement (SELECT only), stacked
  statements rejected, DDL/DML keywords rejected, unknown identifiers
  rejected, role-based column allowlist (PII protection), LIMIT cap
  injected, query timeout via SQLite progress handler.
- **Clarify, don't guess** — unrecognized intents or missing slots
  return what was understood and what's ambiguous.
- **Explained results** — every answer cites the actual numbers.

## Architecture

```
question
  │
  ▼
parse.generate_sql() ──► parameterized SQL + interpretation
  │                       (or clarify response)
  ▼
guard.validate_sql() ──► read-only? known identifiers?
  │                       role allowed? LIMIT injected
  ▼
execute (SQLite) ──────► rows, with progress-handler timeout
  │
  ▼
explain_result() ──────► natural-language summary citing numbers
```

Full trace per question: interpretation, SQL, params, guard verdict,
columns, rows, explanation.

## Quickstart

```bash
pip install -e .
python -m text2sql.eval        # build sample DB + run 15-question gold set
pytest -q
```

```python
from text2sql.data import build_sample_db
from text2sql.execute import run_question

build_sample_db("retail.db")
t = run_question("Top 5 products by revenue", "retail.db")
print(t["sql"])
print(t["explanation"])
```

## Example walkthroughs

**Aggregation:**
> Q: *What is the total revenue in 2024?*
> SQL: `SELECT SUM(total) AS revenue FROM orders WHERE strftime('%Y', order_date) = ?`
> A: *Total revenue in 2024: $68,412.30.*

**Join:**
> Q: *Which customers have open tickets?*
> A: *Customers who have at least one open support ticket: 18 rows…*

**Clarify (not guess):**
> Q: *Show me the top products by revenue*
> A: *I understood: you want top products by revenue. To answer precisely
> I need: How many products? e.g. 'top 5 products by revenue'.*

**Blocked:**
> *DROP TABLE customers* → guard rejects: only SELECT queries are allowed.

## Production path

- Swap the template parser for an LLM constrained by
  `schema.describe_for_prompt()` + the guard as a validation gate.
- Point at a real warehouse (the guard's identifier/role checks are
  dialect-agnostic in structure).
- Add a vector-backed schema linker for wide schemas.

## Data

All data is synthetic and fictional (seeded RNG): 50 customers,
200 orders, 30 products, 40 support tickets. `sqlite3` stdlib only —
no external database needed.

## Tests

31 tests, deterministic, no network or API keys.
