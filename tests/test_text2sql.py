"""Tests for the text2sql pipeline. Deterministic, sqlite3 stdlib only."""
import os
import sqlite3
import tempfile

import pytest

from text2sql import (build_sample_db, evaluate, generate_sql, retail_schema,
                      run_question, validate_sql)
from text2sql.parse import (extract_amount, extract_region, extract_topn)


@pytest.fixture(scope="module")
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "retail.db")
    build_sample_db(path)
    yield path


@pytest.fixture(scope="module")
def schema():
    return retail_schema()


# ---------- schema ----------

def test_schema_describes_all_tables(schema):
    text = schema.describe_for_prompt()
    for t in ("customers", "orders", "products", "support_tickets"):
        assert t in text


def test_schema_rejects_unknown_table(schema):
    assert not schema.validate_identifier("invoices")
    assert schema.validate_identifier("orders")


def test_schema_rejects_unknown_column(schema):
    assert not schema.validate_identifier("orders.bogus_col")
    assert schema.validate_identifier("orders.total")


# ---------- data ----------

def test_sample_db_counts(db_path):
    conn = sqlite3.connect(db_path)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("customers", "products", "orders", "support_tickets")}
    conn.close()
    assert counts == {"customers": 50, "products": 30,
                      "orders": 200, "support_tickets": 40}


def test_sample_db_deterministic():
    d = tempfile.mkdtemp()
    p1, p2 = os.path.join(d, "a.db"), os.path.join(d, "b.db")
    build_sample_db(p1)
    build_sample_db(p2)
    for p in (p1, p2):
        conn = sqlite3.connect(p)
        rev = conn.execute("SELECT SUM(total) FROM orders").fetchone()[0]
        conn.close()
    c1 = sqlite3.connect(p1).execute("SELECT SUM(total) FROM orders").fetchone()[0]
    c2 = sqlite3.connect(p2).execute("SELECT SUM(total) FROM orders").fetchone()[0]
    assert c1 == c2


# ---------- parsing ----------

def test_parse_total_revenue(schema):
    r = generate_sql("What is the total revenue?", schema)
    assert not r["clarify"]
    assert "SUM(total)" in r["sql"] and "FROM orders" in r["sql"]


def test_parse_revenue_by_month(schema):
    r = generate_sql("Show total revenue by month", schema)
    assert "GROUP BY month" in r["sql"]


def test_parse_top_products(schema):
    r = generate_sql("Top 5 products by revenue", schema)
    assert "ORDER BY revenue DESC" in r["sql"]
    assert r["params"] == [5]


def test_parse_top_products_missing_n_clarifies(schema):
    r = generate_sql("Show me the top products by revenue", schema)
    assert r["clarify"] is True
    assert "How many" in r["ambiguous"]


def test_parse_orders_over_amount(schema):
    r = generate_sql("List orders over $300", schema)
    assert "total > ?" in r["sql"] and r["params"] == [300.0]


def test_parse_orders_over_amount_region(schema):
    r = generate_sql("Orders over 150 dollars in the West region", schema)
    assert r["params"] == [150.0, "West"]


def test_parse_orders_missing_amount_clarifies(schema):
    r = generate_sql("Show orders over the limit", schema)
    assert r["clarify"] is True


def test_parse_customers_open_tickets(schema):
    r = generate_sql("Which customers have open tickets?", schema)
    assert "support_tickets" in r["sql"] and "open" in r["sql"]


def test_parse_unknown_intent_clarifies(schema):
    r = generate_sql("What is the weather like?", schema)
    assert r["clarify"] is True
    assert r["sql"] is None


def test_entity_extraction():
    assert extract_amount("over $250") == 250.0
    assert extract_amount("above 99.99 dollars") == 99.99
    assert extract_amount("no amount here") is None
    assert extract_region("in the east region") == "East"
    assert extract_region("nothing") is None
    assert extract_topn("top 10 customers") == 10
    assert extract_topn("best customers") is None


# ---------- guard ----------

def test_guard_rejects_insert(schema):
    v = validate_sql("INSERT INTO orders VALUES (1,2,3,1,9.9,'2024-01-01','North','shipped')", schema)
    assert not v["ok"]


def test_guard_rejects_drop(schema):
    assert not validate_sql("DROP TABLE customers", schema)["ok"]


def test_guard_rejects_stacked(schema):
    v = validate_sql("SELECT * FROM orders; DELETE FROM orders", schema)
    assert not v["ok"]


def test_guard_rejects_unknown_column(schema):
    v = validate_sql("SELECT bogus FROM orders", schema)
    assert not v["ok"]
    assert any("bogus" in e for e in v["errors"])


def test_guard_injects_limit(schema):
    v = validate_sql("SELECT order_id FROM orders", schema)
    assert v["ok"] and v["sql"].rstrip().endswith("LIMIT 1000")


def test_guard_pii_denied_for_analyst(schema):
    v = validate_sql("SELECT email FROM customers", schema, role="analyst")
    assert not v["ok"]
    v2 = validate_sql("SELECT email FROM customers", schema, role="admin")
    assert v2["ok"]


def test_guard_accepts_generated_sql(schema, db_path):
    r = generate_sql("Top 5 products by revenue", schema)
    v = validate_sql(r["sql"], schema)
    assert v["ok"], v["errors"]


# ---------- execution ----------

def test_run_question_end_to_end(db_path):
    t = run_question("What is the total revenue?", db_path)
    assert t["validation"]["ok"]
    assert t["row_count"] == 1
    assert "$" in t["explanation"]


def test_run_question_grouped_explanation(db_path):
    t = run_question("Revenue by region", db_path)
    assert t["row_count"] == 4
    for region in ("North", "South", "East", "West"):
        assert region in t["explanation"]


def test_run_question_clarify_path(db_path):
    t = run_question("Tell me about stuff", db_path)
    assert "clarify" in t
    assert "sql" not in t


def test_run_question_blocked_path(db_path):
    # analyst role cannot see PII; force via crafted flow is blocked at guard
    v = validate_sql("SELECT c.email FROM customers c", retail_schema(), role="analyst")
    assert not v["ok"]


# ---------- eval ----------

def test_eval_suite_passes(db_path):
    rep = evaluate(db_path)
    assert rep["parse_accuracy"] == 1.0, rep["failures"]
    assert rep["exec_accuracy"] == 1.0, rep["failures"]


def test_eval_report_shape(db_path):
    rep = evaluate(db_path)
    assert rep["total"] == 15
    assert rep["failures"] == []
    assert all("question" in r for r in rep["results"])
