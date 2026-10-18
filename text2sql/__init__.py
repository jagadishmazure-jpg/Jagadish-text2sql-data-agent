"""text2sql: natural language to safe SQL over structured data."""

from .data import build_sample_db
from .eval import evaluate
from .execute import run_question
from .guard import validate_sql
from .jsonout import (
    METRICS,
    RESULT_SCHEMA,
    JSONAnswer,
    JSONEscalation,
    answer_json,
    build_result_payload,
    render_result_json,
    run_question_json,
    validate_result,
)
from .parse import generate_sql
from .schema import retail_schema

__all__ = ["build_sample_db", "evaluate", "run_question", "run_question_json",
           "validate_sql", "generate_sql", "retail_schema",
           "JSONEscalation", "RESULT_SCHEMA", "build_result_payload",
           "render_result_json", "validate_result",
           "answer_json", "JSONAnswer", "METRICS"]
