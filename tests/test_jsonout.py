"""Tests for JSON output mode: schema, strict parsing, repair loop, metrics, redaction."""
import json as _json
import logging
import os
import tempfile

import pytest

from text2sql import (
    RESULT_SCHEMA,
    JSONEscalation,
    answer_json,
    build_sample_db,
    run_question,
    run_question_json,
    validate_result,
)
from text2sql.jsonout import JSONOutputMetrics, build_result_payload, render_result_json


@pytest.fixture(scope="module")
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "retail.db")
    build_sample_db(path)
    yield path


# ---------- JSON output mode ----------



def test_json_output_schema_declared():
    assert set(RESULT_SCHEMA["required"]) == {
        "query", "rows", "row_count", "citations"}


def test_json_output_valid_end_to_end(db_path):
    raw = run_question_json("What is the total revenue?", db_path)
    payload = _json.loads(raw)  # must parse
    assert validate_result(payload) == []
    assert payload["query"] == "What is the total revenue?"
    assert payload["row_count"] == len(payload["rows"]) == 1
    assert payload["citations"]  # interpretation + SQL cited


def test_json_output_clarify_path_still_valid(db_path):
    raw = run_question_json("Tell me about stuff", db_path)
    payload = _json.loads(raw)
    assert validate_result(payload) == []
    assert payload["rows"] == [] and payload["row_count"] == 0
    assert any("clarify" in c or "explanation" in c for c in payload["citations"])


def test_json_output_row_count_mismatch_rejected():
    assert any("row_count" in e for e in validate_result({
        "query": "q", "rows": [[1], [2]], "row_count": 5,
        "citations": ["sql: SELECT 1"]}))
    assert any("row_count" in e for e in validate_result({
        "query": "q", "rows": [], "row_count": True,
        "citations": ["x"]}))  # bool is not a valid row count


def test_json_output_missing_keys_rejected():
    errors = validate_result({"query": "q"})
    assert len(errors) == 3  # rows, row_count, citations


def test_json_malformed_repaired_on_retry(db_path):
    calls = []

    def flaky(trace, repair_hint=None):
        calls.append(repair_hint)
        if repair_hint is None:
            return "{not valid json"
        return render_result_json(trace)

    raw = run_question_json("What is the total revenue?", db_path,
                            producer=flaky)
    assert _json.loads(raw)["row_count"] == 1
    assert len(calls) == 2
    assert calls[0] is None  # first attempt: no hint
    assert calls[1] and any("invalid JSON" in e for e in calls[1])


def test_json_schema_violation_repaired_on_retry(db_path):
    def bad_then_good(trace, repair_hint=None):
        if repair_hint is None:
            return _json.dumps({"query": "q"})  # valid JSON, bad schema
        return render_result_json(trace)

    raw = run_question_json("Revenue by region", db_path,
                            producer=bad_then_good)
    payload = _json.loads(raw)
    assert validate_result(payload) == []
    assert payload["row_count"] == 4


def test_json_unrepairable_escalates_loudly(db_path):
    def always_bad(trace, repair_hint=None):
        return "nope, not json"

    with pytest.raises(JSONEscalation) as excinfo:
        run_question_json("What is the total revenue?", db_path,
                          producer=always_bad)
    assert "invalid JSON" in str(excinfo.value)  # explicit, never silent


def test_json_build_payload_matches_trace(db_path):
    trace = run_question("Revenue by region", db_path)
    payload = build_result_payload(trace)
    assert validate_result(payload) == []
    assert payload["row_count"] == trace["row_count"] == 4


def _valid(**over):
    base = {"query": "q", "rows": [[1]], "row_count": 1, "citations": ["sql: SELECT 1"]}
    base.update(over)
    return base


def test_schema_is_the_single_source_for_validation():
    # Every declared property is enforced; nothing outside it is accepted.
    assert RESULT_SCHEMA["additionalProperties"] is False
    assert set(RESULT_SCHEMA["properties"]) == set(RESULT_SCHEMA["required"])
    assert validate_result(_valid()) == []


def test_extra_keys_rejected():
    errs = validate_result(_valid(debug="x"))
    assert errs and "unexpected key" in errs[0] and "debug" in errs[0]


