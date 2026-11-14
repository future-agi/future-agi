"""Regenerate the SQL workbench capability matrix from live execution.

A fixed corpus runs through the production path on the owned PostgreSQL and
ClickHouse services: the compiler, admission, the worker thread and the
locked read-only runner, plus saved queries, export, the pre-run estimate
and result reuse through the service layer. Each case ends as

* ``supported``: accepted and completed on ClickHouse;
* ``rejected``: refused before reaching ClickHouse, with the reason;
* ``failed``: accepted, but the server or the worker failed.

The matrix is machine-readable JSON, so the claims in
docs/sql/query-workbench.rst ("Capability matrix") can be regenerated:

    python futureagi/scripts/sql_workbench/capability_check.py [--out PATH]

The script needs the environment of the live SQL tests (a PostgreSQL test
database and a ClickHouse server reachable as the test sidecar). It runs
``tracer/sql_workbench/tests/test_clickhouse.py::
test_capability_matrix_is_regenerated_live``, which executes every case,
asserts each status against the expectation below and writes the matrix;
the script then prints it. Expectations are what the code is meant to do:
a mismatch fails the run rather than silently changing the matrix.
"""

import argparse
import contextlib
import os
import sys
import tempfile
from pathlib import Path

SUPPORTED, REJECTED, FAILED = "supported", "rejected", "failed"

