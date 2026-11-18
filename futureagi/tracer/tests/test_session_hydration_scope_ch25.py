"""Bounded page hydration must reuse root admission, not inline it per member use."""

import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.schema import SPANS_TABLE, _to_single_node_engine
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (  # noqa: F401
    _REMAP_DDL,
    _SPANS_DDL,
    _drop_legacy_ch_spans_mvs,
    _ensure_test_score_tenant_column,
)

pytestmark = pytest.mark.integration
START = datetime(2026, 10, 20, 12, tzinfo=UTC)
PAGE_SIZE = 200
PROJECT = UUID(int=3689501)
ROOTLESS_PROJECT = UUID(int=3689502)


def _message(session_index, trace_index):
    return f"session-{session_index}-trace-{trace_index}:" + "x" * 2048


@pytest.fixture(params=["v1", "v2"])
def hydration_page(request):
    with _ch_test_owned_database("test_session_hydration_scope_") as database:
        with _ch_test_native_client(database=database) as client:
            client.execute(
                _to_single_node_engine(SPANS_TABLE)
                if request.param == "v1"
                else _SPANS_DDL
            )
            client.execute(_REMAP_DDL)
            client.execute("SYSTEM STOP MERGES spans")
            ids = [UUID(int=1000 + index) for index in range(PAGE_SIZE)]
            aliases = [UUID(int=2000 + index) for index in range(PAGE_SIZE)]
            client.execute(
                "INSERT INTO trace_session_id_remap (old_id, new_id) VALUES",
                list(zip(ids, aliases, strict=True)),
            )
            rows = []
            for index, alias in enumerate(aliases):
                for trace_index in range(2):
                    start = START + timedelta(seconds=index * 2 + trace_index)
                    root = {
                        "project_id": PROJECT,
                        "trace_id": str(UUID(int=10000 + index * 2 + trace_index)),
                        "trace_session_id": alias,
                        "id": "root",
                        "parent_span_id": "",
                        "start_time": start,
                        "end_time": start + timedelta(seconds=1),
                        "name": "pydantic_ai.agent.run",
                        "observation_type": "agent",
                        "prompt_tokens": 42,
                        "completion_tokens": 28,
                        "total_tokens": 70,
                        "cost": 0.875,
                        "attrs_string": {"marker": str(index)},
                        "input": "",
                        "_version": 2,
                        "is_deleted": 0,
                    }
                    rows.append(root)
                    for child in range(7):
                        call = {
                            **root,
                            "id": f"call-{child}",
                            "parent_span_id": "root",
                            "name": "request",
                            "observation_type": "llm",
                            "prompt_tokens": 6,
                            "completion_tokens": 4,
                            "total_tokens": 10,
                            "cost": 0.125,
                            "attrs_string": {},
                            "input": _message(index, trace_index),
                        }
                        rows.append(call)
                        if child == 0:
                            rows.append(
                                {**call, "_version": 1, "cost": 100.0, "input": "stale"}
                            )
                    # An allowed project with the SAME session/trace IDs, but no
                    # root, must not contribute usage or unknown coverage.
                    rows.append(
                        {
                            **call,
                            "project_id": ROOTLESS_PROJECT,
                            "cost": 100.0,
                            "attrs_string": {"fi.cost.kind": "unknown"},
                        }
                    )
            # Same project and window, but outside the finite requested page.
            # These rows must not enlarge the member relation or message keys.
            rows.extend(
                {
                    **rows[0],
                    "trace_id": str(UUID(int=100000 + index)),
                    "trace_session_id": UUID(int=100000 + index),
                    "name": "unrelated",
                    "cost": 100.0,
                }
                for index in range(20000)
            )
            columns = list(rows[0])
            if request.param == "v1":
                for row in rows:
                    row["created_at"] = START
                    row["span_attr_str"] = row.pop("attrs_string")
                    row["_peerdb_version"] = row.pop("_version")
                    row["_peerdb_is_deleted"] = row.pop("is_deleted")
                columns = list(rows[0])
            client.execute(
                f"INSERT INTO spans ({', '.join(columns)}) VALUES",
                [tuple(row[column] for column in columns) for row in rows],
                types_check=True,
            )
            builder_cls = (
                SessionListQueryBuilder
                if request.param == "v1"
                else SessionListQueryBuilderV2
            )
            builder = builder_cls(
                project_ids=[str(PROJECT), str(ROOTLESS_PROJECT)],
                filters=[
                    {
                        "column_id": "created_at",
                        "filter_config": {
                            "filter_type": "datetime",
                            "filter_op": "between",
                            "filter_value": [
                                START.isoformat(),
                                (START + timedelta(days=1)).isoformat(),
                            ],
                        },
                    }
                ],
                page_number=0,
                page_size=PAGE_SIZE,
            )
            try:
                yield client, builder, ids
            finally:
                client.execute("SYSTEM START MERGES spans")


def test_max_page_reuses_admitted_project_sessions(hydration_page, record_property):
    client, builder, ids = hydration_page
    sql, params = builder.build_page_hydration_query([str(value) for value in ids])
    measurements = []
    # Three predetermined measurements, not retries: every returned row and
    # every duration must pass. The first execution uses a newly seeded DB.
    for ordinal in range(3):
        query_id = f"session-hydration-scope-{uuid4()}"
        started = time.monotonic()
        raw, columns = client.execute(
            sql,
            params,
            query_id=query_id,
            with_column_types=True,
            settings={
                "max_memory_usage": 512 * 1024**2,
                "max_bytes_to_read": 128 * 1024**2,
            },
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        measurements.append(
            {"ordinal": ordinal, "query_id": query_id, "ms": elapsed_ms}
        )
        records = {
            str(row[0]): dict(zip([name for name, _type in columns], row, strict=True))
            for row in raw
        }
        assert len(raw) == len(records) == PAGE_SIZE
        assert set(records) == {str(value) for value in ids}
        for index, session in enumerate(ids):
            row = records[str(session)]
            assert row["total_cost"] == 1.75
            assert row["total_tokens"] == 140
            assert row["traces_count"] == 2
            assert row["first_message"] == _message(index, 0)
            assert row["last_message"] == _message(index, 1)
        attributes = builder.expand_page_attribute_rows(list(records.values()))
        assert len(attributes) == PAGE_SIZE * 2
        attrs_column = builder._COST_ATTRIBUTE_MAP
        for row in attributes:
            index = ids.index(UUID(str(row["session_id"])))
            assert row[attrs_column] == {"marker": str(index)}
    record_property("hydration_measurements", json.dumps(measurements))
    assert max(sample["ms"] for sample in measurements) < 2000
