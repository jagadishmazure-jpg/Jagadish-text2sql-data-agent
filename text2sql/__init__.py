"""text2sql: natural language to safe SQL over structured data."""

from .data import build_sample_db
from .eval import evaluate
from .execute import run_question
from .guard import validate_sql
from .parse import generate_sql
from .schema import retail_schema

__all__ = ["build_sample_db", "evaluate", "run_question", "validate_sql",
           "generate_sql", "retail_schema"]
