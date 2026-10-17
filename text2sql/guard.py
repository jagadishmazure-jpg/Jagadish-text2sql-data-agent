"""Safety guard for generated SQL.

Defense in depth, applied even though the parser only emits parameterized
queries from templates:

- read-only: only a single SELECT statement may execute
- identifier check: every table/column must exist in the schema
- role-based column allowlist (PII protection)
- LIMIT cap injected when the query has none
- stacked statements (``;``) and DDL/DML keywords rejected outright
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .schema import PII_COLUMNS, DatabaseSchema

FORBIDDEN = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "merge", "grant", "revoke", "attach", "detach", "vacuum",
    "pragma", "reindex",
}

# SQL keywords / functions the identifier checker must not flag.
SQL_WORDS = {
    "select", "from", "where", "and", "or", "not", "group", "by", "order",
    "limit", "offset", "join", "inner", "left", "right", "outer", "full",
    "cross", "on", "as", "asc", "desc", "distinct", "like", "in", "between",
    "is", "null", "having", "union", "all", "case", "when", "then", "else",
    "end", "strftime", "date", "lower", "upper", "substr", "length",
    "sum", "avg", "count", "min", "max", "round", "coalesce", "cast",
}

ROLE_DENY: Dict[str, set] = {
    "admin": set(),
    "analyst": set(PII_COLUMNS),   # no customer email/phone
    "viewer": set(PII_COLUMNS) | {"customers.name"},
}


def _strip_strings(sql: str) -> str:
    return re.sub(r"'[^']*'", "''", sql)


def validate_sql(sql: str, schema: DatabaseSchema, role: str = "analyst",
                 max_rows: int = 1000) -> Dict[str, Any]:
    """Validate ``sql``; returns {ok, errors, sql} with LIMIT injected."""
    errors: List[str] = []
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        errors.append("empty query")
        return {"ok": False, "errors": errors, "sql": None}

    # single statement only
    if ";" in _strip_strings(cleaned):
        errors.append("multiple statements are not allowed")

    first = re.match(r"\s*(\w+)", cleaned)
    if not first or first.group(1).lower() != "select":
        errors.append("only SELECT queries are allowed")

    lowered = _strip_strings(cleaned).lower()
    for kw in FORBIDDEN:
        if re.search(rf"\b{kw}\b", lowered):
            errors.append(f"forbidden keyword: {kw.upper()}")

    # identifier check: every non-keyword word must be a known table/column.
    # Column aliases (AS x) and table aliases (FROM orders o) are collected
    # first so generated SQL validates.
    aliases = set(re.findall(r"\bas\s+([A-Za-z_]\w*)", lowered))
    table_alias: Dict[str, str] = {}
    for m in re.finditer(r"\b(?:from|join)\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)",
                         lowered):
        real, alias = m.group(1), m.group(2)
        if alias not in SQL_WORDS and real in schema.table_names():
            table_alias[alias] = real

    for match in re.finditer(r"\b([A-Za-z_][\w]*)(?:\.([A-Za-z_][\w]*))?\b", lowered):
        table, col = match.group(1), match.group(2)
        if table in SQL_WORDS or table in aliases:
            continue
        if re.fullmatch(r"\d+", table):
            continue
        if table in table_alias:
            table = table_alias[table]
        ident = f"{table}.{col}" if col else table
        if not schema.validate_identifier(ident):
            # bare column names: must exist in at least one table
            if col is None and any(
                    ident in schema.columns_of(t) for t in schema.table_names()):
                continue
            errors.append(f"unknown identifier: {ident}")
            break

    # role-based column allowlist (check qualified + bare PII columns).
    # Table aliases are resolved: `c.email` counts as `customers.email`.
    deny = ROLE_DENY.get(role, ROLE_DENY["analyst"])
    rev_alias: Dict[str, List[str]] = {}
    for m in re.finditer(r"\b(?:from|join)\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)",
                         lowered):
        real, alias = m.group(1), m.group(2)
        if alias not in SQL_WORDS and real in schema.table_names():
            rev_alias.setdefault(real, []).append(alias)
    if deny:
        for bad in deny:
            btable, bcol = bad.split(".")
            names = "|".join([btable] + rev_alias.get(btable, []))
            if re.search(rf"\b(?:{names})\s*\.\s*{bcol}\b", lowered):
                errors.append(f"column {bad} not allowed for role '{role}'")
                break
            if re.search(rf"\b{btable}\b", lowered) and \
               re.search(rf"(?<![\w.]){bcol}\b", lowered):
                errors.append(f"column {bad} not allowed for role '{role}'")
                break

    if errors:
        return {"ok": False, "errors": errors, "sql": None}

    safe = cleaned
    if not re.search(r"\blimit\b", lowered):
        safe = f"{safe} LIMIT {max_rows}"
    return {"ok": True, "errors": [], "sql": safe}
