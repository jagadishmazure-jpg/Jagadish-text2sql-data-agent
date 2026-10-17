"""Execute NL questions end-to-end: parse -> guard -> run -> explain.

Every question returns a full trace, so the pipeline is auditable:
interpretation, generated SQL, guard verdict, rows, and a
natural-language explanation that cites the actual numbers.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List

from .guard import validate_sql
from .parse import generate_sql
from .schema import DatabaseSchema, retail_schema


class QueryTimeout(Exception):
    pass


def _run_with_timeout(conn: sqlite3.Connection, sql: str,
                      params: List[Any], timeout_s: float = 5.0):
    deadline = time.time() + timeout_s

    def handler() -> int:
        return 1 if time.time() > deadline else 0

    conn.set_progress_handler(handler, 1000)
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, rows
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            raise QueryTimeout(f"query exceeded {timeout_s}s")
        raise
    finally:
        conn.set_progress_handler(None, 0)


def _fmt(n: Any) -> str:
    if isinstance(n, float):
        return f"${n:,.2f}" if abs(n) >= 0.01 else str(round(n, 4))
    return str(n)


def explain_result(question: str, interpretation: str,
                   columns: List[str], rows: List[tuple]) -> str:
    """Natural-language summary of the result, citing the numbers."""
    if not rows:
        return f"{interpretation.capitalize()}: no matching rows found."
    if len(rows) == 1 and len(columns) == 1:
        return (f"{interpretation.capitalize()}: "
                f"{_fmt(rows[0][0])}.")
    if len(columns) == 2 and len(rows) <= 12:
        parts = ", ".join(f"{r[0]}: {_fmt(r[1])}" for r in rows)
        return (f"{interpretation.capitalize()} "
                f"({len(rows)} groups): {parts}.")
    head = ", ".join(str(r[0]) for r in rows[:5])
    more = f" (+{len(rows) - 5} more)" if len(rows) > 5 else ""
    return (f"{interpretation.capitalize()}: {len(rows)} rows. "
            f"First values: {head}{more}.")


def run_question(question: str, db_path: str, role: str = "analyst",
                 schema: DatabaseSchema | None = None,
                 timeout_s: float = 5.0) -> Dict[str, Any]:
    """Full pipeline for one natural-language question."""
    schema = schema or retail_schema()
    trace: Dict[str, Any] = {"question": question, "role": role}

    parsed = generate_sql(question, schema)
    trace["interpretation"] = parsed.get("interpretation")
    if parsed.get("clarify"):
        trace["clarify"] = {"understood": parsed["understood"],
                            "ambiguous": parsed["ambiguous"]}
        trace["explanation"] = (
            f"I understood: {parsed['understood']}. "
            f"To answer precisely I need: {parsed['ambiguous']}")
        return trace

    trace["sql"], trace["params"] = parsed["sql"], parsed["params"]
    verdict = validate_sql(parsed["sql"], schema, role=role)
    trace["validation"] = {"ok": verdict["ok"], "errors": verdict["errors"]}
    if not verdict["ok"]:
        trace["explanation"] = ("Query blocked by safety guard: "
                                + "; ".join(verdict["errors"]))
        return trace

    conn = sqlite3.connect(db_path)
    try:
        columns, rows = _run_with_timeout(conn, verdict["sql"],
                                          parsed["params"], timeout_s)
    finally:
        conn.close()
    trace["columns"], trace["rows"], trace["row_count"] = columns, rows, len(rows)
    trace["explanation"] = explain_result(question, parsed["interpretation"],
                                          columns, rows)
    return trace
