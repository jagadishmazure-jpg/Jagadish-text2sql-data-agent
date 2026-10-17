"""Deterministic natural-language to SQL.

Template-based parsing: each supported intent is a regex matcher plus a
builder that emits *parameterized* SQL (user values never interpolated).
Anything the templates cannot handle returns a ``clarify`` response that
states what was understood and what is ambiguous — the parser never
guesses.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .schema import DatabaseSchema

REGIONS = ["north", "south", "east", "west"]
MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}
TICKET_STATUSES = ["open", "pending", "closed"]


def extract_amount(text: str) -> Optional[float]:
    m = re.search(r"\$\s?(\d+(?:\.\d{1,2})?)", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d{1,2})?)\s*dollars?", text)
    if m:
        return float(m.group(1))
    return None


def extract_region(text: str) -> Optional[str]:
    for r in REGIONS:
        if re.search(rf"\b{r}\b", text):
            return r.capitalize()
    return None


def extract_topn(text: str) -> Optional[int]:
    m = re.search(r"\btop\s+(\d+)\b", text)
    return int(m.group(1)) if m else None


def extract_month(text: str) -> Optional[str]:
    for name, num in MONTHS.items():
        if re.search(rf"\bin\s+{name}\b", text):
            return num
    return None


def extract_year(text: str) -> Optional[str]:
    m = re.search(r"\b(20\d{2})\b", text)
    return m.group(1) if m else None


def _date_filter(text: str, column: str) -> Tuple[str, List[Any], str]:
    """Optional date restriction shared by order/revenue intents."""
    month, year = extract_month(text), extract_year(text)
    if month and year:
        return (f"strftime('%Y-%m', {column}) = ?",
                [f"{year}-{month}"],
                f"in {year}-{month}")
    if year:
        return (f"strftime('%Y', {column}) = ?", [year], f"in {year}")
    if month:
        return (f"strftime('%m', {column}) = ?", [month],
                f"in month {month}")
    return "", [], ""


def _clarify(understood: str, ambiguous: str) -> Dict[str, Any]:
    return {"clarify": True, "understood": understood,
            "ambiguous": ambiguous, "sql": None}


def generate_sql(question: str, schema: DatabaseSchema) -> Dict[str, Any]:
    """Parse ``question`` into parameterized SQL or a clarify response."""
    q = question.strip().lower()
    if not q:
        return _clarify("empty question", "Please ask a question about the data.")

    # 1. revenue by month
    if re.search(r"revenue\s+by\s+month", q):
        cond, params, period = _date_filter(q, "order_date")
        where = f" WHERE {cond}" if cond else ""
        interp = ("total revenue grouped by month"
                  + (f" {period}" if period else " (all time)"))
        return {"clarify": False, "intent": "revenue_by_month",
                "sql": ("SELECT strftime('%Y-%m', order_date) AS month, "
                        "SUM(total) AS revenue FROM orders"
                        f"{where} GROUP BY month ORDER BY month"),
                "params": params, "interpretation": interp}

    # 2. revenue by region (before plain revenue — more specific first)
    if re.search(r"\brevenue\b.*\bby\s+region\b", q):
        return {"clarify": False, "intent": "revenue_by_region",
                "sql": ("SELECT region, SUM(total) AS revenue FROM orders "
                        "GROUP BY region ORDER BY revenue DESC"),
                "params": [],
                "interpretation": "total revenue grouped by region"}

    # 3. top-N products by revenue
    if re.search(r"\btop\b.*\bproducts?\b.*\brevenue\b", q) or \
       re.search(r"\bbest[- ]selling\b", q):
        n = extract_topn(q)
        if n is None:
            return _clarify("you want top products by revenue",
                            "How many products? e.g. 'top 5 products by revenue'.")
        return {"clarify": False, "intent": "top_products",
                "sql": ("SELECT p.name AS product, SUM(o.total) AS revenue "
                        "FROM orders o JOIN products p ON o.product_id = p.product_id "
                        "GROUP BY p.name ORDER BY revenue DESC LIMIT ?"),
                "params": [n],
                "interpretation": f"top {n} products by total revenue"}


    # 4. total revenue (optionally filtered)
    if re.search(r"\b(total\s+)?revenue\b", q):
        cond, params, period = _date_filter(q, "order_date")
        region = extract_region(q)
        if region:
            cond = (cond + " AND " if cond else "") + "region = ?"
            params = params + [region]
        where = f" WHERE {cond}" if cond else ""
        scope = " ".join(x for x in [period, f"in the {region} region" if region else ""] if x)
        return {"clarify": False, "intent": "total_revenue",
                "sql": f"SELECT SUM(total) AS revenue FROM orders{where}",
                "params": params,
                "interpretation": f"total revenue{(' ' + scope) if scope else ' (all time)'}".strip()}

    # 5. orders over $X (optional region)
    if re.search(r"\borders?\b.*\b(over|above|greater than)\b", q):
        amount = extract_amount(q)
        if amount is None:
            return _clarify("you want orders above an amount",
                            "Above what amount? e.g. 'orders over $200'.")
        region = extract_region(q)
        cond, params = "total > ?", [amount]
        if region:
            cond += " AND region = ?"
            params.append(region)
        return {"clarify": False, "intent": "orders_over_amount",
                "sql": ("SELECT order_id, customer_id, total, order_date, region "
                        f"FROM orders WHERE {cond} ORDER BY total DESC"),
                "params": params,
                "interpretation": (f"orders over ${amount:,.2f}"
                                   + (f" in the {region} region" if region else ""))}

    # 6. customers with open tickets
    if re.search(r"customers?\s+(with|have|having)\s+(open\s+)?tickets?", q):
        return {"clarify": False, "intent": "customers_open_tickets",
                "sql": ("SELECT DISTINCT c.customer_id, c.name, c.region "
                        "FROM customers c JOIN support_tickets t "
                        "ON c.customer_id = t.customer_id "
                        "WHERE t.status = 'open' ORDER BY c.customer_id"),
                "params": [],
                "interpretation": "customers who have at least one open support ticket"}

    # 7. ticket counts by status
    if re.search(r"\btickets?\b.*\bby\s+status\b", q) or \
       re.search(r"\bhow many\b.*\btickets?\b", q):
        status = next((s for s in TICKET_STATUSES
                       if re.search(rf"\b{s}\b", q)), None)
        if status:
            return {"clarify": False, "intent": "ticket_count_status",
                    "sql": "SELECT COUNT(*) AS n FROM support_tickets WHERE status = ?",
                    "params": [status],
                    "interpretation": f"number of {status} support tickets"}
        return {"clarify": False, "intent": "tickets_by_status",
                "sql": ("SELECT status, COUNT(*) AS n FROM support_tickets "
                        "GROUP BY status ORDER BY n DESC"),
                "params": [],
                "interpretation": "support ticket counts grouped by status"}

    # 8. average order value
    if re.search(r"\baverage\b.*\border\b.*\bvalue\b", q) or \
       re.search(r"\bavg\b.*\border\b", q):
        return {"clarify": False, "intent": "avg_order_value",
                "sql": "SELECT AVG(total) AS avg_value FROM orders",
                "params": [],
                "interpretation": "average order value across all orders"}

    # 9. order/customer counts
    if re.search(r"\bhow many\b.*\borders?\b", q):
        return {"clarify": False, "intent": "count_orders",
                "sql": "SELECT COUNT(*) AS n FROM orders",
                "params": [],
                "interpretation": "total number of orders"}
    if re.search(r"\bhow many\b.*\bcustomers?\b", q):
        return {"clarify": False, "intent": "count_customers",
                "sql": "SELECT COUNT(*) AS n FROM customers",
                "params": [],
                "interpretation": "total number of customers"}

    # 10. list customers in a region
    if re.search(r"\bcustomers?\b.*\bin\s+the\s+(north|south|east|west)\b", q):
        region = extract_region(q)
        return {"clarify": False, "intent": "customers_by_region",
                "sql": ("SELECT customer_id, name, region FROM customers "
                        "WHERE region = ? ORDER BY customer_id"),
                "params": [region],
                "interpretation": f"customers in the {region} region"}

    return _clarify(
        "your question is about the retail database",
        ("I can answer questions about revenue, order counts, top products, "
         "orders over an amount, customers by region, and support tickets. "
         "Could you rephrase using one of those?"))
