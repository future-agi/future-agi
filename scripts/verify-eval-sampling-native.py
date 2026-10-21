#!/usr/bin/env python3
"""Small real-ClickHouse sampling proof without a server or shared Docker.

Run with the backend's Python environment and an installed, verified ClickHouse
25.8+ binary: python scripts/verify-eval-sampling-native.py --clickhouse /path/to/clickhouse
This is a SQL correctness fixture, not a production-scale benchmark or a
substitute for PostgreSQL migrations and the managed browser flow.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "ba052ed0-921f-4aa9-972d-7bbbc79e5d30"
NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
DDL = """
CREATE TABLE spans (
 project_id UUID, project_version_id Nullable(UUID), id String, trace_id String, trace_session_id Nullable(UUID), end_user_id Nullable(UUID),
 parent_span_id String DEFAULT '', start_time DateTime64(6,'UTC'), created_at DateTime64(6,'UTC'), updated_at DateTime64(6,'UTC') DEFAULT created_at,
 end_time Nullable(DateTime64(6,'UTC')), cost Float64 DEFAULT 0, total_tokens Int32 DEFAULT 0,
 observation_type String DEFAULT 'llm', service_name String DEFAULT '', name String DEFAULT '', status String DEFAULT 'OK',
 attrs_string Map(String,String), attrs_number Map(String,Float64), attrs_bool Map(String,UInt8),
 attributes_extra JSON, resource_attrs JSON, metadata JSON, is_deleted UInt8 DEFAULT 0, _version UInt64 DEFAULT 1
) ENGINE=ReplacingMergeTree(_version,is_deleted) PARTITION BY toDate(start_time)
ORDER BY (project_id,observation_type,service_name,toStartOfHour(start_time),trace_id,id);
CREATE TABLE trace_session_id_remap (old_id UUID,new_id UUID,version UInt64 DEFAULT 1)
ENGINE=ReplacingMergeTree(version) ORDER BY old_id;
CREATE TABLE end_user_id_remap (old_id UUID,new_id UUID,version UInt64 DEFAULT 1)
ENGINE=ReplacingMergeTree(version) ORDER BY old_id;
"""


def generate(directory: Path) -> None:
    # Separate process: importing the app must not patch the parent subprocess
    # transport used to run an independent native ClickHouse binary.
    sys.path.insert(0, str(ROOT / "futureagi"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.test")
    import django

    django.setup()
    from clickhouse_driver.util.escape import escape_param, escape_params
    from tracer.services.eval_tasks.sampling.policy import (
        validate_policy,
        unit_hash,
        rate_selects,
    )
    from tracer.services.eval_tasks.sampling.query import (
        SamplingQuery,
        native_parameters,
    )

    def literal(value):
        return escape_param(value, None)

    def identity(name):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, name))

    def contact(name, **extra):
        session = identity(name)
        rows = []
        for trace_index in range(2):
            trace = identity(f"{name}/trace/{trace_index}")
            root = uuid.uuid5(uuid.NAMESPACE_URL, trace).hex[:16]
            for child in range(2):
                rows.append(
                    {
                        "id": root
                        if not child
                        else uuid.uuid5(uuid.NAMESPACE_URL, f"{trace}/child").hex[:16],
                        "trace": trace,
                        "session": session,
                        "parent": "" if not child else root,
                        **extra,
                    }
                )
        return rows

    def insert(rows):
        values = []
        for row in rows:
            attributes = row.get("attributes", {})
            map_sql = (
                "map("
                + ",".join(
                    str(literal(value)) for pair in attributes.items() for value in pair
                )
                + ")"
            )
            boolean_sql = f"map('positive',{int(row.get('positive', False))})"
            fields = [
                PROJECT,
                row["id"],
                row["trace"],
                row.get("session"),
                row.get("user"),
                row.get("parent", ""),
                "2026-10-19 12:00:00",
                "2026-10-19 12:00:01",
                row.get("type", "llm"),
            ]
            values.append(
                "("
                + ",".join(str(literal(value)) for value in fields)
                + f",{map_sql},{boolean_sql},{row.get('version', 1)})"
            )
        return (
            "INSERT INTO spans (project_id,id,trace_id,trace_session_id,end_user_id,parent_span_id,start_time,created_at,observation_type,attrs_string,attrs_bool,_version) VALUES "
            + ",".join(values)
            + ";\n"
        )

    base = {
        "version": 1,
        "sampling_unit": "session",
        "mode": "closed",
        "window": {"start": "2026-10-19T00:00:00Z", "end": "2026-10-20T00:00:00Z"},
        "default": {"target_n": 1},
    }
    cases = []

    def case(
        name,
        rows,
        policy,
        method,
        expected,
        *,
        remap="",
        row_type="spans",
        filters=None,
        row_options=None,
    ):
        task = SimpleNamespace(
            project_id=PROJECT, row_type=row_type, filters=filters or {}
        )
        clean = validate_policy(policy, now=NOW, row_type=row_type)
        query = SamplingQuery(task, clean, "native-proof", NOW)
        if method == "witnesses":
            from tracer.services.eval_tasks.sampling.witnesses import TraceWitnessReader

            sql, parameters = TraceWitnessReader(task).build_query(
                sorted({row["trace"] for row in rows})
            )
        else:
            sql, parameters = getattr(query, method)(**(row_options or {}))
        parameters = native_parameters(parameters)
        rendered = sql % escape_params(parameters, None)
        path = directory / f"{name}.sql"
        path.write_text(
            DDL + insert(rows) + remap + rendered + " FORMAT JSONEachRow;\n"
        )
        cases.append({"name": name, "sql": str(path), "expected": expected})

    rows = contact("first") + contact("second")
    units = [f"session:{identity(name)}" for name in ("first", "second")]
    winner = min(units, key=lambda unit: unit_hash("native-proof", unit))
    case(
        "closed-hash-quota",
        rows,
        base,
        "members",
        {"units": [winner], "probabilities": [0.5], "populations": [2]},
    )
    case(
        "contact-row-counts",
        rows,
        base,
        "preview",
        {"cells": 1, "eligible_units": 2, "selected_units": 1, "selected_rows": 4},
    )
    case(
        "trace-rows-session-unit",
        rows,
        base,
        "preview",
        {"cells": 1, "eligible_units": 2, "selected_units": 1, "selected_rows": 2},
        row_type="traces",
    )
    case(
        "session-rows-session-unit",
        rows,
        base,
        "preview",
        {"cells": 1, "eligible_units": 2, "selected_units": 1, "selected_rows": 1},
        row_type="sessions",
    )
    population = []
    expected_units = []
    for locale in range(5):
        for category in range(4):
            cell = []
            for index in range(2 if category == 3 else 3):
                name = f"l{locale}/c{category}/{index}"
                population.extend(
                    contact(
                        name,
                        attributes={"locale": f"l{locale}", "category": f"c{category}"},
                        positive=category == 3,
                    )
                )
                cell.append(f"session:{identity(name)}")
            expected_units.extend(
                sorted(cell, key=lambda unit: unit_hash("native-proof", unit))[:2]
            )
    stratified = {
        **base,
        "group_by": [{"key": "locale"}, {"key": "category"}],
        "strata": [
            {
                "name": "rare",
                "filters": [{"key": "positive", "op": "in", "values": [True]}],
                "rate": 10,
                "rare_below": 2,
            },
            {"name": "control", "filters": [], "target_n": 2},
        ],
        "default": {"target_n": 0},
    }
    case(
        "twenty-cells-ordered-overlap",
        population,
        stratified,
        "preview",
        {"cells": 20, "eligible_units": 55, "selected_units": 40, "selected_rows": 160},
    )
    case(
        "twenty-cells-exact-members",
        population,
        stratified,
        "members",
        {
            "units": sorted(expected_units),
            "probabilities": [2 / 3, 1],
            "populations": [2, 3],
        },
    )
    missing = contact("custom", attributes={"contact_id": "contact-one"})[:2] + [
        dict(row, session=None) for row in contact("missing")[:2]
    ]
    for behavior, count, row_count in (("exclude", 1, 2), ("row", 3, 4)):
        case(
            f"missing-custom-{behavior}",
            missing,
            {
                **base,
                "sampling_unit": "attribute",
                "unit_attribute": {"key": "contact_id"},
                "missing_id": behavior,
                "default": {"rate": 100},
            },
            "preview",
            {
                "cells": 1,
                "eligible_units": count,
                "selected_units": count,
                "selected_rows": row_count,
            },
        )
    for unit, column, table in (
        ("session", "session", "trace_session_id_remap"),
        ("user", "user", "end_user_id_remap"),
    ):
        old_a, old_b, new = [
            identity(f"{unit}-{name}") for name in ("old-a", "old-b", "new")
        ]
        mapped = []
        for index, value in enumerate((old_a, old_b, new)):
            mapped.extend(
                [
                    dict(row, **{column: value})
                    for row in contact(f"mapped-{unit}-{index}")[:1]
                ]
            )
        remap = f"INSERT INTO {table} (old_id,new_id) VALUES ('{old_a}','{new}'),('{old_b}','{new}');\n"
        case(
            f"canonical-{unit}",
            mapped,
            {**base, "sampling_unit": unit},
            "members",
            {
                "units": [f"{unit}:{min(old_a, old_b)}"],
                "probabilities": [1],
                "populations": [1],
            },
            remap=remap,
        )
    live_rows = [row for index in range(32) for row in contact(f"live-{index}")]
    expected_rows = sorted(
        row["id"]
        for row in live_rows
        if rate_selects("native-proof", f"session:{row['session']}", 12.5)
    )
    case(
        "live-contact-hash",
        live_rows,
        {**base, "mode": "live", "default": {"rate": 12.5}},
        "rows",
        {"selected_row_ids": expected_rows},
    )
    filtered = rows + [dict(row, type="embedding") for row in contact("excluded")]
    case(
        "row-filter-before-unit-population",
        filtered,
        base,
        "preview",
        {"cells": 1, "eligible_units": 2, "selected_units": 1, "selected_rows": 4},
        filters={"observation_type": ["llm"]},
    )
    witness_rows = contact("witness")
    for row in witness_rows:
        row["attributes"] = {"locale": "en" if row["parent"] else "excluded"}
    case(
        "filter-bound-trace-inputs",
        witness_rows,
        base,
        "witnesses",
        {
            "witness_span_ids": sorted(
                row["id"] for row in witness_rows if row["parent"]
            ),
        },
        row_type="traces",
        filters={
            "filters": [
                {
                    "column_id": "locale",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["en"],
                    },
                }
            ]
        },
    )
    late_rows = contact("late-arrival")
    late_rows[-1]["version"] = int(NOW.timestamp()) * 1_000_000_000 - 1_000_000_000
    case(
        "late-physical-arrival-old-event-time",
        late_rows,
        base,
        "rows",
        {
            "row_ids": [late_rows[-1]["id"]],
        },
        row_options={
            "arrival_floor": NOW - timedelta(seconds=5),
            "unit_ids": [f"session:{late_rows[-1]['session']}"],
        },
    )
    (directory / "cases.json").write_text(json.dumps(cases))


def verify(args) -> None:
    with tempfile.TemporaryDirectory(prefix="sampling-native-") as temporary:
        directory = Path(temporary)
        subprocess.run(
            [
                args.backend_python or sys.executable,
                str(Path(__file__).resolve()),
                "--generate",
                str(directory),
            ],
            check=True,
            cwd=ROOT,
        )
        cases = json.loads((directory / "cases.json").read_text())
        for case in cases:
            # Local mode auto-detects piped input; close stdin explicitly so
            # a non-interactive agent terminal cannot leave it waiting for data.
            result = subprocess.run(
                [
                    args.clickhouse,
                    "local",
                    "--max_threads=2",
                    "--max_memory_usage=268435456",
                    "--max_execution_time=10",
                    "--multiquery",
                    "--query",
                    Path(case["sql"]).read_text(),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode:
                raise SystemExit(f"FAIL {case['name']}: {result.stderr[:6000]}")
            rows = [json.loads(line) for line in result.stdout.splitlines() if line]
            expected = case["expected"]
            if "units" in expected:
                assert sorted(row["unit_id"] for row in rows) == sorted(
                    expected["units"]
                ), (case["name"], rows)
                assert (
                    sorted(set(row["inclusion_probability"] for row in rows))
                    == expected["probabilities"]
                ), (case["name"], rows)
                assert (
                    sorted(set(int(row["population_size"]) for row in rows))
                    == expected["populations"]
                ), (case["name"], rows)
            elif "row_ids" in expected:
                assert sorted(row["row_id"] for row in rows) == expected["row_ids"], (
                    case["name"],
                    rows,
                )
            elif "witness_span_ids" in expected:
                assert (
                    sorted(row["filter_witness_0"][0] for row in rows)
                    == expected["witness_span_ids"]
                ), (case["name"], rows)
            elif "selected_row_ids" in expected:
                assert (
                    sorted(row["row_id"] for row in rows if row["selected"])
                    == expected["selected_row_ids"]
                ), (case["name"], rows)
            else:
                assert len(rows) == expected["cells"], (case["name"], rows)
                for key in ("eligible_units", "selected_units", "selected_rows"):
                    assert sum(int(row[key]) for row in rows) == expected[key], (
                        case["name"],
                        key,
                        rows,
                    )
            print(f"PASS {case['name']}")
        print(
            f"PASS {len(cases)} native ClickHouse cases (at most 220 spans per case; not a load benchmark)"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clickhouse")
    parser.add_argument(
        "--backend-python",
        help="Backend virtualenv interpreter used only to compile queries",
    )
    parser.add_argument("--generate", type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.generate:
        generate(options.generate)
    elif options.clickhouse:
        verify(options)
    else:
        parser.error("--clickhouse requires a verified ClickHouse executable")
