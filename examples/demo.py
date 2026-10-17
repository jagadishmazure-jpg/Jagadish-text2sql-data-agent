"""Gold-set evaluation, a few questions end to end, and the SQL safety guard on hostile queries (offline)."""
import os
import tempfile

from text2sql.data import build_sample_db
from text2sql.eval import GOLD, evaluate
from text2sql.execute import run_question
from text2sql.guard import validate_sql
from text2sql.schema import retail_schema

db = os.path.join(tempfile.mkdtemp(), "retail.db")
build_sample_db(db)

rep = evaluate(db)
print(f"Gold set: {len(GOLD)} questions, parse accuracy {rep['parse_accuracy']:.2f}, "
      f"execution accuracy {rep['exec_accuracy']:.2f}, failures {len(rep['failures'])}\n")

for q in ["Total revenue in 2024", "Top 3 products by revenue", "Show me the top products by revenue"]:
    t = run_question(q, db)
    print(f"Q: {q}")
    if "sql" in t:
        print(f"SQL: {t['sql']}  params={t['params']}")
    print(f"A: {t['explanation']}\n")

schema = retail_schema()
hostile = [
    ("analyst", "DROP TABLE customers"),
    ("analyst", "SELECT name FROM customers; DELETE FROM orders"),
    ("analyst", "SELECT email FROM customers"),
    ("viewer", "SELECT name, region FROM customers"),
    ("analyst", "SELECT secret FROM payroll"),
    ("admin", "SELECT email FROM customers"),
]
print("Safety guard:")
for role, sql in hostile:
    v = validate_sql(sql, schema, role=role)
    print(f"  [{role:7}] {sql:48} -> {'allowed: ' + v['sql'] if v['ok'] else 'blocked: ' + '; '.join(v['errors'])}")
