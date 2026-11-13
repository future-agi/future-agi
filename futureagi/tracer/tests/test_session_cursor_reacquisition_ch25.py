"""Engine regressions for held Session matches across bounded checkpoints."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests import test_span_latest_state_engine_ch25 as engine_helper
from tracer.views.trace_session import _session_list_cursor_order_for_partial_page

pytestmark = pytest.mark.integration

PROJECT = "11111111-1111-4111-8111-111111111111"
START = datetime(2026, 8, 8)
END = START + timedelta(days=1)
SESSIONS = {
    "A": "00000000-0000-4000-8000-000000000001",
    "B": "00000000-0000-4000-8000-000000000002",
    "C": "00000000-0000-4000-8000-000000000003",
    "D": "00000000-0000-4000-8000-000000000004",
}
OLD_B = "00000000-0000-4000-8000-000000000090"
NEW_B = "00000000-0000-4000-8000-000000000091"


def _filters(operation: str = "in") -> list[dict[str, Any]]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START, END],
            },
        },
        {
            "column_id": "status",
            "property_id": "system_attribute:sessions:status",
            "filter_config": {
                "filter_type": "text",
                "filter_op": operation,
                "filter_value": ["ERROR"],
                "col_type": "SYSTEM_METRIC",
            },
        },
    ]


def _builder(
    operation: str = "in", *, sampled: bool = False
) -> SessionListQueryBuilderV2:
    return SessionListQueryBuilderV2(
        project_id=PROJECT,
        filters=_filters(operation),
        page_number=0,
        page_size=25,
        bounded_internal_scan=True,
        **(
            {"bounded_sampling_salt": "cursor-test", "bounded_sampling_rate": 100}
            if sampled
            else {}
        ),
    )


class _Analytics:
    def __init__(self, engine: engine_helper.DockerEngine) -> None:
        self.engine = engine

    def execute_ch_query(
        self, query: str, params: dict[str, Any], **_kwargs: Any
    ) -> SimpleNamespace:
        context = SimpleNamespace(
            server_info=SimpleNamespace(get_timezone=lambda: "UTC")
        )
        sql = query % escape_params(params, context)
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                self.engine.container,
                "clickhouse-client",
                "--max_query_size=262144",
                "--max_execution_time=8",
                "--max_memory_usage=536870912",
                "--multiquery",
            ],
            input=sql + " FORMAT JSON",
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        payload = json.loads(result.stdout)
        for row in payload["data"]:
            for key in ("start_time", "session_start"):
                if isinstance(row.get(key), str):
                    row[key] = datetime.fromisoformat(row[key])
        return SimpleNamespace(
            data=payload["data"],
            columns=[column["name"] for column in payload["meta"]],
            rows_read=len(payload["data"]),
            bytes_read=len(result.stdout.encode()),
            query_time_ms=1,
        )


@pytest.fixture(scope="module")
def engine() -> Iterator[engine_helper.DockerEngine]:
    instance = engine_helper.DockerEngine()
    if not instance.available():
        pytest.skip("the existing ClickHouse 25.3 image is unavailable; never pull")
    try:
        instance.start()
        subprocess.run(
            [
                "docker",
                "update",
                "--memory=1073741824",
                "--memory-swap=1073741824",
                instance.container,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        with mock.patch.object(
            engine_helper,
            "SCHEMA_FILES",
            (
                "002_spans_v2.sql",
                "019_id_remap.sql",
                "012_trace_name_and_count_rollup.sql",
                "015_traces_and_trace_dict.sql",
            ),
        ):
            for ddl in engine_helper.schema_statements():
                instance.execute(ddl)
        instance.execute("SYSTEM STOP MERGES spans")
        yield instance
    finally:
        instance.stop()


@pytest.fixture(autouse=True)
def empty_tables(engine: engine_helper.DockerEngine) -> None:
    for table in ("spans", "traces", "trace_session_id_remap"):
        engine.execute(f"TRUNCATE TABLE {table}")


def _insert_roots(
    engine: engine_helper.DockerEngine,
    roots: list[tuple[str, datetime, str, int, int]],
) -> None:
    values = []
    for number, (session, when, status, version, deleted) in enumerate(roots, 100):
        trace = f"00000000-0000-4000-8000-{number:012}"
        values.append(
            f"('{PROJECT}','agent','cursor-test','{when.isoformat(sep=' ')}',"
            f"'{trace}','{trace}','','root','{session}','{status}',{deleted},{version})"
        )
    engine.execute(
        "INSERT INTO spans (project_id,observation_type,service_name,start_time,"
        "trace_id,id,parent_span_id,name,trace_session_id,status,is_deleted,_version) "
        "VALUES " + ",".join(values)
    )


def _seed(
    engine: engine_helper.DockerEngine,
    builder: SessionListQueryBuilderV2,
    *,
    before_time: datetime,
    before_id: str,
) -> list[dict[str, Any]]:
    query, params = builder.build_filter_seed_page(
        slice_start=START,
        slice_end=END,
        limit=200,
        before_start_time=before_time,
        before_id=before_id,
    )
    return _Analytics(engine).execute_ch_query(query, params).data


@pytest.mark.parametrize("operation", ["in", "not_in"])
@pytest.mark.parametrize("remapped", [False, True])
def test_checkpoint_reacquires_held_matching_session(
    engine: engine_helper.DockerEngine, operation: str, remapped: bool
) -> None:
    matching_status = "ERROR" if operation == "in" else "UNSET"
    other_status = "UNSET" if operation == "in" else "ERROR"
    physical_b = OLD_B if remapped else SESSIONS["B"]
    roots = [
        (SESSIONS["A"], START + timedelta(hours=22), matching_status, 1, 0),
        (physical_b, START + timedelta(hours=20), matching_status, 1, 0),
        (physical_b, START + timedelta(hours=12), matching_status, 1, 0),
        (SESSIONS["C"], START + timedelta(hours=18), matching_status, 1, 0),
    ]
    # Fill the normal 200-ID seed while leaving the public page unfilled.
    roots.extend(
        (
            f"00000000-0000-4000-8000-{number:012}",
            START + timedelta(hours=19),
            other_status,
            1,
            0,
        )
        for number in range(1000, 1197)
    )
    _insert_roots(engine, roots)
    if remapped:
        engine.execute(
            "INSERT INTO trace_session_id_remap (old_id,new_id) "
            f"VALUES ('{OLD_B}','{NEW_B}'), ('{SESSIONS['B']}','{NEW_B}')"
        )
    builder = _builder(operation)
    read_args = {
        "filters": _filters(operation),
        "key_field": "session_id",
        "page_number": 0,
        "page_size": 25,
        "deadline_ms": 20_000,
        "max_seed_attempts": 24,
        "max_candidates": 512,
        "classify_batch_size": 50,
        "include_incomplete_rows": True,
        "bounded_continuation": True,
        "carry_continuation_slice_width": True,
    }
    first = read_bounded_filter_page(
        builder=builder,
        analytics=_Analytics(engine),
        max_query_count=5,
        cursor_start_time=END,
        cursor_order_token="\U0010ffff",
        continuation_slice_start=START,
        continuation_slice_end=END,
        **read_args,
    )
    assert not first.complete
    assert first.error_code == "query_budget_exceeded"
    assert [str(row["session_id"]) for row in first.rows] == [
        SESSIONS["A"],
        SESSIONS["C"],
    ]
    order = _session_list_cursor_order_for_partial_page(
        rows=first.rows, bounded_page=first, cursor_state=None
    )
    second = read_bounded_filter_page(
        builder=_builder(operation),
        analytics=_Analytics(engine),
        max_query_count=50,
        cursor_start_time=order[0],
        cursor_order_token=order[1],
        continuation_slice_start=first.continuation_slice_start,
        continuation_slice_end=first.continuation_slice_end,
        continuation_before_start_time=first.continuation_before_start_time,
        continuation_before_id=first.continuation_before_id,
        **read_args,
    )
    assert second.complete
    assert not second.has_more
    published = [
        str(row["session_id"]) for page in (first, second) for row in page.rows
    ]
    query, params = builder.build_filter_match_query(list(SESSIONS.values())[:3])
    expected = _Analytics(engine).execute_ch_query(query, params).data
    assert published == [str(row["session_id"]) for row in expected]
    assert published == [SESSIONS["A"], SESSIONS["C"], SESSIONS["B"]]


@pytest.mark.parametrize("sampled", [False, True])
def test_seed_reaches_older_root_below_checkpoint(
    engine: engine_helper.DockerEngine, sampled: bool
) -> None:
    _insert_roots(
        engine,
        [
            (SESSIONS["B"], START + timedelta(hours=20), "ERROR", 1, 0),
            (SESSIONS["B"], START + timedelta(hours=12), "ERROR", 1, 0),
        ],
    )
    rows = _seed(
        engine,
        _builder(sampled=sampled),
        before_time=START + timedelta(hours=18),
        before_id=SESSIONS["C"],
    )
    assert [(str(row["session_id"]), row["start_time"]) for row in rows] == [
        (SESSIONS["B"], START + timedelta(hours=12)),
    ]


def test_sampled_remapped_keyset_compares_canonical_id_at_boundary(
    engine: engine_helper.DockerEngine,
) -> None:
    instant = START + timedelta(hours=18, microseconds=123456)
    _insert_roots(
        engine,
        [
            (OLD_B, instant + timedelta(hours=2), "ERROR", 1, 0),
            (OLD_B, instant, "ERROR", 1, 0),
        ],
    )
    engine.execute(
        "INSERT INTO trace_session_id_remap (old_id,new_id) "
        f"VALUES ('{OLD_B}','{NEW_B}'), ('{SESSIONS['B']}','{NEW_B}')"
    )
    rows = _seed(
        engine, _builder(sampled=True), before_time=instant, before_id=SESSIONS["C"]
    )
    assert [(str(row["session_id"]), row["start_time"]) for row in rows] == [
        (SESSIONS["B"], instant),
    ]


def test_keyset_preserves_microseconds_and_descends_through_ties(
    engine: engine_helper.DockerEngine,
) -> None:
    instant = START + timedelta(hours=18, microseconds=123456)
    _insert_roots(
        engine,
        [
            (SESSIONS["A"], instant, "ERROR", 1, 0),
            (SESSIONS["B"], instant + timedelta(hours=2), "ERROR", 1, 0),
            (SESSIONS["B"], instant - timedelta(microseconds=1), "ERROR", 1, 0),
            (SESSIONS["C"], instant, "ERROR", 1, 0),
            (SESSIONS["D"], instant, "ERROR", 1, 0),
        ],
    )
    builder = _builder()
    rows = _seed(engine, builder, before_time=instant, before_id=SESSIONS["C"])
    assert [(str(row["session_id"]), row["start_time"]) for row in rows] == [
        (SESSIONS["A"], instant),
        (SESSIONS["B"], instant - timedelta(microseconds=1)),
    ]
    boundary = rows[-1]
    assert (
        _seed(
            engine,
            builder,
            before_time=boundary["start_time"],
            before_id=str(boundary["session_id"]),
        )
        == []
    )


@pytest.mark.parametrize("deleted", [0, 1])
def test_reacquired_seed_still_requires_latest_live_error(
    engine: engine_helper.DockerEngine, deleted: int
) -> None:
    instant = START + timedelta(hours=12)
    _insert_roots(
        engine,
        [
            (SESSIONS["B"], START + timedelta(hours=20), "UNSET", 1, 0),
            (SESSIONS["B"], instant, "ERROR", 1, 0),
        ],
    )
    # Replace the same physical root, rather than adding a second identity.
    engine.execute(
        "INSERT INTO spans (project_id,observation_type,service_name,start_time,"
        "trace_id,id,parent_span_id,name,trace_session_id,status,is_deleted,_version) "
        f"VALUES ('{PROJECT}','agent','cursor-test','{instant.isoformat(sep=' ')}',"
        "'00000000-0000-4000-8000-000000000101',"
        "'00000000-0000-4000-8000-000000000101','','root',"
        f"'{SESSIONS['B']}','UNSET',{deleted},2)"
    )
    builder = _builder()
    rows = _seed(
        engine,
        builder,
        before_time=START + timedelta(hours=18),
        before_id=SESSIONS["C"],
    )
    assert [str(row["session_id"]) for row in rows] == [SESSIONS["B"]]
    query, params = builder.build_filter_match_query([SESSIONS["B"]])
    assert _Analytics(engine).execute_ch_query(query, params).data == []
