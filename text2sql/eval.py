"""Gold-set evaluation for the text-to-SQL pipeline.

Each gold item has a hand-written reference SQL (ground truth). The
generated SQL must (a) contain the expected structural fragments and
(b) return exactly the same rows as the reference when executed.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from .data import build_sample_db
from .execute import run_question
from .schema import retail_schema

GOLD: List[Dict[str, Any]] = [
    {"q": "What is the total revenue?",
     "must_contain": ["SUM(total)", "FROM orders"],
     "ref_sql": "SELECT SUM(total) AS revenue FROM orders", "ref_params": []},
    {"q": "Show total revenue by month",
     "must_contain": ["SUM(total)", "GROUP BY month"],
     "ref_sql": ("SELECT strftime('%Y-%m', order_date) AS month, "
                 "SUM(total) AS revenue FROM orders GROUP BY month ORDER BY month"),
     "ref_params": []},
    {"q": "Top 5 products by revenue",
     "must_contain": ["GROUP BY", "ORDER BY revenue DESC", "LIMIT"],
     "ref_sql": ("SELECT p.name AS product, SUM(o.total) AS revenue FROM orders o "
                 "JOIN products p ON o.product_id = p.product_id "
                 "GROUP BY p.name ORDER BY revenue DESC LIMIT 5"),
     "ref_params": []},
    {"q": "Top 3 products by revenue",
     "must_contain": ["LIMIT"],
     "ref_sql": ("SELECT p.name AS product, SUM(o.total) AS revenue FROM orders o "
                 "JOIN products p ON o.product_id = p.product_id "
                 "GROUP BY p.name ORDER BY revenue DESC LIMIT 3"),
     "ref_params": []},
    {"q": "List orders over $300",
     "must_contain": ["total >", "FROM orders"],
     "ref_sql": ("SELECT order_id, customer_id, total, order_date, region FROM orders "
                 "WHERE total > 300 ORDER BY total DESC"),
     "ref_params": []},
    {"q": "Orders over 150 dollars in the West region",
     "must_contain": ["total >", "region"],
     "ref_sql": ("SELECT order_id, customer_id, total, order_date, region FROM orders "
                 "WHERE total > 150 AND region = 'West' ORDER BY total DESC"),
     "ref_params": []},
    {"q": "Which customers have open tickets?",
     "must_contain": ["JOIN", "support_tickets", "open"],
     "ref_sql": ("SELECT DISTINCT c.customer_id, c.name, c.region FROM customers c "
                 "JOIN support_tickets t ON c.customer_id = t.customer_id "
                 "WHERE t.status = 'open' ORDER BY c.customer_id"),
     "ref_params": []},
    {"q": "How many open tickets are there?",
     "must_contain": ["COUNT(*)", "support_tickets"],
     "ref_sql": "SELECT COUNT(*) AS n FROM support_tickets WHERE status = 'open'",
     "ref_params": []},
    {"q": "Ticket counts by status",
     "must_contain": ["GROUP BY status"],
     "ref_sql": ("SELECT status, COUNT(*) AS n FROM support_tickets "
                 "GROUP BY status ORDER BY n DESC"),
     "ref_params": []},
    {"q": "What is the average order value?",
     "must_contain": ["AVG(total)"],
     "ref_sql": "SELECT AVG(total) AS avg_value FROM orders",
     "ref_params": []},
    {"q": "Revenue by region",
     "must_contain": ["GROUP BY region", "SUM(total)"],
     "ref_sql": ("SELECT region, SUM(total) AS revenue FROM orders "
                 "GROUP BY region ORDER BY revenue DESC"),
     "ref_params": []},
    {"q": "How many orders are there?",
     "must_contain": ["COUNT(*)", "FROM orders"],
     "ref_sql": "SELECT COUNT(*) AS n FROM orders",
     "ref_params": []},
    {"q": "How many customers are there?",
     "must_contain": ["COUNT(*)", "FROM customers"],
     "ref_sql": "SELECT COUNT(*) AS n FROM customers",
     "ref_params": []},
    {"q": "Total revenue in 2024",
     "must_contain": ["SUM(total)", "2024"],
     "ref_sql": ("SELECT SUM(total) AS revenue FROM orders "
                 "WHERE strftime('%Y', order_date) = '2024'"),
     "ref_params": []},
    {"q": "Customers in the North region",
     "must_contain": ["FROM customers", "North"],
     "ref_sql": ("SELECT customer_id, name, region FROM customers "
                 "WHERE region = 'North' ORDER BY customer_id"),
     "ref_params": []},
]


def _rows_equal(a: List[tuple], b: List[tuple], ordered: bool) -> bool:
    if ordered:
        return list(a) == list(b)
    return sorted(map(str, a)) == sorted(map(str, b))


def evaluate(db_path: str) -> Dict[str, Any]:
    """Run the gold set; report parse accuracy + execution correctness."""
    schema = retail_schema()
    results = []
    for item in GOLD:
        trace = run_question(item["q"], db_path, schema=schema)
        haystack = ((trace.get("sql") or "")
                    + " " + str(trace.get("params") or "")).lower()
        parse_ok = (not trace.get("clarify")
                    and trace.get("validation", {}).get("ok")
                    and all(frag.lower() in haystack for frag in item["must_contain"]))
        exec_ok = False
        if parse_ok:
            conn = sqlite3.connect(db_path)
            try:
                ref_rows = conn.execute(item["ref_sql"],
                                        item["ref_params"]).fetchall()
            finally:
                conn.close()
            ordered = "order by" in (trace.get("sql") or "").lower()
            exec_ok = _rows_equal(trace["rows"], ref_rows, ordered)
        results.append({"question": item["q"], "parse_ok": parse_ok,
                        "exec_ok": exec_ok,
                        "sql": trace.get("sql")})
    n = len(results)
    return {
        "total": n,
        "parse_accuracy": sum(r["parse_ok"] for r in results) / n,
        "exec_accuracy": sum(r["exec_ok"] for r in results) / n,
        "failures": [r for r in results if not (r["parse_ok"] and r["exec_ok"])],
        "results": results,
    }


if __name__ == "__main__":
    import tempfile, os
    path = os.path.join(tempfile.mkdtemp(), "retail.db")
    build_sample_db(path)
    rep = evaluate(path)
    print(f"parse accuracy: {rep['parse_accuracy']:.2f}")
    print(f"exec accuracy:  {rep['exec_accuracy']:.2f}")
    for f in rep["failures"]:
        print("FAIL:", f["question"], "->", f["sql"])
