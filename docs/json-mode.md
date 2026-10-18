# JSON output mode

`text2sql/jsonout.py` turns a pipeline trace into a machine-readable answer for
callers that parse the result instead of reading prose.

## Contract

```json
{"query": "Revenue by region",
 "rows": [["West", 41514.98], ["East", 36991.56]],
 "row_count": 2,
 "citations": ["interpretation: ...", "sql: SELECT ...", "explanation: ..."]}
```

`RESULT_SCHEMA` is the one place this shape is defined. It is plain JSON
Schema, so the same dict can go to a model as a response format. The
validator reads it directly instead of keeping its own copy of the rules:

| Rule | Source |
|---|---|
| `query`, `rows`, `row_count`, `citations` required | `required` |
| No other keys | `additionalProperties: false` |
| `query` is a non-empty string | `type`, `minLength` |
| `rows` is a list of lists | `type`, `items` |
| `row_count` is an integer >= 0, and `true`/`false` do not count | `type: integer`, `minimum` |
| `citations` has at least one non-empty string | `minItems`, `items` |
| No NaN, Infinity or -Infinity anywhere | strict parse plus a finiteness check |
| `row_count == len(rows)` | semantic rule in `validate_result` |

Clarify and guard-blocked traces still produce valid JSON: `rows` is empty
and the explanation goes into `citations`.

## Repair and escalation

1. The producer renders the trace (the SQL has already run, exactly once).
2. Strict parse: `json.loads` with `parse_constant` rejecting NaN/Infinity.
3. Schema validation. Any error starts a repair: the producer is called again
   with the error list (`max_repairs`, default 1).
4. If the output is still invalid, `JSONEscalation` is raised. Its message and
   attributes hold the errors, the raw length and the first 12 hex digits of
   the SHA-256. The raw text is withheld, because a bad answer can still hold
   row data such as PII. Unknown key names are cut to 32 characters for the
   same reason.

`answer_json()` returns `JSONAnswer(raw, payload, repairs)`.
`run_question_json()` keeps the older string-only return.

## Metrics and logging

`METRICS` (or a `JSONOutputMetrics` you pass in) counts `calls`,
`first_pass_valid`, `repaired`, `escalated`, `repair_attempts` and error
types. It also exposes `first_pass_rate`, `repair_rate` and
`escalation_rate`. Each call writes one log line on the `text2sql.jsonout`
logger with the repair count and raw length, and never the payload.

A rising repair rate means the producer is drifting from the schema. A
non-zero escalation rate means callers are getting errors. With a model as
producer, alert on both.

## Number formatting

Floats in `rows` are rounded to 2 decimal places, so `41514.979999999996`
becomes `41514.98`. That fits this money-only sample. A schema with non-money
floats would need precision set per column.
