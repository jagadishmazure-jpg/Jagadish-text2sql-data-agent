"""JSON output mode: schema-validated machine-readable answers.

The final answer is a JSON document that conforms to ``RESULT_SCHEMA``
(``query``, ``rows``, ``row_count``, ``citations``). ``RESULT_SCHEMA`` is the
single source of truth: ``validate_result`` walks it (a small JSON Schema
subset: type, required, properties, additionalProperties, items, minLength,
minItems, minimum), so the schema handed to a model and the check applied to
its output cannot drift apart. One semantic rule sits on top:
``row_count == len(rows)``.

Flow: produce -> parse (NaN/Infinity rejected) -> validate -> at most
``max_repairs`` repair attempts with the errors fed back -> otherwise
:class:`JSONEscalation`. Escalation messages carry error codes, the length and
a short SHA-256 of the raw output, never the output itself, so row data cannot
leak into logs. Every call is counted in :data:`METRICS` (first-pass valid,
repaired, escalated) and logged as one line without payload data.

Floats in ``rows`` are rounded to 2 decimal places (the sample data is money).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .execute import run_question
from .schema import DatabaseSchema

log = logging.getLogger("text2sql.jsonout")
log.addHandler(logging.NullHandler())  # library default: the application configures logging

RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["query", "rows", "row_count", "citations"],
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string", "minLength": 1,
                  "description": "the original NL question"},
        "rows": {"type": "array", "items": {"type": "array"},
                 "description": "result rows as lists; floats rounded to 2 dp"},
        "row_count": {"type": "integer", "minimum": 0,
                      "description": "must equal len(rows)"},
        "citations": {"type": "array", "minItems": 1,
                      "items": {"type": "string", "minLength": 1},
                      "description": "sources: interpretation and SQL, or the explanation"},
    },
}

REQUIRED_KEYS = tuple(RESULT_SCHEMA["required"])
MONEY_DP = 2


class JSONEscalation(Exception):
    """Raised when a JSON answer cannot be repaired within the retry budget.

    ``errors`` holds the validation errors; ``raw_len`` and ``raw_sha256``
    identify the offending output without including it.
    """

    def __init__(self, message: str, errors: List[str], raw_len: int, raw_sha256: str):
        super().__init__(message)
        self.errors = errors
        self.raw_len = raw_len
        self.raw_sha256 = raw_sha256


@dataclass
class JSONOutputMetrics:
    """Counters for JSON mode. ``repair_rate`` and ``escalation_rate`` are per call."""

    calls: int = 0
    first_pass_valid: int = 0
    repaired: int = 0
    escalated: int = 0
    repair_attempts: int = 0
    error_types: Dict[str, int] = field(default_factory=dict)

    @property
    def repair_rate(self) -> float:
        return self.repaired / self.calls if self.calls else 0.0

    @property
    def escalation_rate(self) -> float:
        return self.escalated / self.calls if self.calls else 0.0

    @property
    def first_pass_rate(self) -> float:
        return self.first_pass_valid / self.calls if self.calls else 0.0

    def reset(self) -> None:
        self.__init__()

    def summary(self) -> str:
        return (f"calls {self.calls}, first-pass valid {self.first_pass_rate:.2f}, "
                f"repair rate {self.repair_rate:.2f}, escalation rate {self.escalation_rate:.2f}")


METRICS = JSONOutputMetrics()


@dataclass(frozen=True)
class JSONAnswer:
    """A validated answer: the JSON text, the decoded payload, and how many repairs it took."""

    raw: str
    payload: Dict[str, Any]
    repairs: int


# A producer turns a pipeline trace into a raw JSON string. On the repair
# attempt it receives the validation errors so a generative planner can fix
# its own output.
Producer = Callable[[Dict[str, Any], Optional[List[str]]], str]


def _round_cell(v: Any) -> Any:
    if isinstance(v, float) and math.isfinite(v):
        return round(v, MONEY_DP)
    return v


def build_result_payload(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Build the schema payload from a pipeline trace.

    Clarify and guard-blocked traces still produce valid JSON: rows are
    empty, row_count is 0, and the explanation lands in citations so no
    information is silently dropped.
    """
    rows = [[_round_cell(c) for c in r] for r in trace.get("rows", []) or []]
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
    """Serialize a trace to the JSON answer document (strict JSON: no NaN/Infinity)."""
    return json.dumps(build_result_payload(trace), ensure_ascii=False, allow_nan=False)


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    # bool is a subclass of int in Python; JSON Schema treats them as different types.
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def _check(schema: Dict[str, Any], value: Any, path: str, errors: List[str]) -> None:
    t = schema.get("type")
    if t and not _TYPES[t](value):
        label = {"array": "an array", "object": "an object", "integer": "an integer"}.get(t, f"a {t}")
        errors.append(f"{path} must be {label}")
        return
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{path} must be a finite number")
    if t == "string" and len(value.strip()) < schema.get("minLength", 0):
        errors.append(f"{path} must be a non-empty string")
    if t == "integer" and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path} must be >= {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path} must have at least {schema['minItems']} item(s)")
        # Items without a schema (row cells) are still walked so NaN/Infinity is caught.
        item_schema = schema.get("items", {})
        for i, item in enumerate(value):
            _check(item_schema, item, f"{path}[{i}]", errors)


