"""JSON output mode: schema-validated machine-readable answers.

When enabled, the agent's final answer is a JSON document conforming to
``RESULT_SCHEMA`` (``query``, ``rows``, ``row_count``, ``citations``).
The payload is validated *before* it leaves the pipeline: a single repair
retry re-asks the producer with the validation errors, and if repair fails
the pipeline escalates explicitly via :class:`JSONEscalation` — malformed
JSON is never passed downstream silently.

In this deterministic template-based pipeline the default producer always
emits valid JSON, so the repair path exists for generative planners
swapped in via the ``producer`` hook (and is exercised by tests with a
flaky producer).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from .execute import run_question
from .schema import DatabaseSchema

RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["query", "rows", "row_count", "citations"],
    "properties": {
        "query": {"type": "string", "description": "the original NL question"},
        "rows": {"type": "array", "description": "result rows as lists"},
        "row_count": {
            "type": "integer",
            "description": "must equal len(rows)",
        },
        "citations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "sources: interpretation and SQL, or the explanation",
        },
    },
}

REQUIRED_KEYS = ("query", "rows", "row_count", "citations")


class JSONEscalation(Exception):
    """Raised when a JSON answer cannot be repaired within the retry budget.

    Carries the validation errors and a snippet of the offending raw
    output so the caller can log or surface exactly what went wrong.
    """


# A producer turns a pipeline trace into a raw JSON string. It receives the
# validation errors on the repair attempt so a generative planner can fix
# its own output.
Producer = Callable[[Dict[str, Any], Optional[List[str]]], str]


def build_result_payload(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Build the schema payload from a pipeline trace.

    Clarify and guard-blocked traces still produce valid JSON: rows are
    empty, row_count is 0, and the explanation lands in citations so no
    information is silently dropped.
    """
    rows = [list(r) for r in trace.get("rows", []) or []]
    citations: List[str] = []
    if trace.get("interpretation"):
        citations.append(f"interpretation: {trace['interpretation']}")
    if trace.get("sql"):
        citations.append(f"sql: {trace['sql']}")
    if trace.get("explanation"):
        citations.append(f"explanation: {trace['explanation']}")
    if not citations:
        citations.append("no result produced")
    return {
        "query": trace.get("question", ""),
        "rows": rows,
        "row_count": trace.get("row_count", len(rows)),
        "citations": citations,
    }


def render_result_json(trace: Dict[str, Any]) -> str:
    """Serialize a trace to the JSON answer document."""
    return json.dumps(build_result_payload(trace), ensure_ascii=False)


def validate_result(payload: Any) -> List[str]:
    """Validate a decoded payload against RESULT_SCHEMA.

    Returns a list of human-readable errors; empty means valid.
    """
    errors: List[str] = []
    if not isinstance(payload, dict):
        return [f"payload must be a JSON object, got {type(payload).__name__}"]
    for key in REQUIRED_KEYS:
        if key not in payload:
            errors.append(f"missing required key: {key!r}")
    if errors:
        return errors

    query = payload["query"]
    if not isinstance(query, str) or not query.strip():
        errors.append("'query' must be a non-empty string")

    rows = payload["rows"]
    if not isinstance(rows, list):
        errors.append("'rows' must be an array")
    elif any(not isinstance(r, list) for r in rows):
        errors.append("'rows' must be an array of arrays")

    row_count = payload["row_count"]
    # bool is a subclass of int; True/False are not valid row counts.
    if isinstance(row_count, bool) or not isinstance(row_count, int):
        errors.append("'row_count' must be an integer")
    elif isinstance(rows, list) and row_count != len(rows):
        errors.append(
            f"'row_count' ({row_count}) does not match len(rows) ({len(rows)})"
        )

    citations = payload["citations"]
    if not isinstance(citations, list) or not citations:
        errors.append("'citations' must be a non-empty array")
    elif any(not isinstance(c, str) or not c.strip() for c in citations):
        errors.append("'citations' must contain only non-empty strings")

    return errors


def _parse_and_validate(raw: str) -> tuple[Optional[Dict[str, Any]], List[str]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: {exc}"]
    return payload, validate_result(payload)


def _default_producer(trace: Dict[str, Any],
                      repair_hint: Optional[List[str]] = None) -> str:
    return render_result_json(trace)


def run_question_json(
    question: str,
    db_path: str,
    role: str = "analyst",
    schema: DatabaseSchema | None = None,
    timeout_s: float = 5.0,
    producer: Optional[Producer] = None,
    max_repairs: int = 1,
) -> str:
    """Full pipeline with a schema-validated JSON answer.

    ``producer`` renders the trace to a raw JSON string; it is called once,
    and on validation failure exactly once more with the errors as a
    repair hint. If the output is still invalid, :class:`JSONEscalation`
    is raised — malformed JSON is never returned.
    """
    trace = run_question(question, db_path, role=role, schema=schema,
                         timeout_s=timeout_s)
    produce = producer or _default_producer

    raw = produce(trace, None)
    _, errors = _parse_and_validate(raw)
    repairs = 0
    while errors and repairs < max_repairs:
        repairs += 1
        raw = produce(trace, errors)
        _, errors = _parse_and_validate(raw)

    if errors:
        raise JSONEscalation(
            "JSON output failed validation after "
            f"{repairs} repair attempt(s): {'; '.join(errors)}. "
            f"Raw output (truncated): {raw[:200]!r}"
        )
    return raw