def test_extra_key_names_truncated_in_errors():
    errs = validate_result(_valid(**{"k" * 500: 1}))
    assert errs and len(errs[0]) < 80


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_constants_rejected(db_path, bad):
    raw = '{"query": "q", "rows": [[%s]], "row_count": 1, "citations": ["x"]}' % bad
    with pytest.raises(JSONEscalation) as ei:
        run_question_json("What is the total revenue?", db_path, producer=lambda t, h=None: raw)
    assert "non-finite" in str(ei.value)


def test_non_finite_python_float_rejected_by_validator():
    assert any("finite" in e for e in validate_result(_valid(rows=[[float("nan")]])))
    assert any("finite" in e for e in validate_result(_valid(rows=[[float("inf")]])))


def test_wrong_types_rejected():
    assert any("'query'" in e for e in validate_result(_valid(query="  ")))
    assert any("'rows'" in e for e in validate_result(_valid(rows="x", row_count=0)))
    assert any("'citations'" in e for e in validate_result(_valid(citations=[])))
    assert any("'citations'" in e for e in validate_result(_valid(citations=[3])))
    assert any(">= 0" in e for e in validate_result(_valid(rows=[], row_count=-1)))
    assert validate_result([1, 2])[0].startswith("payload must be a JSON object")


def test_money_rounded_to_cents(db_path):
    payload = _json.loads(run_question_json("What is the total revenue?", db_path))
    value = payload["rows"][0][0]
    assert value == round(value, 2)
    assert build_result_payload({"question": "q", "rows": [[41514.979999999996, "x", 3]],
                                 "row_count": 1})["rows"] == [[41514.98, "x", 3]]


def test_render_never_emits_nan():
    with pytest.raises(ValueError):
        render_result_json({"question": "q", "rows": [[float("nan")]], "row_count": 1})


def test_answer_reports_repair_count(db_path):
    ok = answer_json("What is the total revenue?", db_path, metrics=JSONOutputMetrics())
    assert ok.repairs == 0 and ok.payload["row_count"] == 1

    def flaky(trace, hint=None):
        return render_result_json(trace) if hint else "{oops"

    fixed = answer_json("What is the total revenue?", db_path, producer=flaky,
                        metrics=JSONOutputMetrics())
    assert fixed.repairs == 1 and _json.loads(fixed.raw) == fixed.payload


def test_metrics_track_first_pass_repair_and_escalation(db_path):
    m = JSONOutputMetrics()
    q = "What is the total revenue?"
    answer_json(q, db_path, metrics=m)
    answer_json(q, db_path, metrics=m,
                producer=lambda t, h=None: render_result_json(t) if h else "{")
    with pytest.raises(JSONEscalation):
        answer_json(q, db_path, metrics=m, producer=lambda t, h=None: "{")
    assert (m.calls, m.first_pass_valid, m.repaired, m.escalated) == (3, 1, 1, 1)
    assert m.repair_attempts == 2
    assert m.repair_rate == pytest.approx(1 / 3)
    assert m.escalation_rate == pytest.approx(1 / 3)
    assert m.error_types["invalid"] == 2
    assert "escalation rate 0.33" in m.summary()


def test_zero_repair_budget_escalates_without_retry(db_path):
    calls = []

    def bad(trace, hint=None):
        calls.append(hint)
        return "{"

    with pytest.raises(JSONEscalation):
        answer_json("What is the total revenue?", db_path, producer=bad, max_repairs=0,
                    metrics=JSONOutputMetrics())
    assert calls == [None]


def test_escalation_redacts_raw_output(db_path, caplog):
    secret_row = "alice@example.com 99999.99"

    def leaky(trace, hint=None):
        return _json.dumps({"query": "q", "rows": [[secret_row]], "row_count": 7,
                            "citations": ["x"]})

    with caplog.at_level(logging.INFO, logger="text2sql.jsonout"):
        with pytest.raises(JSONEscalation) as ei:
            answer_json("What is the total revenue?", db_path, producer=leaky,
                        metrics=JSONOutputMetrics())
    exc = ei.value
    assert secret_row not in str(exc) and "alice" not in str(exc)
    assert "raw output withheld" in str(exc) and len(exc.raw_sha256) == 12
    assert exc.raw_len > 0 and any("row_count" in e for e in exc.errors)
    assert "alice" not in caplog.text and "escalated" in caplog.text


def test_success_is_logged_with_repair_count(db_path, caplog):
    with caplog.at_level(logging.INFO, logger="text2sql.jsonout"):
        answer_json("Revenue by region", db_path, metrics=JSONOutputMetrics())
    assert "repairs=0" in caplog.text