def validate_result(payload: Any) -> List[str]:
    """Validate a decoded payload against RESULT_SCHEMA. Empty list means valid."""
    s = RESULT_SCHEMA
    if not isinstance(payload, dict):
        return [f"payload must be a JSON object, got {type(payload).__name__}"]
    errors = [f"missing required key: {k!r}" for k in s["required"] if k not in payload]
    if errors:
        return errors
    if s.get("additionalProperties") is False:
        extra = sorted(k for k in payload if k not in s["properties"])
        if extra:
            # Key names only, truncated: they may be model-controlled text.
            errors.append("unexpected key(s): " + ", ".join(repr(k[:32]) for k in extra[:5]))
    for key, sub in s["properties"].items():
        _check(sub, payload[key], f"'{key}'", errors)
    rows, row_count = payload["rows"], payload["row_count"]
    if isinstance(rows, list) and _TYPES["integer"](row_count) and row_count != len(rows):
        errors.append(f"'row_count' ({row_count}) does not match len(rows) ({len(rows)})")
    return errors


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not valid JSON")


def _parse_and_validate(raw: str) -> tuple[Optional[Dict[str, Any]], List[str]]:
    try:
        payload = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        msg = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        return None, [f"invalid JSON: {msg}"]
    return payload, validate_result(payload)


def _default_producer(trace: Dict[str, Any], repair_hint: Optional[List[str]] = None) -> str:
    return render_result_json(trace)


def _error_type(err: str) -> str:
    return err.split(":")[0].split(" ")[0].strip("'") if err else "unknown"


def answer_json(
    question: str,
    db_path: str,
    role: str = "analyst",
    schema: DatabaseSchema | None = None,
    timeout_s: float = 5.0,
    producer: Optional[Producer] = None,
    max_repairs: int = 1,
    metrics: JSONOutputMetrics | None = None,
) -> JSONAnswer:
    """Full pipeline returning a validated :class:`JSONAnswer` (with the repair count).

    The SQL runs once; repairs only re-render. Raises :class:`JSONEscalation`
    if the output is still invalid after ``max_repairs`` attempts.
    """
    m = metrics if metrics is not None else METRICS
    trace = run_question(question, db_path, role=role, schema=schema, timeout_s=timeout_s)
    produce = producer or _default_producer
    m.calls += 1

    raw = produce(trace, None)
    payload, errors = _parse_and_validate(raw)
    repairs = 0
    while errors and repairs < max_repairs:
        for e in errors:
            m.error_types[_error_type(e)] = m.error_types.get(_error_type(e), 0) + 1
        repairs += 1
        m.repair_attempts += 1
        raw = produce(trace, errors)
        payload, errors = _parse_and_validate(raw)

    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]
    if errors:
        m.escalated += 1
        log.warning("json_output escalated repairs=%d errors=%d raw_len=%d raw_sha256=%s",
                    repairs, len(errors), len(raw), digest)
        raise JSONEscalation(
            f"JSON output failed validation after {repairs} repair attempt(s): "
            f"{'; '.join(errors)} (raw output withheld: {len(raw)} chars, sha256 {digest})",
            errors=errors, raw_len=len(raw), raw_sha256=digest,
        )
    if repairs:
        m.repaired += 1
    else:
        m.first_pass_valid += 1
    log.info("json_output ok repairs=%d raw_len=%d", repairs, len(raw))
    return JSONAnswer(raw=raw, payload=payload, repairs=repairs)


def run_question_json(question: str, db_path: str, **kwargs: Any) -> str:
    """Same as :func:`answer_json` but returns only the JSON text."""
    return answer_json(question, db_path, **kwargs).raw