# (id, category, sql, expected status)
QUERIES = [
    # Aggregation and analytical functions
    (
        "agg-group-by",
        "aggregation",
        "SELECT model, count() AS n, sum(total_tokens) AS tokens FROM spans "
        "GROUP BY model ORDER BY n DESC",
        SUPPORTED,
    ),
    (
        "agg-percentiles",
        "aggregation",
        "SELECT quantiles(0.5, 0.95, 0.99)(latency_ms) AS q FROM spans",
        SUPPORTED,
    ),
    (
        "agg-distinct-topk",
        "aggregation",
        "SELECT uniqExact(trace_id) AS traces, topK(3)(model) AS top FROM spans",
        SUPPORTED,
    ),
    (
        "agg-conditional",
        "aggregation",
        "SELECT countIf(status = 'ERROR') AS errors, "
        "avgIf(latency_ms, status = 'OK') AS ok_latency FROM spans",
        SUPPORTED,
    ),
    (
        "agg-having",
        "aggregation",
        "SELECT name, count() AS n FROM spans GROUP BY name HAVING n >= 1 "
        "ORDER BY name",
        SUPPORTED,
    ),
    (
        "agg-time-buckets",
        "aggregation",
        "SELECT toStartOfInterval(start_time, INTERVAL 5 MINUTE) AS bucket, "
        "count() AS n FROM spans GROUP BY bucket ORDER BY bucket",
        SUPPORTED,
    ),
    (
        "agg-json-attributes",
        "aggregation",
        "SELECT JSONExtractString(attrs_string['tool_args'], 'tool') AS tool, "
        "count() AS n FROM spans GROUP BY tool",
        SUPPORTED,
    ),
    # Joins
    (
        "join-inner",
        "join",
        "SELECT s.model AS model, avg(e.score) AS score FROM eval_results AS e "
        "JOIN spans AS s ON e.span_id = s.id GROUP BY s.model",
        SUPPORTED,
    ),
    (
        "join-left-anti",
        "join",
        "SELECT count() AS n FROM spans AS s LEFT JOIN eval_results AS e "
        "ON s.id = e.span_id WHERE e.id IS NULL",
        SUPPORTED,
    ),
    (
        "join-three-way",
        "join",
        "SELECT u.external_id AS user_name, count() AS spans_seen, "
        "count(e.id) AS evals FROM users AS u JOIN spans AS s ON s.user_id = u.id "
        "LEFT JOIN eval_results AS e ON e.span_id = s.id GROUP BY u.external_id",
        SUPPORTED,
    ),
    (
        "join-sessions-evals",
        "join",
        "SELECT s.external_id AS session, countIf(e.passed = 0) AS failures "
        "FROM sessions AS s JOIN eval_results AS e ON s.id = e.session_id "
        "GROUP BY s.external_id",
        SUPPORTED,
    ),
    (
        "join-annotations-evals",
        "join",
        "SELECT a.value AS label, avg(e.score) AS score FROM annotations AS a "
        "JOIN eval_results AS e ON a.span_id = e.span_id GROUP BY a.value",
        SUPPORTED,
    ),
    (
        "join-range-predicate",
        "join",
        "SELECT count() AS n FROM spans AS a JOIN spans AS b "
        "ON a.trace_id = b.trace_id AND b.start_time > a.start_time",
        REJECTED,
    ),
    (
        "join-cross",
        "join",
        "SELECT count() AS n FROM spans CROSS JOIN traces",
        REJECTED,
    ),
    # CTEs, set operations and subqueries
    (
        "cte-multiple",
        "cte",
        "WITH t AS (SELECT trace_id FROM spans), "
        "u AS (SELECT toString(id) AS trace_id FROM traces) "
        "SELECT count() AS n FROM t JOIN u ON t.trace_id = u.trace_id",
        SUPPORTED,
    ),
    (
        "cte-recursive",
        "cte",
        "WITH RECURSIVE r AS (SELECT 1 AS n FROM spans) SELECT n FROM r",
        REJECTED,
    ),
    (
        "union-all",
        "set_operation",
        "SELECT span_id, toString(score) AS v, 'eval' AS src FROM eval_results "
        "UNION ALL SELECT span_id, value AS v, 'human' AS src FROM annotations",
        SUPPORTED,
    ),
    (
        "intersect-in-subquery",
        "set_operation",
        "SELECT count() AS n FROM spans WHERE session_id IN "
        "(SELECT session_id FROM eval_results INTERSECT "
        "SELECT session_id FROM annotations)",
        SUPPORTED,
    ),
    (
        "intersect-top-level",
        "set_operation",
        "SELECT session_id FROM eval_results INTERSECT "
        "SELECT session_id FROM annotations",
        REJECTED,
    ),
    (
        "except-top-level",
        "set_operation",
        "SELECT session_id FROM eval_results EXCEPT SELECT session_id FROM annotations",
        REJECTED,
    ),
    (
        "subquery-in",
        "subquery",
        "SELECT count() AS n FROM spans WHERE trace_id IN "
        "(SELECT toString(id) FROM traces)",
        SUPPORTED,
    ),
    (
        "subquery-scalar",
        "subquery",
        "SELECT name, latency_ms - (SELECT avg(latency_ms) FROM spans) AS delta "
        "FROM spans",
        SUPPORTED,
    ),
    (
        "subquery-derived-table",
        "subquery",
        "SELECT avg(t) AS avg_tokens FROM "
        "(SELECT trace_id, sum(total_tokens) AS t FROM spans GROUP BY trace_id)",
        SUPPORTED,
    ),
    (
        "in-table-name",
        "subquery",
        "SELECT count() AS n FROM spans WHERE id IN spans",
        REJECTED,
    ),
    # Window functions
    (
        "window-ranking",
        "window",
        "SELECT model, latency_ms, dense_rank() OVER "
        "(PARTITION BY model ORDER BY latency_ms DESC) AS r FROM spans",
        SUPPORTED,
    ),
    (
        "window-lag-lead",
        "window",
        "SELECT trace_id, latency_ms, "
        "lag(latency_ms) OVER (PARTITION BY trace_id ORDER BY start_time) AS prev, "
        "lead(latency_ms) OVER (PARTITION BY trace_id ORDER BY start_time) AS next "
        "FROM spans",
        SUPPORTED,
    ),
    (
        "window-running-frame",
        "window",
        "SELECT start_time, sum(cost) OVER (ORDER BY start_time ROWS BETWEEN "
        "UNBOUNDED PRECEDING AND CURRENT ROW) AS running FROM spans",
        SUPPORTED,
    ),
    (
        "window-distribution",
        "window",
        "SELECT id, ntile(4) OVER (ORDER BY latency_ms) AS quartile, "
        "percent_rank() OVER (ORDER BY latency_ms) AS pr, "
        "cume_dist() OVER (ORDER BY latency_ms) AS cd FROM spans",
        SUPPORTED,
    ),
    (
        "window-over-cte-join",
        "window",
        "WITH spend AS (SELECT user_id, sum(cost) AS c FROM spans GROUP BY user_id) "
        "SELECT u.external_id AS user_name, rank() OVER (ORDER BY s.c DESC) AS r "
        "FROM spend AS s JOIN users AS u ON s.user_id = u.id",
        SUPPORTED,
    ),
    (
        "window-named",
        "window",
        "SELECT row_number() OVER w AS n FROM spans WINDOW w AS (ORDER BY start_time)",
        REJECTED,
    ),
    (
        "window-groups-frame",
        "window",
        "SELECT sum(cost) OVER (ORDER BY start_time GROUPS BETWEEN 1 PRECEDING "
        "AND CURRENT ROW) AS s FROM spans",
        REJECTED,
    ),
    # Functions that stay outside the analytical set
    (
        "function-lambda",
        "function",
        "SELECT count() AS n FROM spans WHERE "
        "length(arrayFilter(x -> x = 'pii', mapKeys(attrs_string))) > 0",
        REJECTED,
    ),
    (
        "function-array-join",
        "function",
        "SELECT arrayJoin(mapKeys(attrs_string)) AS k FROM spans",
        REJECTED,
    ),
    (
        "function-unbounded-collection",
        "function",
        "SELECT groupArray(model) AS models FROM spans",
        REJECTED,
    ),
    (
        "function-dictionary",
        "function",
        "SELECT dictGet('d', 'a', toUInt64(1)) AS v FROM spans",
        REJECTED,
    ),
    (
        "function-side-effect",
        "function",
        "SELECT sleep(1) AS v FROM spans",
        REJECTED,
    ),
    # Sources and statements
    (
        "source-system-table",
        "source",
        "SELECT * FROM system.tables",
        REJECTED,
    ),
    (
        "source-table-function",
        "source",
        "SELECT * FROM numbers(10)",
        REJECTED,
    ),
    (
        "source-network",
        "source",
        "SELECT * FROM url('http://example.invalid', 'CSV')",
        REJECTED,
    ),
    (
        "source-qualified-physical",
        "source",
        "SELECT * FROM default.spans",
        REJECTED,
    ),
    (
        "source-unknown-dataset",
        "source",
        "SELECT count() AS n FROM prompts",
        REJECTED,
    ),
    (
        "statement-settings",
        "statement",
        "SELECT count() AS n FROM spans SETTINGS max_threads = 64",
        REJECTED,
    ),
    (
        "statement-multiple",
        "statement",
        "SELECT 1 AS n FROM spans; SELECT 2 AS n FROM spans",
        REJECTED,
    ),
    ("statement-ddl", "statement", "DROP TABLE spans", REJECTED),
    (
        "statement-dml",
        "statement",
        "INSERT INTO spans SELECT * FROM spans",
        REJECTED,
    ),
    (
        "statement-outfile",
        "statement",
        "SELECT * FROM spans INTO OUTFILE '/tmp/leak'",
        REJECTED,
    ),
]

