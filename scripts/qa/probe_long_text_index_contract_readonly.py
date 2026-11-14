#!/usr/bin/env python3
"""Actual CH compiler/engine implication probe over constant-only span fixtures.

Run with the backend virtualenv and --production-read-only after source freeze.
No server tables are used unless --explain-plan is explicitly supplied; that
option only EXPLAINs the known metadata case and emits sanitized index counts.
This is supplementary engine evidence, never customer-data/SLO qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "11111111-1111-4111-8111-111111111111"
FOREIGN = "22222222-2222-4222-8222-222222222222"
START = datetime(2026, 8, 29)
END = datetime(2026, 9, 5)
KNOWN_CASE = "3f148d4ff2cbe101a49d7425"
SETTINGS = {
    "readonly": 2,
    "max_execution_time": 1,
    "max_threads": 1,
    "max_memory_usage": 32 * 1024**2,
    "max_result_rows": 1000,
    "max_result_bytes": 1024**2,
    "max_rows_to_read": 100_000,
    "max_bytes_to_read": 32 * 1024**2,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
    "use_query_cache": 0,
}
EXPLAIN_SETTINGS = {
    **SETTINGS,
    "max_execution_time": 15,
    "max_threads": 2,
    "max_memory_usage": 256 * 1024**2,
}
OPERATIONS = ("equals", "in", "contains", "starts_with", "ends_with")
NEEDLES = {
    "ascii_quotes": "A long quoted \"identifier\" 'value' 1234567890 tail",
    "literal_like": "Long literal percent_under_backslash %_\\  67890 tail",
    "kelvin": "k customer identifier 3456789012 uppercase letters tail",
    "dotted_i": "İ customer identifier 4567890123 literal Unicode tail",
    "combining": "Cafe\u0301 customer identifier 5678901234 Unicode tail",
    "controls": "Long whitespace identifier \t\n\r 6789012345 tail",
    "prose_letters": "Descripción de la respuesta del cliente y mensaje de retorno",
    "kelvin_letters": "bookkeeper response and outbound customer message in the café",
}
EXPECTED_CASE_COUNT = len(NEEDLES) * len(OPERATIONS)


def initialize():
    """Reuse setup only; prevent every network connection during Django boot."""
    from replay_observe_queries_readonly import initialize_candidate

    def forbidden(*_args, **_kwargs):
        raise RuntimeError("QA_STARTUP_NETWORK_DISABLED")

    os.environ["NO_STARTUP_DB_MUTATIONS"] = "true"
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    with (
        patch.object(socket.socket, "connect", forbidden),
        patch.object(socket, "create_connection", forbidden),
    ):
        initialize_candidate()
    logging.disable(logging.CRITICAL)


def fingerprint():
    paths = [
        Path(__file__),
        ROOT / "scripts/qa/test_long_text_index_contract_readonly.py",
        ROOT / "futureagi/tracer/services/clickhouse/query_builders/trace_list.py",
        ROOT
        / "futureagi/tracer/services/clickhouse/query_builders/latest_filter_predicates.py",
        ROOT / "futureagi/tracer/services/clickhouse/query_builders/filters.py",
        ROOT / "futureagi/tracer/services/clickhouse/v2/query_builders/trace_list.py",
        ROOT / "futureagi/tracer/services/clickhouse/v2/query_builders/filters.py",
        ROOT / "futureagi/tracer/services/clickhouse/v2/query_builders/_rewrite.py",
    ]
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def filters_for(operation, value):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START, END],
            },
        },
        {
            "column_id": "metadata",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": operation,
                "filter_value": value,
            },
        },
    ]


def actual_hint_expression(witness):
    """Extract the selected leading hint without changing its expression."""
    prefix = "indexHint("
    if not witness.startswith(prefix):
        raise ValueError("SELECTED_PLAN_MISSING_HINT")
    depth = 1
    for index in range(len(prefix), len(witness)):
        depth += (witness[index] == "(") - (witness[index] == ")")
        if depth == 0:
            return witness[len(prefix) : index]
    raise ValueError("UNBALANCED_COMPILER_HINT")


def enforce_literal_sources(sql):
    """Fail closed if compiler evolution introduces any server relation."""
    from tracer.services.clickhouse.server_readonly import ensure_read_statement

    ensure_read_statement(sql)
    allowed = {
        "fixture_spans",
        "exact_rows",
        "raw_witnesses",
        "candidate_rows",
        "matching_scalar_trace_identities",
    }
    sources = re.findall(r"\b(?:FROM|JOIN)\s+([\w.]+)", sql, flags=re.I)
    if not sources or any(source not in allowed for source in sources):
        raise ValueError("NON_LITERAL_SOURCE_IN_FIXTURE")


def values_where(sql):
    """Lower only storage clauses, keeping both predicates and their precedence.

    Track original parenthesis depth per SELECT, as in the session VALUES QA.
    Added parentheses do not change that tracking. Quoted text, identifiers,
    comments and bound placeholders are opaque; nested queries and UNION arms
    each retain their own WHERE. This is deliberately fixture-only lowering.
    """
    depth = 0
    opened = {}
    tokens = re.compile(
        r"'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\""
        r"|`(?:\\.|``|[^`\\])*`|--[^\n]*|/\*[\s\S]*?\*/|%\(\w+\)s"
        r"|\b(?:SELECT|PREWHERE|WHERE|GROUP|HAVING|ORDER|LIMIT|SETTINGS|FORMAT"
        r"|UNION|INTERSECT|EXCEPT|WINDOW|QUALIFY)\b|[();]",
        re.I,
    )

    def close():
        if depth in opened:
            del opened[depth]
            return ") "
        return ""

    def token(match):
        nonlocal depth
        raw = match.group()
        word = raw.upper()
        if word == "(":
            depth += 1
        elif word == ")":
            prefix = close()
            depth -= 1
            if depth < 0:
                raise ValueError("UNBALANCED_FIXTURE_SQL")
            return prefix + raw
        elif word == "PREWHERE":
            if depth in opened:
                raise ValueError("DUPLICATE_FIXTURE_PREWHERE")
            opened[depth] = "prewhere"
            return "WHERE ("
        elif word == "WHERE" and depth in opened:
            if opened[depth] != "prewhere":
                raise ValueError("DUPLICATE_FIXTURE_WHERE")
            opened[depth] = "where"
            return ") AND ("
        elif word in {
            "SELECT",
            "GROUP",
            "HAVING",
            "ORDER",
            "LIMIT",
            "SETTINGS",
            "FORMAT",
            "UNION",
            "INTERSECT",
            "EXCEPT",
            "WINDOW",
            "QUALIFY",
            ";",
        }:
            return close() + raw
        return raw

    lowered = tokens.sub(token, sql)
    if depth:
        raise ValueError("UNBALANCED_FIXTURE_SQL")
    # A trailing line comment must not swallow the synthetic closing bracket.
    return lowered + ("\n)" if opened else "")


def fixture_rows(control, variant, operation, second=None):
    """Synthetic history with hand-declared structural expectations, not a matcher."""
    rows = []
    root_at = START + timedelta(days=2, hours=12)
    epoch = datetime(1970, 1, 1)

    def row(
        trace,
        span="root",
        value=None,
        *,
        at=root_at,
        parent="",
        version=1,
        deleted=0,
        service="svc",
        kind="SPAN",
        project=PROJECT,
        key="metadata",
    ):
        rows.append(
            (
                project,
                kind,
                service,
                trace,
                span,
                (at - epoch) // timedelta(microseconds=1),
                parent,
                version,
                deleted,
                [] if value is None else [key],
                [] if value is None else [value],
            )
        )

    def text(value):
        if operation == "contains":
            return "prefix " + value + " suffix"
        if operation == "starts_with":
            return value + " suffix"
        if operation == "ends_with":
            return "prefix " + value
        return value

    match = text(control)
    row("live", value=match)
    row("duplicate", value=match)
    row("duplicate", value=match)
    for trace, at in (
        ("late_child", END + timedelta(days=2)),
        ("early_child", START - timedelta(days=2)),
    ):
        row(trace)
        row(trace, "child", match, at=at, parent="root")
    row("stale", value=match)
    row("stale", value="unrelated", version=2, at=root_at + timedelta(seconds=1))
    row("tombstone")
    row("tombstone", "child", match, parent="root")
    row("tombstone", "child", match, parent="root", version=2, deleted=1)
    row("missing")
    row("missing", value=match, project=FOREIGN)  # Project identity collision.
    row("removed_key", value=match)
    row("removed_key", value=match, version=2, key="other_key")
    row("false_positive_hint", value=control[-20:])
    for trace, changed in (
        ("service_identity", {"service": "other"}),
        ("kind_identity", {"kind": "GENERATION"}),
        ("hour_identity", {"at": root_at + timedelta(hours=1)}),
    ):
        row(trace)
        row(trace, "reused", match, parent="root")
        row(trace, "reused", "unrelated", parent="root", version=99, **changed)
    row("surviving_root", value=match)
    row("surviving_root", "newer_root", at=root_at + timedelta(hours=1))
    row(
        "surviving_root",
        "newer_root",
        at=root_at + timedelta(hours=1),
        version=2,
        deleted=1,
    )
    row("outside_root", value=match, at=END + timedelta(days=1))
    row("unicode_variant", value=text(variant))
    expected = {
        "live",
        "duplicate",
        "late_child",
        "early_child",
        "service_identity",
        "kind_identity",
        "hour_identity",
        "surviving_root",
    }
    if second is not None:
        row("second_in_operand", value=second)
        expected.add("second_in_operand")
    return rows, frozenset(expected)


LITERAL_CTE = """fixture_spans AS (
    SELECT toUUID(entry.1) AS project_id, entry.2 AS observation_type,
        entry.3 AS service_name, entry.4 AS trace_id, entry.5 AS id,
        fromUnixTimestamp64Micro(toInt64(entry.6)) AS start_time,
        entry.7 AS parent_span_id, toUInt64(entry.8) AS _version,
        toUInt8(entry.9) AS is_deleted,
        CAST(NULL AS Nullable(UUID)) AS project_version_id,
        mapFromArrays(entry.10, entry.11) AS attrs_string
    FROM (SELECT arrayJoin(%(fixture_rows)s) AS entry)
)"""


@dataclass(frozen=True)
class Case:
    name: str
    sql: str
    params: dict
    expected: frozenset[str]
    fixture_ids: frozenset[str]


def build_case(variant_name, operation):
    from tracer.services.clickhouse.server_readonly import without_query_settings
    from tracer.services.clickhouse.v2.query_builders.filters import (
        rewrite_v1_sql_to_v2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    needle = NEEDLES[variant_name]
    second = "Second unrelated identifier 9999988888 alternate branch"
    value = [needle, second] if operation == "in" else needle
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=filters_for(operation, value), page_size=25
    )
    builder.TABLE = (
        "fixture_spans"  # Only the source is replaced, not predicates/replay.
    )
    selected = builder._public_long_text_candidate_seed_plan()
    if selected is None:
        raise ValueError("FIXTURE_PLAN_DECLINED")
    bound = selected.params["latest_filter_param_0"]
    control = bound[0] if isinstance(bound, tuple) else bound
    if variant_name in {"kelvin", "kelvin_letters"}:
        variant = needle.replace("k", "K")
    elif variant_name == "prose_letters":
        variant = needle.upper()
    else:
        variant = needle
    rows, expected = fixture_rows(
        control, variant, operation, second=bound[1] if operation == "in" else None
    )
    ids = sorted({row[3] for row in rows})
    exact, exact_params = builder.build_filter_match_query(
        ids,
        candidate_identity_only=True,
        include_filter_witnesses=False,
    )
    candidate, candidate_params = builder.build_filter_candidate_seed_page(
        slice_start=START,
        slice_end=END,
        limit=512,
    )
    witness = rewrite_v1_sql_to_v2(selected.raw_graph_value_witness_predicate)
    # Crucial: indexHint() returns true. Exercise its actual LIKE obligation,
    # and the existing key hints, as real predicates in this constant fixture.
    evaluated_witness = re.sub(r"\bindexHint\s*\(", "(", witness)
    hint = rewrite_v1_sql_to_v2(
        actual_hint_expression(selected.raw_graph_value_witness_predicate)
    )
    sql = f"""WITH {LITERAL_CTE},
        exact_rows AS ({without_query_settings(exact)}),
        candidate_rows AS ({without_query_settings(candidate)}),
        raw_witnesses AS (
            SELECT DISTINCT trace_id FROM fixture_spans
            WHERE project_id = toUUID(%(fixture_project)s) AND ({evaluated_witness})
        )
        SELECT 'exact' AS evidence, trace_id FROM exact_rows
        UNION ALL SELECT 'witness', trace_id FROM raw_witnesses
        UNION ALL SELECT 'seed', trace_id FROM candidate_rows
        UNION ALL SELECT 'hint', trace_id FROM fixture_spans
            WHERE project_id = toUUID(%(fixture_project)s) AND ({hint})
    """
    sql = values_where(sql)
    enforce_literal_sources(sql)
    params = {
        **exact_params,
        **candidate_params,
        **selected.params,
        "fixture_project": PROJECT,
        "fixture_rows": rows,
    }
    return Case(f"{variant_name}:{operation}", sql, params, expected, frozenset(ids))


def assess(case, result):
    groups = {name: set() for name in ("exact", "witness", "seed", "hint")}
    for kind, trace_id in result:
        if kind not in groups or trace_id not in case.fixture_ids:
            raise ValueError("UNEXPECTED_ENGINE_FIXTURE_RESULT")
        groups[kind].add(trace_id)
    exact = groups["exact"]
    # Unicode variant behavior is observed from CH, never simulated using
    # Python's lower/casefold. Controlled latest-state cases must match exactly.
    structural_match = (exact - {"unicode_variant"}) == set(case.expected)
    missing = {
        name: sorted(exact - groups[name]) for name in ("witness", "seed", "hint")
    }
    return {
        "case": case.name,
        "passed": structural_match and not any(missing.values()),
        "structural_match": structural_match,
        "exact_fixture_ids": sorted(exact),
        "missing_necessary_witnesses": missing,
        "unicode_variant_exact_match": "unicode_variant" in exact,
    }


def build_known_explain(plan):
    from tracer.services.clickhouse.server_readonly import without_query_settings
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    cases = [case for case in plan.get("cases", []) if case.get("id") == KNOWN_CASE]
    if len(cases) != 1:
        raise ValueError("KNOWN_EXPLAIN_CASE_REQUIRED")
    case = cases[0]
    request = case["request"]
    if request.get("path") != "/tracer/trace/list_traces_of_session/":
        raise ValueError("KNOWN_EXPLAIN_ROUTE_REQUIRED")
    params = request["params"]
    filters = json.loads(params["filters"])
    attrs = [leaf for leaf in filters if leaf.get("column_id") != "created_at"]
    if len(attrs) != 1 or attrs[0].get("column_id") != "metadata":
        raise ValueError("KNOWN_METADATA_FILTER_REQUIRED")
    config = attrs[0]["filter_config"]
    if (config.get("col_type"), config.get("filter_type"), config.get("filter_op")) != (
        "SPAN_ATTRIBUTE",
        "text",
        "contains",
    ):
        raise ValueError("KNOWN_METADATA_CONTRACT_REQUIRED")
    builder = TraceListQueryBuilderV2(
        project_id=params["project_id"], filters=filters, page_size=25
    )
    if builder._public_long_text_candidate_seed_plan() is None:
        raise ValueError("KNOWN_EXPLAIN_PLAN_NOT_ANCHORABLE")
    start, end = builder.parse_time_range(filters)
    sql, values = builder.build_filter_candidate_seed_page(
        slice_start=start, slice_end=end, limit=50
    )
    return "EXPLAIN indexes = 1, json = 1 " + without_query_settings(sql), values


def sanitized_indexes(explain_json):
    """Never retain conditions, SQL, keys, table names, literals, or exceptions."""
    found = []

    def walk(value):
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            for item in value.get("Indexes", []):
                name = item.get("Name", item.get("Type", ""))
                if not isinstance(name, str) or not re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*", name
                ):
                    continue
                counts = {}
                for key in (
                    "Initial Parts",
                    "Selected Parts",
                    "Initial Granules",
                    "Selected Granules",
                ):
                    count = item.get(key)
                    if type(count) is int and count >= 0:
                        counts[key] = count
                if counts:
                    found.append({"name": name, **counts})
            for key, child in value.items():
                if key != "Indexes":
                    walk(child)

    walk(explain_json)
    return found


def safe_error(exc):
    code = getattr(exc, "code", None)
    result = {
        "error_type": type(exc).__name__,
        "error_code": code if type(code) is int else None,
    }
    if code == 158:
        result["error_name"] = "TOO_MANY_ROWS"
        # A number alone cannot distinguish estimated reads from other limits.
        # Inspect only the driver's message; retain a fixed setting name, never
        # SQL, table names, numbers, stack traces or any message substring.
        message = getattr(exc, "message", None)
        if isinstance(message, str):
            for setting in ("max_rows_to_read", "max_rows_to_read_leaf"):
                if f"rows (controlled by '{setting}' setting)" in message:
                    result["limit_setting"] = setting
                    break
    return result


def run(client, *, expected_server, explain_plan=None):
    identity = client.execute(
        "SELECT hostName(), currentDatabase(), version()", settings=SETTINGS
    )[0]
    if identity[:2] != (expected_server, "default"):
        raise ValueError("DATABASE_TARGET_MISMATCH")
    results = []
    for variant in NEEDLES:
        for operation in OPERATIONS:
            case = build_case(variant, operation)
            try:
                results.append(
                    assess(
                        case, client.execute(case.sql, case.params, settings=SETTINGS)
                    )
                )
            except Exception as exc:
                results.append({"case": case.name, "passed": False, **safe_error(exc)})
    report = {
        "source": "constant_CTE_only",
        "engine_version": identity[2],
        "qualified_customer_data": False,
        "executed": len(results),
        "passed": sum(item["passed"] for item in results),
        "cases": results,
    }
    if explain_plan is not None:
        try:
            sql, params = build_known_explain(explain_plan)
            rows = client.execute(sql, params, settings=EXPLAIN_SETTINGS)
            report["explain_indexes"] = sanitized_indexes(
                json.loads("\n".join(row[0] for row in rows))
            )
            if not report["explain_indexes"]:
                report["explain_error"] = {
                    "error_type": "NoIndexEvidence",
                    "error_code": None,
                }
        except Exception as exc:
            report["explain_error"] = safe_error(exc)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-read-only", action="store_true", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--expected-server", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--explain-plan",
        type=Path,
        help="Optional local plan containing the known metadata case",
    )
    args = parser.parse_args()
    source_before = fingerprint()
    initialize()
    from clickhouse_driver import Client
    from replay_observe_filters import canonical, private_write

    client = Client(
        "127.0.0.1",
        port=args.port,
        database="default",
        user=os.environ.get("OBSERVE_CH_USER", "default"),
        password=os.environ.get("OBSERVE_CH_PASSWORD", ""),
        connect_timeout=3,
        send_receive_timeout=20 if args.explain_plan else 3,
        settings=SETTINGS,
    )
    try:
        plan = json.loads(args.explain_plan.read_text()) if args.explain_plan else None
        report = run(client, expected_server=args.expected_server, explain_plan=plan)
    except Exception as exc:
        report = {"passed": 0, "executed": 0, **safe_error(exc)}
    finally:
        client.disconnect()
    source_after = fingerprint()
    report.update(
        source_sha256=source_before,
        source_after_sha256=source_after,
        source_unchanged=source_before == source_after,
        captured_at=datetime.now(UTC).isoformat(),
    )
    if source_before != source_after:
        report.update(
            reason="SOURCE_CHANGED_DURING_RUN_REJECT_RESULTS",
            rejected_fixture_passes=report.get("passed", 0),
            passed=0,
        )
    private_write(args.output, report)
    print(canonical(report))
    # Optional planner evidence is independent of constant-fixture correctness.
    return (
        0
        if report.get("executed") == EXPECTED_CASE_COUNT
        and report["passed"] == EXPECTED_CASE_COUNT
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
