"""Schema registry for the synthetic retail database.

The schema is the grounding contract: the NL parser may only reference
tables/columns declared here, and the guard re-validates every generated
query against it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str
    description: str = ""


@dataclass
class Table:
    name: str
    columns: List[Column]
    primary_key: str = ""
    foreign_keys: Dict[str, str] = field(default_factory=dict)  # column -> "table.column"


class DatabaseSchema:
    def __init__(self, tables: List[Table]):
        self.tables: Dict[str, Table] = {t.name: t for t in tables}

    def describe_for_prompt(self) -> str:
        """Render the schema as plain text for LLM/prompt grounding."""
        lines = []
        for t in self.tables.values():
            cols = ", ".join(f"{c.name} ({c.dtype})" for c in t.columns)
            lines.append(f"Table {t.name}: {cols}")
            if t.primary_key:
                lines.append(f"  primary key: {t.primary_key}")
            if t.foreign_keys:
                fks = ", ".join(f"{c} -> {r}" for c, r in t.foreign_keys.items())
                lines.append(f"  foreign keys: {fks}")
        return "\n".join(lines)

    def validate_identifier(self, name: str) -> bool:
        """Accept ``table`` or ``table.column``; reject anything undeclared."""
        if "." in name:
            table, col = name.split(".", 1)
            t = self.tables.get(table)
            return t is not None and any(c.name == col for c in t.columns)
        return name in self.tables

    def columns_of(self, table: str) -> List[str]:
        return [c.name for c in self.tables[table].columns]

    def table_names(self) -> List[str]:
        return list(self.tables.keys())


PII_COLUMNS = {"customers.email", "customers.phone"}


def retail_schema() -> DatabaseSchema:
    return DatabaseSchema([
        Table(
            name="customers",
            columns=[
                Column("customer_id", "INTEGER", "primary key"),
                Column("name", "TEXT", "full name (fictional)"),
                Column("email", "TEXT", "contact email (fictional, PII)"),
                Column("phone", "TEXT", "contact phone (fictional, PII)"),
                Column("region", "TEXT", "one of North/South/East/West"),
                Column("signup_date", "TEXT", "ISO date YYYY-MM-DD"),
            ],
            primary_key="customer_id",
        ),
        Table(
            name="products",
            columns=[
                Column("product_id", "INTEGER", "primary key"),
                Column("name", "TEXT", "product name (fictional)"),
                Column("category", "TEXT", "product category"),
                Column("price", "REAL", "unit price in USD"),
            ],
            primary_key="product_id",
        ),
        Table(
            name="orders",
            columns=[
                Column("order_id", "INTEGER", "primary key"),
                Column("customer_id", "INTEGER", "references customers"),
                Column("product_id", "INTEGER", "references products"),
                Column("quantity", "INTEGER", "units ordered"),
                Column("total", "REAL", "line total in USD"),
                Column("order_date", "TEXT", "ISO date YYYY-MM-DD"),
                Column("region", "TEXT", "fulfilment region"),
                Column("status", "TEXT", "shipped/delivered/cancelled"),
            ],
            primary_key="order_id",
            foreign_keys={"customer_id": "customers.customer_id",
                          "product_id": "products.product_id"},
        ),
        Table(
            name="support_tickets",
            columns=[
                Column("ticket_id", "INTEGER", "primary key"),
                Column("customer_id", "INTEGER", "references customers"),
                Column("subject", "TEXT", "ticket subject (fictional)"),
                Column("status", "TEXT", "open/pending/closed"),
                Column("priority", "TEXT", "low/medium/high"),
                Column("created_date", "TEXT", "ISO date YYYY-MM-DD"),
            ],
            primary_key="ticket_id",
            foreign_keys={"customer_id": "customers.customer_id"},
        ),
    ])