# (id, category, what is exercised, expected status)
LIFECYCLE = [
    (
        "async-lifecycle",
        "lifecycle",
        "submit dispatches a worker; the worker completes it; poll reads it",
        SUPPORTED,
    ),
    ("cancel-queued", "lifecycle", "cancel a queued query", SUPPORTED),
    (
        "saved-query",
        "lifecycle",
        "save a project-shared definition and run it again",
        SUPPORTED,
    ),
    ("export-csv", "export", "CSV download of the result snapshot", SUPPORTED),
    (
        "export-parquet",
        "export",
        "Parquet download of the result snapshot",
        SUPPORTED,
    ),
    ("pre-run-estimate", "lifecycle", "advisory EXPLAIN ESTIMATE", SUPPORTED),
    (
        "result-reuse",
        "lifecycle",
        "identical request answered from the completed result, no new scan",
        SUPPORTED,
    ),
]


def case_ids():
    return [case[0] for case in QUERIES] + [case[0] for case in LIFECYCLE]


def matrix(results, environment):
    """The machine-readable matrix: one entry per case, plus counts."""
    expected = {case[0]: case[3] for case in QUERIES + LIFECYCLE}
    if sorted(results) != sorted(expected):
        raise ValueError("Every corpus case needs exactly one result.")
    entries = []
    for case in QUERIES + LIFECYCLE:
        result = results[case[0]]
        entries.append(
            {
                "id": case[0],
                "category": case[1],
                "kind": "query" if case in QUERIES else "lifecycle",
                "input": case[2],
                "status": result["status"],
                "expected": case[3],
                "reason": result.get("reason", ""),
                "rows": result.get("rows"),
            }
        )
    summary = {}
    for entry in entries:
        counts = summary.setdefault(entry["category"], {})
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "environment": environment,
        "summary": summary,
        "matches_expectations": all(e["status"] == e["expected"] for e in entries),
        "cases": entries,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="Write the matrix JSON here as well.")
    parser.add_argument(
        "--reuse-db", action="store_true", help="Reuse a verified test database."
    )
    args = parser.parse_args(argv)
    backend = Path(__file__).resolve().parents[2]
    output = Path(args.out).resolve() if args.out else None
    previous_cwd = Path.cwd()
    previous_target = os.environ.get("SQL_CAPABILITY_MATRIX_OUT")
    import pytest

    try:
        os.chdir(backend)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "sql-capability-matrix.json"
            os.environ["SQL_CAPABILITY_MATRIX_OUT"] = str(out)
            # Diagnostics go to stderr; stdout is exactly one JSON document.
            # Running in-process preserves a caller's network audit hook.
            with contextlib.redirect_stdout(sys.stderr):
                code = pytest.main(
                    [
                        "-q",
                        "-p",
                        "no:cacheprovider",
                        *(["--reuse-db"] if args.reuse_db else []),
                        "tracer/sql_workbench/tests/test_clickhouse.py::"
                        "test_capability_matrix_is_regenerated_live",
                    ]
                )
            if not out.exists():
                print("No matrix was produced; see test diagnostics.", file=sys.stderr)
                return code or 1
            text = out.read_text()
    finally:
        os.chdir(previous_cwd)
        if previous_target is None:
            os.environ.pop("SQL_CAPABILITY_MATRIX_OUT", None)
        else:
            os.environ["SQL_CAPABILITY_MATRIX_OUT"] = previous_target
    if output:
        output.write_text(text)
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
