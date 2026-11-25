"""Real CH25 proofs for bounded voice paging and trace-detail replay."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from django.test import override_settings

from conftest import _ch_test_native_client
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse import eval_logger_table as eval_logger_table_config
from tracer.services.clickhouse.query_builders.voice_call_list import VAPI_PHONE_NUMBERS
from tracer.services.clickhouse.v2 import (
    trace_detail_reads as trace_detail_reads_module,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailReadBuilder,
    TraceDetailReadUnavailable,
    read_span_detail,
    read_trace_detail,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ch_client():
    with _ch_test_native_client() as client:
        yield client


@contextmanager
def _detail_tables(ch_client, monkeypatch, *, legacy_evals=False):
    suffix = uuid.uuid4().hex[:8]
    spans = f"_test_voice_detail_spans_{suffix}"
    # The builder picks the version and tombstone columns by the ``_v2``
    # suffix, so the legacy-shaped table must not end with it.
    evals = (
        f"_test_voice_detail_evals_{suffix}_legacy"
        if legacy_evals
        else f"_test_voice_detail_evals_{suffix}_v2"
    )
    scores = f"_test_voice_detail_scores_{suffix}"
    live_predicate = (
        "_peerdb_is_deleted = 0 AND deleted = 0" if legacy_evals else "is_deleted = 0"
    )
    monkeypatch.setattr(
        trace_detail_reads_module,
        "eval_logger_source",
        lambda *args, **kwargs: (evals, live_predicate),
    )

    def test_eval_source(alias="", include_cdc_tombstone_guard=False):
        del include_cdc_tombstone_guard
        prefix = f"{alias}." if alias else ""
        return evals, " AND ".join(
            f"{prefix}{term}" for term in live_predicate.split(" AND ")
        )

    monkeypatch.setattr(
        VoiceCallListQueryBuilderV2,
        "_EVAL_LOGGER_SOURCE",
        staticmethod(test_eval_source),
    )
    monkeypatch.setattr(
        eval_logger_table_config,
        "SUPPORTED_EVAL_LOGGER_TABLES",
        eval_logger_table_config.SUPPORTED_EVAL_LOGGER_TABLES | {evals},
    )
    ch_client.execute(
        f"""
        CREATE TABLE {spans} (
            project_id UUID,
            observation_type String,
            service_name String,
            start_time DateTime64(6, 'UTC'),
            trace_id String,
            id String,
            parent_span_id String,
            name String,
            trace_name String DEFAULT '',
            end_time Nullable(DateTime64(6, 'UTC')),
            project_version_id Nullable(UUID),
            trace_session_id Nullable(UUID),
            custom_eval_config_id Nullable(UUID),
            status String,
            status_message String,
            model String,
            provider String,
            latency_ms Int32,
            prompt_tokens Int32,
            completion_tokens Int32,
            total_tokens Int32,
            cost Float64,
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            attributes_extra String,
            metadata String,
            input String,
            output String,
            tags String,
            span_events String,
            created_at DateTime64(6, 'UTC'),
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = MergeTree
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id, _version)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {evals} (
            id UUID,
            trace_id Nullable(UUID),
            observation_span_id Nullable(String),
            custom_eval_config_id UUID,
            output_bool Nullable(UInt8),
            output_float Nullable(Float64),
            output_str Nullable(String),
            output_str_list String DEFAULT '[]',
            eval_explanation Nullable(String),
            error UInt8 DEFAULT 0,
            error_message Nullable(String),
            status LowCardinality(String) DEFAULT 'completed',
            skipped_reason Nullable(String),
            target_type LowCardinality(String) DEFAULT 'span',
            created_at DateTime64(6),
            deleted UInt8 DEFAULT 0,
            _peerdb_is_deleted UInt8 DEFAULT 0,
            _peerdb_version Int64
        ) ENGINE = MergeTree ORDER BY (id, _peerdb_version)
        """
        if legacy_evals
        else f"""
        CREATE TABLE {evals} (
            id UUID,
            trace_id Nullable(UUID),
            observation_span_id Nullable(String),
            custom_eval_config_id UUID,
            output_bool Nullable(UInt8),
            output_float Nullable(Float64),
            output_str Nullable(String),
            output_str_list String DEFAULT '[]',
            eval_explanation Nullable(String),
            error UInt8,
            error_message Nullable(String),
            target_type LowCardinality(String) DEFAULT 'span',
            created_at DateTime64(3, 'UTC'),
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = MergeTree
        PARTITION BY toYYYYMM(created_at)
        ORDER BY (custom_eval_config_id, created_at, id)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {scores} (
            id UUID,
            tracer_project_id Nullable(UUID),
            trace_id Nullable(UUID),
            observation_span_id Nullable(String),
            label_id UUID,
            value String,
            deleted UInt8,
            created_at DateTime64(6),
            _peerdb_is_deleted UInt8,
            _peerdb_version Int64
        ) ENGINE = MergeTree
        PARTITION BY toYYYYMM(created_at)
        ORDER BY (label_id, created_at, id)
        """
    )
    monkeypatch.setattr(TraceDetailReadBuilder, "TABLE", spans)
    monkeypatch.setattr(TraceDetailReadBuilder, "SCORE_TABLE", scores)
    try:
        yield SimpleNamespace(
            spans=spans, evals=evals, scores=scores, legacy_evals=legacy_evals
        )
    finally:
        ch_client.execute(f"DROP TABLE {spans}")
        ch_client.execute(f"DROP TABLE {evals}")
        ch_client.execute(f"DROP TABLE {scores}")


@pytest.fixture()
def detail_tables(ch_client, monkeypatch):
    with _detail_tables(ch_client, monkeypatch) as tables:
        yield tables


@pytest.fixture(params=["v2", "legacy"])
def shaped_detail_tables(request, ch_client, monkeypatch):
    """``detail_tables`` with a native v2 or a legacy PeerDB-shaped eval table.

    Both are plain MergeTree, so every physical version stays readable, as on
    production before a merge.
    """

    with _detail_tables(
        ch_client, monkeypatch, legacy_evals=request.param == "legacy"
    ) as tables:
        yield tables


class _Analytics:
    def __init__(self, client):
        self.client = client

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        # The integration table models production's typed-JSON metadata column
        # as its wire-format String representation. ``attributes_extra`` is
        # already a String in production (schema 013), so its query must run
        # unchanged to catch accidental double encoding.
        query = query.replace("toJSONString(latest_metadata)", "latest_metadata")
        rows, columns = self.client.execute(
            query,
            params,
            with_column_types=True,
            settings={
                **settings,
                "max_execution_time": max(timeout_ms / 1000, 0.1),
            },
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows]
        )


def _span_row(
    *,
    project_id,
    trace_id,
    span_id,
    started_at,
    version=1,
    deleted=0,
    parent="",
    observation_type="conversation",
    final_status="Rejected",
    input_value="",
    project_version_id=None,
    raw_log=None,
):
    return (
        project_id,
        observation_type,
        "svc",
        started_at,
        trace_id,
        span_id,
        parent,
        span_id,
        started_at + timedelta(seconds=1),
        project_version_id,
        None,
        None,
        "OK",
        "",
        "",
        "vapi",
        1000,
        1,
        2,
        3,
        0.01,
        {
            "final_status": final_status,
            **({"raw_log": raw_log} if raw_log is not None else {}),
        },
        {},
        {},
        "{}",
        "{}",
        input_value,
        "",
        "[]",
        "[]",
        started_at,
        deleted,
        version,
    )


_SPAN_COLUMNS = """
    (project_id, observation_type, service_name, start_time, trace_id, id,
     parent_span_id, name, end_time, project_version_id, trace_session_id,
     custom_eval_config_id, status, status_message, model, provider, latency_ms,
     prompt_tokens, completion_tokens, total_tokens, cost, attrs_string,
     attrs_number, attrs_bool, attributes_extra, metadata, input, output, tags,
     span_events, created_at, is_deleted, _version)
"""


def _time_filter(start, end):
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _status_filter():
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "Rejected",
        },
    }


def test_voice_page_n_and_content_use_physical_latest_identity(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000001"
    other = "00000000-0000-4000-8000-000000000002"
    start = datetime(2026, 7, 30, 10, 0)
    a_time = start.replace(microsecond=123456)
    b_time = (start + timedelta(seconds=1)).replace(microsecond=654321)
    deleted_time = start + timedelta(seconds=2)
    rows = [
        _span_row(
            project_id=project,
            trace_id="trace-a",
            span_id="shared-root",
            started_at=a_time,
            input_value="a-old",
            raw_log='{"call":"a-old"}',
        ),
        _span_row(
            project_id=project,
            trace_id="trace-a",
            span_id="shared-root",
            started_at=a_time,
            version=2,
            input_value="a-latest",
            raw_log='{"call":"a-latest"}',
        ),
        _span_row(
            project_id=project,
            trace_id="trace-b",
            span_id="shared-root",
            started_at=b_time,
            input_value="b",
            raw_log='{"call":"b"}',
        ),
        _span_row(
            project_id=project,
            trace_id="trace-deleted",
            span_id="deleted-root",
            started_at=deleted_time,
        ),
        _span_row(
            project_id=project,
            trace_id="trace-deleted",
            span_id="deleted-root",
            started_at=deleted_time,
            version=2,
            deleted=1,
        ),
        _span_row(
            project_id=project,
            trace_id="trace-miss",
            span_id="miss-root",
            started_at=start + timedelta(seconds=3),
            final_status="Approved",
        ),
        _span_row(
            project_id=other,
            trace_id="foreign",
            span_id="shared-root",
            started_at=start + timedelta(seconds=4),
        ),
    ]
    ch_client.execute(f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES", rows)

    class LocalVoiceBuilder(VoiceCallListQueryBuilderV2):
        TABLE = detail_tables.spans

    builder = LocalVoiceBuilder(
        project_id=project,
        filters=[
            _time_filter(start - timedelta(minutes=1), start + timedelta(minutes=1)),
            _status_filter(),
        ],
        page_number=1,
        page_size=1,
    )
    page = read_bounded_filter_page(
        builder=builder,
        analytics=_Analytics(ch_client),
        filters=builder.filters,
        key_field="trace_id",
        page_number=1,
        page_size=1,
        deadline_ms=5000,
    )
    assert page.complete is True
    assert [row["trace_id"] for row in page.rows] == ["trace-a"]
    selected = page.rows[0]
    expected_rows = [
        selected,
        {
            "project_id": project,
            "trace_id": "trace-b",
            "root_span_id": "shared-root",
            "start_time": b_time,
            "_root_observation_type": "conversation",
            "_root_service_name": "svc",
            "_root_start_hour": start,
            "_root_version": 1,
        },
    ]
    root_identities = builder.content_root_identities_for_rows(expected_rows)
    epoch = datetime(1970, 1, 1)
    assert root_identities[0] == (
        project,
        "trace-a",
        "shared-root",
        (a_time - epoch) // timedelta(microseconds=1),
        "conversation",
        "svc",
        (start - epoch) // timedelta(microseconds=1),
        2,
    )
    query, params = builder.build_content_query(
        [identity[2] for identity in root_identities],
        root_identities=root_identities,
    )
    content = (
        _Analytics(ch_client)
        .execute_ch_query(
            query,
            params,
            timeout_ms=2000,
            settings={"max_threads": 1, "max_result_rows": 2},
        )
        .data
    )
    assert len(content) == 2
    assert builder.content_root_rows_match(expected_rows, content)
    assert {
        (row["trace_id"], row["span_id"], row["attrs_string"]["raw_log"])
        for row in content
    } == {
        ("trace-a", "shared-root", '{"call":"a-latest"}'),
        ("trace-b", "shared-root", '{"call":"b"}'),
    }


def test_trace_detail_replays_versions_tombstones_evals_and_annotations(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000011"
    foreign = "00000000-0000-4000-8000-000000000012"
    trace = "00000000-0000-4000-8000-000000000021"
    root_time = datetime(2026, 7, 30, 11, 0, 0, 123456)
    child_time = datetime(2026, 7, 30, 11, 0, 1, 654321)
    dead_time = datetime(2026, 7, 30, 11, 0, 2)
    recycled_old_time = datetime(2026, 7, 30, 11, 0, 3, 111111)
    recycled_live_time = datetime(2026, 7, 30, 11, 0, 4, 222222)
    project_version = "00000000-0000-4000-8000-000000000099"
    rows = [
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="root",
            started_at=root_time,
            observation_type="chain",
            input_value="old",
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="root",
            started_at=root_time,
            observation_type="chain",
            input_value="latest",
            version=2,
            project_version_id=project_version,
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="child",
            parent="root",
            started_at=child_time,
            observation_type="llm",
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="dead",
            started_at=dead_time,
            observation_type="tool",
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="dead",
            started_at=dead_time,
            observation_type="tool",
            version=2,
            deleted=1,
        ),
        _span_row(
            project_id=foreign,
            trace_id=trace,
            span_id="foreign",
            started_at=root_time,
            observation_type="chain",
        ),
        # A reused bare id is safe when the older physical identity's latest
        # state is a tombstone. Only the new physical identity may survive.
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="recycled",
            started_at=recycled_old_time,
            observation_type="tool",
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="recycled",
            started_at=recycled_old_time,
            observation_type="tool",
            version=2,
            deleted=1,
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="recycled",
            started_at=recycled_live_time,
            observation_type="tool",
            input_value="new-physical-row",
        ),
    ]
    ch_client.execute(f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES", rows)
    eval_id = str(uuid.uuid4())
    config_id = str(uuid.uuid4())
    ch_client.execute(
        f"""
        INSERT INTO {detail_tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_bool, output_float, output_str, eval_explanation, error,
             is_deleted, _version)
        VALUES
        """,
        [
            (eval_id, trace, "root", config_id, None, 0.2, None, "old", 0, 0, 1),
            (
                eval_id,
                trace,
                "root",
                config_id,
                None,
                0.9,
                None,
                "latest",
                0,
                0,
                2,
            ),
        ],
    )
    score_id = str(uuid.uuid4())
    label_id = str(uuid.uuid4())
    ch_client.execute(
        f"""
        INSERT INTO {detail_tables.scores}
            (id, tracer_project_id, trace_id, observation_span_id, label_id,
             value, deleted, _peerdb_is_deleted, _peerdb_version)
        VALUES
        """,
        [
            (score_id, project, trace, "child", label_id, '{"rating":1}', 0, 0, 1),
            (score_id, project, trace, "child", label_id, '{"rating":5}', 0, 0, 2),
        ],
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: [config_id],
        deadline_ms=5000,
    )
    assert [row["id"] for row in read.spans] == ["root", "child", "recycled"]
    assert read.spans[0]["input"] == "latest"
    assert str(read.spans[0]["project_version_id"]) == project_version
    assert read.spans[2]["input"] == "new-physical-row"
    assert read.evals[0]["output_float"] == 0.9
    assert read.annotations[0]["value"] == '{"rating":5}'
    assert read.query_count == 4


def test_trace_detail_eval_rows_are_scoped_by_selected_project_configs(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000071"
    trace = "00000000-0000-4000-8000-000000000072"
    own_config = "00000000-0000-4000-8000-000000000073"
    foreign_config = "00000000-0000-4000-8000-000000000074"
    started = datetime(2026, 7, 30, 15, 0)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="shared-span",
                started_at=started,
            )
        ],
    )
    ch_client.execute(
        f"""
        INSERT INTO {detail_tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_bool, output_float, output_str, eval_explanation, error,
             is_deleted, _version)
        VALUES
        """,
        [
            (
                str(uuid.uuid4()),
                trace,
                "shared-span",
                own_config,
                None,
                0.8,
                None,
                "own-project",
                0,
                0,
                1,
            ),
            (
                str(uuid.uuid4()),
                trace,
                "shared-span",
                foreign_config,
                None,
                0.1,
                None,
                "foreign-project-collision",
                0,
                0,
                1,
            ),
        ],
    )

    resolved_projects = []

    def resolve_project_configs(selected_project):
        resolved_projects.append(selected_project)
        return [own_config]

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project],
        trace_id=trace,
        eval_config_ids_resolver=resolve_project_configs,
        deadline_ms=5000,
    )

    assert resolved_projects == [project]
    assert read.eval_config_ids == (own_config,)
    assert [row["eval_config_id"] for row in read.evals] == [own_config]
    assert read.evals[0]["eval_explanation"] == "own-project"


def test_trace_detail_without_authorized_configs_skips_eval_read(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000081"
    trace = "00000000-0000-4000-8000-000000000082"
    started = datetime(2026, 7, 30, 16, 0)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="root",
                started_at=started,
            )
        ],
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: [],
        deadline_ms=5000,
    )

    assert read.eval_config_ids == ()
    assert read.evals == ()
    # identity + content + annotations; the eval query is not issued.
    assert read.query_count == 3


def _inline_with_bindings(query):
    """``query`` with every ``WITH %(param)s AS alias`` binding undone.

    Each ``IN alias`` names its parameter again: the statement as it was
    written before the lists were bound once.
    """

    head, select, body = query.partition("SELECT")
    bindings = re.findall(r"%\((\w+)\)s\s+AS\s+(\w+)", head)
    assert head.strip().startswith("WITH") and bindings, head
    for param, alias in bindings:
        body = re.sub(rf"\bIN\s+{alias}\b", f"IN %({param})s", body)
        assert alias not in body, alias
    return select + body


def _seed_config_scope_trace(ch_client, tables, *, project, trace, span_ids, evals):
    started = datetime(2026, 8, 1, 9, 0, 0)
    ch_client.execute(
        f"INSERT INTO {tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id=span_id,
                started_at=started + timedelta(microseconds=index),
            )
            for index, span_id in enumerate(span_ids)
        ],
    )
    created = datetime(2026, 8, 1, 9, 30)
    if tables.legacy_evals:
        columns = "_peerdb_is_deleted, _peerdb_version"
        extra = ()
    else:
        columns = "error, is_deleted, _version"
        extra = (0,)
    ch_client.execute(
        f"""
        INSERT INTO {tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_str, eval_explanation, created_at, {columns})
        VALUES
        """,
        [
            (
                eval_id,
                trace,
                span_id,
                config_id,
                payload,
                payload,
                created,
                *extra,
                deleted,
                version,
            )
            for eval_id, span_id, config_id, payload, deleted, version in evals
        ],
    )


@pytest.mark.parametrize("config_count", [1, 2])
def test_trace_detail_eval_config_alias_scopes_small_config_sets(
    ch_client, shaped_detail_tables, config_count
):
    # One config renders as ('x'), which ClickHouse reads as a scalar String.
    project = "00000000-0000-4000-8000-0000000000c1"
    trace = "00000000-0000-4000-8000-0000000000c2"
    configs = [str(uuid.UUID(int=0xC100 + index)) for index in range(config_count)]
    foreign_config = str(uuid.UUID(int=0xC1FF))
    served_ids = [str(uuid.uuid4()) for _ in configs]
    _seed_config_scope_trace(
        ch_client,
        shaped_detail_tables,
        project=project,
        trace=trace,
        span_ids=["root"],
        evals=[
            *(
                (eval_id, "root", config_id, f"own-{index}", 0, 1)
                for index, (eval_id, config_id) in enumerate(
                    zip(served_ids, configs, strict=True)
                )
            ),
            (str(uuid.uuid4()), "root", foreign_config, "foreign", 0, 1),
        ],
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: configs,
        include_annotations=False,
        deadline_ms=5000,
    )

    assert {
        row["log_id"]: (row["eval_config_id"], row["output_str"]) for row in read.evals
    } == {
        eval_id: (config_id, f"own-{index}")
        for index, (eval_id, config_id) in enumerate(
            zip(served_ids, configs, strict=True)
        )
    }
    assert read.eval_unavailable is False


def test_trace_detail_eval_read_fits_max_query_size_at_the_config_ceiling(
    ch_client, shaped_detail_tables
):
    from clickhouse_driver.errors import ServerException

    from tracer.services.clickhouse.read_budget import is_clickhouse_query_size_error

    tables = shaped_detail_tables
    project = "00000000-0000-4000-8000-0000000000d1"
    trace = "00000000-0000-4000-8000-0000000000d2"
    span_ids = [str(uuid.UUID(int=0xD0000 + index)) for index in range(1000)]
    configs = [
        str(uuid.UUID(int=0xE0000 + index))
        for index in range(trace_detail_reads_module.MAX_TRACE_DETAIL_EVAL_CONFIGS)
    ]
    first, deleted, versioned, last = (str(uuid.uuid4()) for _ in range(4))
    _seed_config_scope_trace(
        ch_client,
        tables,
        project=project,
        trace=trace,
        span_ids=span_ids,
        evals=[
            (first, span_ids[0], configs[0], "first-config", 0, 1),
            (last, span_ids[-1], configs[-1], "last-config", 0, 1),
            (str(uuid.uuid4()), span_ids[0], str(uuid.uuid4()), "foreign", 0, 1),
            (deleted, span_ids[1], configs[1], "deleted-v1", 0, 1),
            (deleted, span_ids[1], configs[1], "deleted-v2", 1, 2),
            (versioned, span_ids[2], configs[2], "versioned-v1", 0, 1),
            (versioned, span_ids[2], configs[2], "versioned-v2", 0, 2),
        ],
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: configs,
        include_annotations=False,
        deadline_ms=30_000,
    )

    assert len(read.spans) == 1000
    assert read.eval_unavailable is False
    assert [
        (row["log_id"], row["span_id"], row["eval_config_id"], row["output_str"])
        for row in read.evals
    ] == sorted(
        [
            (first, span_ids[0], configs[0], "first-config"),
            (versioned, span_ids[2], configs[2], "versioned-v2"),
            (last, span_ids[-1], configs[-1], "last-config"),
        ],
        key=lambda row: (row[1], row[2]),
    )

    # Negative control: the same statement with each bound list written out
    # at every use, as before the binding, is over this server's limit.
    limit = int(
        ch_client.execute(
            "SELECT value FROM system.settings WHERE name = 'max_query_size'"
        )[0][0]
    )
    query, params = TraceDetailReadBuilder(
        project_ids=[project], trace_id=trace
    ).build_eval_query(project_id=project, span_ids=span_ids, eval_config_ids=configs)
    context = ch_client.connection.context
    rendered = ch_client.substitute_params(query, params, context)
    rendered_inlined = ch_client.substitute_params(
        _inline_with_bindings(query), params, context
    )
    assert len(rendered.encode()) < limit < len(rendered_inlined.encode())
    with pytest.raises(ServerException) as caught:
        ch_client.execute(rendered_inlined)
    assert is_clickhouse_query_size_error(caught.value), caught.value


# Each case lists its versions oldest first as (span, payload) or
# (span, payload, deleted). Every version has its own payload, so the served
# row shows which version won. Spans: "root" and "child" are live in the
# selected project, "dead" is tombstoned, "foreign" is live only in another
# project's copy of the trace and "ghost" is not in ClickHouse at all.
_STALE_EVAL_CASES = {
    "E1-null": [("root", "E1-v1"), (None, "E1-v2")],
    "E2-dead": [("root", "E2-v1"), ("dead", "E2-v2")],
    "E3-foreign": [("root", "E3-v1"), ("foreign", "E3-v2")],
    "E4-ghost": [("root", "E4-v1"), ("ghost", "E4-v2")],
    "E5-null-tombstone": [("root", "E5-v1"), (None, "E5-v2", 1)],
    "E6-child-to-root": [("child", "E6-v1"), ("root", "E6-v2")],
    "E7-null-to-root": [(None, "E7-v1"), ("root", "E7-v2")],
    "E8-root-null-child": [("root", "E8-v1"), (None, "E8-v2"), ("child", "E8-v3")],
    "E9-single": [("root", "E9-v1")],
    "E10-tombstone-on-span": [("root", "E10-v1"), ("root", "E10-v2", 1)],
    "E17-five-versions": [
        ("root", "E17-v1"),
        (None, "E17-v2"),
        ("child", "E17-v3"),
        ("dead", "E17-v4"),
        ("root", "E17-v5"),
    ],
}
_STALE_EVAL_CREATED = datetime(2026, 7, 31, 9, 30)
# The trace-detail eval read before the latest-first change, reduced to the
# columns these tests compare: the span check runs in PREWHERE, before
# GROUP BY id picks each record's latest version.
_PRIOR_EVAL_SQL = """
SELECT
    toString(grouped_eval_id) AS log_id,
    latest_span_id AS span_id,
    toString(latest_eval_config_id) AS eval_config_id,
    latest_output_str AS output_str
FROM (
    SELECT
        id AS grouped_eval_id,
        argMax(observation_span_id, {version}) AS latest_span_id,
        argMax(custom_eval_config_id, {version}) AS latest_eval_config_id,
        argMax(tuple(output_str), {version}).1 AS latest_output_str,
        {deleted} AS latest_is_deleted
    FROM {table}
    PREWHERE trace_id = %(detail_eval_trace_id)s
      AND observation_span_id IN %(detail_eval_span_ids)s
      AND toString(custom_eval_config_id) IN %(detail_eval_config_ids)s
    GROUP BY id
) AS latest_trace_evals
WHERE latest_is_deleted = 0
  AND toString(latest_eval_config_id) IN %(detail_eval_config_ids)s
"""


def _eval_columns(tables):
    """``(version column, deleted expression over physical rows)``."""

    if tables.legacy_evals:
        return "_peerdb_version", "greatest(_peerdb_is_deleted, deleted)"
    return "_version", "is_deleted"


def _seed_stale_attachment_trace(ch_client, tables, *, project, foreign, trace):
    started = datetime(2026, 7, 31, 9, 0, 0)
    rows = [
        _span_row(
            project_id=project, trace_id=trace, span_id="root", started_at=started
        ),
        # The newest write makes the selected project's copy win.
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="root",
            started_at=started,
            version=2,
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="child",
            parent="root",
            started_at=started + timedelta(seconds=1),
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="dead",
            started_at=started + timedelta(seconds=2),
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="dead",
            started_at=started + timedelta(seconds=2),
            version=2,
            deleted=1,
        ),
        _span_row(
            project_id=foreign,
            trace_id=trace,
            span_id="foreign",
            started_at=started,
        ),
    ]
    ch_client.execute(f"INSERT INTO {tables.spans} {_SPAN_COLUMNS} VALUES", rows)


def _insert_evals(ch_client, tables, physical):
    """Insert physical eval versions, given as dicts, into either table shape."""

    version, _deleted = _eval_columns(tables)
    deleted_column = "_peerdb_is_deleted" if tables.legacy_evals else "is_deleted"
    ch_client.execute(
        f"""
        INSERT INTO {tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_str, eval_explanation, created_at, {deleted_column},
             {version})
        VALUES
        """,
        [
            (
                row["id"],
                row["trace_id"],
                row["span"],
                row["config"],
                row["payload"],
                row["payload"],
                row.get("created", _STALE_EVAL_CREATED),
                row["deleted"],
                row["version"],
            )
            for row in physical
        ],
    )


def _live_span_oracle(ch_client, tables, *, project, trace):
    """Live span ids of one project's copy, from every physical span row."""

    latest = {}
    for row in ch_client.execute(
        f"""
        SELECT toString(project_id), trace_id, id, start_time, _version, is_deleted
        FROM {tables.spans}
        """
    ):
        key = row[:4]
        if key not in latest or row[4] > latest[key][4]:
            latest[key] = row
    return {
        span_id
        for (row_project, row_trace, span_id, _start), row in latest.items()
        if row_project == project and row_trace == trace and not row[5]
    }


def _latest_by_id(rows):
    """The newest physical row of each id; a version tie would be ambiguous."""

    versions = Counter((row["id"], row["version"]) for row in rows)
    assert all(count == 1 for count in versions.values()), versions
    latest = {}
    for row in rows:
        if row["id"] not in latest or row["version"] > latest[row["id"]]["version"]:
            latest[row["id"]] = row
    return latest


def _eval_oracle(ch_client, tables, *, trace, configs, live_spans):
    """``{id: (span, config, payload)}`` from every physical eval row.

    The newest version of each id is served only when it is live, on the
    trace, under an authorized config and on one of the live spans.
    """

    version, deleted = _eval_columns(tables)
    stored = [
        dict(
            zip(
                ("id", "trace_id", "span", "config", "payload", "deleted", "version"),
                row,
                strict=True,
            )
        )
        for row in ch_client.execute(
            f"""
            SELECT toString(id), toString(trace_id), observation_span_id,
                   toString(custom_eval_config_id), output_str, {deleted},
                   {version}
            FROM {tables.evals}
            """
        )
    ]
    return {
        row["id"]: (row["span"], row["config"], row["payload"])
        for row in _latest_by_id(stored).values()
        if row["trace_id"] == trace
        and row["config"] in configs
        and not row["deleted"]
        and row["span"] in live_spans
    }


def _prior_eval_rows(ch_client, tables, *, trace, spans, configs):
    """``{id: (span, config, payload)}`` served by the prior statement."""

    version, _deleted = _eval_columns(tables)
    deleted = (
        f"greatest(argMax(_peerdb_is_deleted, {version}), "
        f"coalesce(argMax(deleted, {version}), 0))"
        if tables.legacy_evals
        else f"argMax(is_deleted, {version})"
    )
    rows = ch_client.execute(
        _PRIOR_EVAL_SQL.format(table=tables.evals, version=version, deleted=deleted),
        {
            "detail_eval_trace_id": trace,
            "detail_eval_span_ids": tuple(spans),
            "detail_eval_config_ids": tuple(configs),
        },
    )
    return {log_id: (span, config, payload) for log_id, span, config, payload in rows}


def _served_evals(read):
    served = {
        row["log_id"]: (row["span_id"], row["eval_config_id"], row["output_str"])
        for row in read.evals
    }
    assert len(served) == len(read.evals)
    return served


def test_trace_detail_serves_eval_only_when_latest_version_is_on_a_live_span(
    ch_client, shaped_detail_tables
):
    tables = shaped_detail_tables
    project = "00000000-0000-4000-8000-0000000000a1"
    foreign = "00000000-0000-4000-8000-0000000000a2"
    trace = "00000000-0000-4000-8000-0000000000a3"
    config = "00000000-0000-4000-8000-0000000000a4"
    other_config = "00000000-0000-4000-8000-0000000000a5"
    unauthorized = "00000000-0000-4000-8000-0000000000a6"
    _seed_stale_attachment_trace(
        ch_client, tables, project=project, foreign=foreign, trace=trace
    )
    cases = {
        name: [(span, payload, *rest, config) for span, payload, *rest in versions]
        for name, versions in _STALE_EVAL_CASES.items()
    }
    # A config change between two authorized configs serves the latest one.
    cases["E11-config-move"] = [
        ("root", "E11-v1", 0, config),
        ("root", "E11-v2", 0, other_config),
    ]
    cases["E12-unauthorized"] = [("root", "E12-v1", 0, unauthorized)]
    ids = {name: str(uuid.uuid4()) for name in cases}
    _insert_evals(
        ch_client,
        tables,
        [
            {
                "id": ids[name],
                "trace_id": trace,
                "span": span,
                "config": rest[-1],
                "payload": payload,
                "deleted": rest[0] if len(rest) == 2 else 0,
                "version": version,
            }
            for name, versions in cases.items()
            for version, (span, payload, *rest) in enumerate(versions, start=1)
        ],
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project, foreign],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: [config, other_config],
        include_annotations=False,
        deadline_ms=5000,
    )

    assert read.project_id == project
    live_spans = _live_span_oracle(ch_client, tables, project=project, trace=trace)
    assert live_spans == {"root", "child"}
    oracle = _eval_oracle(
        ch_client,
        tables,
        trace=trace,
        configs={config, other_config},
        live_spans=live_spans,
    )
    assert oracle == {
        ids["E6-child-to-root"]: ("root", config, "E6-v2"),
        ids["E7-null-to-root"]: ("root", config, "E7-v2"),
        ids["E8-root-null-child"]: ("child", config, "E8-v3"),
        ids["E9-single"]: ("root", config, "E9-v1"),
        ids["E11-config-move"]: ("root", other_config, "E11-v2"),
        ids["E17-five-versions"]: ("root", config, "E17-v5"),
    }
    assert _served_evals(read) == oracle
    assert all(row["eval_explanation"] == row["output_str"] for row in read.evals)
    assert read.eval_unavailable is False
    # identity + content + evals; annotations were not requested.
    assert read.query_count == 3
    # The prior statement serves each moved record's older version, so these
    # cases exercise the order this read fixes.
    prior = _prior_eval_rows(
        ch_client,
        tables,
        trace=trace,
        spans=sorted(live_spans),
        configs=[config, other_config],
    )
    assert {
        name: prior.get(ids[name])
        for name in ("E1-null", "E2-dead", "E3-foreign", "E4-ghost")
    } == {
        "E1-null": ("root", config, "E1-v1"),
        "E2-dead": ("root", config, "E2-v1"),
        "E3-foreign": ("root", config, "E3-v1"),
        "E4-ghost": ("root", config, "E4-v1"),
    }


def test_trace_detail_eval_residual_cases_against_the_prior_statement(
    ch_client, shaped_detail_tables
):
    tables = shaped_detail_tables
    project = "00000000-0000-4000-8000-0000000000b4"
    foreign = "00000000-0000-4000-8000-0000000000b5"
    trace = "00000000-0000-4000-8000-0000000000b6"
    config = "00000000-0000-4000-8000-0000000000b7"
    other_config = "00000000-0000-4000-8000-0000000000b8"
    later = _STALE_EVAL_CREATED + timedelta(days=40)
    _seed_stale_attachment_trace(
        ch_client, tables, project=project, foreign=foreign, trace=trace
    )
    names = ("E14-config-and-span", "E15-created-and-span", "E16-id-only-tombstone")
    ids = {name: str(uuid.uuid4()) for name in names}

    def version(name, number, span, config_id, created=_STALE_EVAL_CREATED):
        return {
            "id": ids[name],
            "trace_id": trace,
            "span": span,
            "config": config_id,
            "payload": f"{name}-v{number}",
            "deleted": 0,
            "version": number,
            "created": created,
        }

    _insert_evals(
        ch_client,
        tables,
        [
            # A sort-key column changes in the same version as the span
            # leaves the live spans.
            version("E14-config-and-span", 1, "root", config),
            version("E14-config-and-span", 2, None, other_config),
            version("E15-created-and-span", 1, "root", config),
            version("E15-created-and-span", 2, None, config, later),
            version("E16-id-only-tombstone", 1, "root", config),
        ],
    )
    # A hard delete arrives as the id, the tombstone flag and a newer version;
    # every other column keeps its default, so it has no trace.
    version_column, _deleted = _eval_columns(tables)
    deleted_column = "_peerdb_is_deleted" if tables.legacy_evals else "is_deleted"
    ch_client.execute(
        f"INSERT INTO {tables.evals} (id, {deleted_column}, {version_column}) VALUES",
        [(ids["E16-id-only-tombstone"], 1, 2)],
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project, foreign],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: [config, other_config],
        include_annotations=False,
        deadline_ms=5000,
    )

    served = _served_evals(read)
    live_spans = _live_span_oracle(ch_client, tables, project=project, trace=trace)
    assert (
        _eval_oracle(
            ch_client,
            tables,
            trace=trace,
            configs={config, other_config},
            live_spans=live_spans,
        )
        == {}
    )
    prior = _prior_eval_rows(
        ch_client,
        tables,
        trace=trace,
        spans=sorted(live_spans),
        configs=[config, other_config],
    )
    assert prior == {ids[name]: ("root", config, f"{name}-v1") for name in names}
    # An id-only tombstone has no trace, so no trace-scoped read sees it.
    assert served[ids["E16-id-only-tombstone"]] == prior[ids["E16-id-only-tombstone"]]
    if tables.legacy_evals:
        # tracer_eval_logger reads every version of the trace in one scan, so
        # a key change that also leaves the live spans is hidden.
        assert set(served) == {ids["E16-id-only-tombstone"]}
    else:
        # tracer_eval_logger_v2 reads every version of the candidates' sort
        # keys. A newer version under another key is not one of them, so
        # these records stay as the prior statement served them.
        assert served == prior


def test_trace_detail_eval_read_past_the_row_limit_returns_only_live_rows(
    ch_client, shaped_detail_tables
):
    tables = shaped_detail_tables
    project = "00000000-0000-4000-8000-0000000000e1"
    foreign = "00000000-0000-4000-8000-0000000000e2"
    trace = "00000000-0000-4000-8000-0000000000e3"
    config = "00000000-0000-4000-8000-0000000000e4"
    _seed_stale_attachment_trace(
        ch_client, tables, project=project, foreign=foreign, trace=trace
    )
    version_column, _deleted = _eval_columns(tables)
    # 6,000 ids whose newest version moved to the tombstoned span, more than
    # the 5,000-row limit, and 10 ids live on a live span.
    ch_client.execute(
        f"""
        INSERT INTO {tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_str, created_at, {version_column})
        SELECT
            reinterpretAsUUID(sipHash128('stale', intDiv(number, 2))),
            toUUID('{trace}'),
            if(number % 2 = 0, 'root', 'dead'),
            toUUID('{config}'),
            concat('stale-', toString(number)),
            toDateTime64('2026-07-31 09:30:00', 3),
            1 + number % 2
        FROM numbers(12000)
        """
    )
    ch_client.execute(
        f"""
        INSERT INTO {tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_str, created_at, {version_column})
        SELECT
            reinterpretAsUUID(sipHash128('live', number)),
            toUUID('{trace}'),
            'child',
            toUUID('{config}'),
            concat('live-', toString(number)),
            toDateTime64('2026-07-31 09:30:00', 3),
            1
        FROM numbers(10)
        """
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project, foreign],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: [config],
        include_annotations=False,
        deadline_ms=30_000,
    )

    assert read.eval_unavailable is False
    assert sorted(row["output_str"] for row in read.evals) == sorted(
        f"live-{number}" for number in range(10)
    )
    # The prior statement served all 6,010 ids, over the row limit.
    prior = _prior_eval_rows(
        ch_client, tables, trace=trace, spans=["child", "root"], configs=[config]
    )
    assert len(prior) == 6010


def test_voice_simulator_exclusion_is_candidate_scoped_and_page_exact(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000041"
    started = datetime(2026, 7, 30, 13, 0)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id="simulator-newest",
                span_id="sim-root",
                started_at=started + timedelta(seconds=2),
                raw_log=('{"customer":{"number":"' + VAPI_PHONE_NUMBERS[0] + '"}}'),
            ),
            _span_row(
                project_id=project,
                trace_id="real-call",
                span_id="real-root",
                started_at=started + timedelta(seconds=1),
                raw_log='{"customer":{"number":"+10000000000"}}',
            ),
        ],
    )

    class LocalVoiceBuilder(VoiceCallListQueryBuilderV2):
        TABLE = detail_tables.spans

    builder = LocalVoiceBuilder(
        project_id=project,
        filters=[
            _time_filter(started - timedelta(minutes=1), started + timedelta(minutes=1))
        ],
        page_number=0,
        page_size=1,
        remove_simulation_calls=True,
    )
    page = read_bounded_filter_page(
        builder=builder,
        analytics=_Analytics(ch_client),
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=1,
        deadline_ms=5000,
    )

    assert page.complete is True
    assert [row["trace_id"] for row in page.rows] == ["real-call"]
    assert page.has_more is False


def test_voice_eval_hydration_replays_direct_write_versions_without_final(
    ch_client, detail_tables
):
    trace = "00000000-0000-4000-8000-000000000051"
    config = "00000000-0000-4000-8000-000000000052"
    live_eval = str(uuid.uuid4())
    deleted_eval = str(uuid.uuid4())
    ch_client.execute(
        f"""
        INSERT INTO {detail_tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_bool, output_float, output_str, output_str_list,
             eval_explanation, error, is_deleted, _version)
        VALUES
        """,
        [
            (live_eval, trace, "root", config, None, 0.1, None, "[]", "old", 0, 0, 1),
            (
                live_eval,
                trace,
                "root",
                config,
                None,
                0.9,
                None,
                "[]",
                "latest",
                0,
                0,
                2,
            ),
            (
                deleted_eval,
                trace,
                "root",
                config,
                1,
                None,
                None,
                "[]",
                "deleted",
                0,
                0,
                1,
            ),
            (
                deleted_eval,
                trace,
                "root",
                config,
                1,
                None,
                None,
                "[]",
                "deleted",
                0,
                1,
                2,
            ),
        ],
    )
    with override_settings(CH25_EVAL_LOGGER_TABLE=detail_tables.evals):
        builder = VoiceCallListQueryBuilderV2(
            project_id="00000000-0000-4000-8000-000000000053",
            eval_config_ids=[config],
        )
        query, params = builder.build_eval_query([trace])
    assert " FINAL" not in query
    rows = (
        _Analytics(ch_client)
        .execute_ch_query(
            query,
            params,
            timeout_ms=2000,
            settings={"max_threads": 1, "max_result_rows": 10},
        )
        .data
    )
    assert len(rows) == 1
    assert rows[0]["trace_id"] == uuid.UUID(trace)
    assert rows[0]["avg_score"] == 0.9
    assert rows[0]["eval_count"] == 1


def test_trace_detail_fails_closed_on_two_live_physical_rows_with_same_id(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000031"
    trace = "00000000-0000-4000-8000-000000000032"
    started = datetime(2026, 7, 30, 12, 0)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="reused",
                started_at=started.replace(microsecond=111111),
            ),
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="reused",
                started_at=started.replace(microsecond=999999),
            ),
        ],
    )
    with pytest.raises(TraceDetailReadUnavailable, match="ambiguous_span_identity"):
        read_trace_detail(
            analytics=_Analytics(ch_client),
            project_ids=[project],
            trace_id=trace,
            deadline_ms=5000,
        )


def test_trace_replayed_into_two_scoped_projects_serves_newest_copy(
    ch_client, detail_tables
):
    older = "00000000-0000-4000-8000-000000000091"
    newer = "00000000-0000-4000-8000-000000000092"
    trace = "00000000-0000-4000-8000-000000000093"
    started = datetime(2026, 9, 22, 16, 27, 36)
    rows = []
    for project, version in ((older, 1), (newer, 2)):
        rows.append(
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="root",
                started_at=started,
                version=version,
                input_value=f"copy-{version}",
            )
        )
        rows.append(
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="child",
                started_at=started + timedelta(milliseconds=1),
                version=version,
                parent="root",
                observation_type="llm",
                input_value=f"copy-{version}",
            )
        )
    ch_client.execute(f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES", rows)
    analytics = _Analytics(ch_client)

    read = read_trace_detail(
        analytics=analytics,
        project_ids=[older, newer],
        trace_id=trace,
        deadline_ms=5000,
    )
    assert read.project_id == newer
    assert [(row["id"], row["input"]) for row in read.spans] == [
        ("root", "copy-2"),
        ("child", "copy-2"),
    ]

    # A newer copy outside the caller's scope is never selected.
    scoped = read_trace_detail(
        analytics=analytics,
        project_ids=[older],
        trace_id=trace,
        deadline_ms=5000,
    )
    assert scoped.project_id == older
    assert {row["input"] for row in scoped.spans} == {"copy-1"}


def test_span_anchor_serves_newest_live_copy_across_scoped_projects(
    ch_client, detail_tables
):
    older = "00000000-0000-4000-8000-0000000000a1"
    newer = "00000000-0000-4000-8000-0000000000a2"
    trace = "00000000-0000-4000-8000-0000000000a3"
    started = datetime(2026, 9, 22, 16, 27, 36)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="shared",
                started_at=started,
                version=version,
                input_value=f"copy-{version}",
            )
            for project, version in ((older, 1), (newer, 2))
        ],
    )
    analytics = _Analytics(ch_client)

    read = read_span_detail(
        analytics=analytics,
        project_ids=[older, newer],
        span_id="shared",
        deadline_ms=5000,
    )
    assert read.project_id == newer
    assert [row["input"] for row in read.spans] == ["copy-2"]

    # The newest write is a tombstone: the deleted copy is not resurrected and
    # the remaining live copy is served.
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=newer,
                trace_id=trace,
                span_id="shared",
                started_at=started,
                version=3,
                deleted=1,
            )
        ],
    )
    read = read_span_detail(
        analytics=analytics,
        project_ids=[older, newer],
        span_id="shared",
        deadline_ms=5000,
    )
    assert read.project_id == older
    assert [row["input"] for row in read.spans] == ["copy-1"]


# Score versions oldest first as (span, payload) or (span, payload, deleted);
# spans as in the eval cases: "root" and "child" live, "dead" tombstoned,
# "foreign" live only in another project's copy, "ghost" absent.
_STALE_SCORE_CASES = {
    "S1-null": [("root", "S1-v1"), (None, "S1-v2")],
    "S2-dead": [("root", "S2-v1"), ("dead", "S2-v2")],
    "S3-foreign": [("root", "S3-v1"), ("foreign", "S3-v2")],
    "S4-ghost": [("root", "S4-v1"), ("ghost", "S4-v2")],
    "S5-null-tombstone": [("root", "S5-v1"), (None, "S5-v2", 1)],
    "S6-child-to-root": [("child", "S6-v1"), ("root", "S6-v2")],
    "S7-null-to-root": [(None, "S7-v1"), ("root", "S7-v2")],
    "S8-root-null-child": [("root", "S8-v1"), (None, "S8-v2"), ("child", "S8-v3")],
    "S9-single": [("root", "S9-v1")],
    "S13-five-versions": [
        ("root", "S13-v1"),
        (None, "S13-v2"),
        ("child", "S13-v3"),
        ("dead", "S13-v4"),
        ("root", "S13-v5"),
    ],
}
_SCORE_CREATED = datetime(2026, 7, 31, 9, 45)
# The trace-detail annotation read before the latest-first change: the span
# check runs in PREWHERE, before GROUP BY id picks each score's latest version.
_PRIOR_ANNOTATION_SQL = """
SELECT
    toString(id) AS score_id,
    argMax(observation_span_id, _peerdb_version) AS latest_span_id,
    toString(argMax(label_id, _peerdb_version)) AS latest_label_id,
    argMax(value, _peerdb_version) AS latest_value,
    greatest(
        argMax(_peerdb_is_deleted, _peerdb_version),
        argMax(deleted, _peerdb_version)
    ) AS latest_is_deleted
FROM {table}
PREWHERE tracer_project_id = %(project)s
  AND trace_id = %(trace)s
  AND observation_span_id IN %(spans)s
GROUP BY id
HAVING latest_is_deleted = 0
"""


def _seed_score_trace_spans(ch_client, tables, *, project, foreign, trace):
    """Live "root" and "child", tombstoned "dead", "foreign" in another copy."""

    started = datetime(2026, 7, 31, 9, 0, 0)
    ch_client.execute(
        f"INSERT INTO {tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="root",
                started_at=started,
                version=2,
            ),
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="child",
                parent="root",
                started_at=started + timedelta(seconds=1),
            ),
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="dead",
                started_at=started + timedelta(seconds=2),
            ),
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="dead",
                started_at=started + timedelta(seconds=2),
                version=2,
                deleted=1,
            ),
            _span_row(
                project_id=foreign,
                trace_id=trace,
                span_id="foreign",
                started_at=started,
            ),
        ],
    )


def _insert_scores(ch_client, tables, physical):
    """Insert physical score versions, given as dicts."""

    ch_client.execute(
        f"""
        INSERT INTO {tables.scores}
            (id, tracer_project_id, trace_id, observation_span_id, label_id,
             value, deleted, created_at, _peerdb_is_deleted, _peerdb_version)
        VALUES
        """,
        [
            (
                row["id"],
                row["project"],
                row["trace_id"],
                row["span"],
                row["label"],
                row["value"],
                0,
                row.get("created", _SCORE_CREATED),
                row["deleted"],
                row["version"],
            )
            for row in physical
        ],
    )


def _score_oracle(ch_client, tables, *, project, trace, live_spans):
    """``{id: (span, label, value)}`` from every physical score row.

    The newest version of each id is served only when it is live, in the
    project, on the trace and on one of the live spans.
    """

    latest = {}
    for row in ch_client.execute(
        f"""
        SELECT toString(id), toString(tracer_project_id), toString(trace_id),
               observation_span_id, toString(label_id), value,
               greatest(deleted, _peerdb_is_deleted), _peerdb_version
        FROM {tables.scores}
        """
    ):
        score_id, version = row[0], row[7]
        assert (score_id, version) not in {
            (kept[0], kept[7]) for kept in latest.values()
        }, "version tie"
        if score_id not in latest or version > latest[score_id][7]:
            latest[score_id] = row
    return {
        score_id: (row[3], row[4], row[5])
        for score_id, row in latest.items()
        if row[1] == project and row[2] == trace and not row[6] and row[3] in live_spans
    }


def _prior_annotation_rows(ch_client, tables, *, project, trace, spans):
    rows = ch_client.execute(
        _PRIOR_ANNOTATION_SQL.format(table=tables.scores),
        {"project": project, "trace": trace, "spans": tuple(spans)},
    )
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def _read_annotations(ch_client, *, project, foreign, trace):
    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project, foreign],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: [],
        deadline_ms=30_000,
    )
    assert read.project_id == project
    # identity + content + annotations; no authorized configs, so no evals.
    assert read.query_count == 3
    return read


def _ids_by_label(ch_client, tables):
    return dict(
        ch_client.execute(
            f"SELECT DISTINCT toString(label_id), toString(id) FROM {tables.scores}"
        )
    )


def test_trace_detail_serves_annotation_only_when_latest_version_is_on_a_live_span(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-0000000000f1"
    foreign = "00000000-0000-4000-8000-0000000000f2"
    trace = "00000000-0000-4000-8000-0000000000f3"
    _seed_score_trace_spans(
        ch_client, detail_tables, project=project, foreign=foreign, trace=trace
    )
    # Each case has its own label, so a served row names its case.
    labels = {name: str(uuid.uuid4()) for name in _STALE_SCORE_CASES}
    ids = {name: str(uuid.uuid4()) for name in _STALE_SCORE_CASES}
    physical = [
        {
            "id": ids[name],
            "project": project,
            "trace_id": trace,
            "span": span,
            "label": labels[name],
            "value": payload,
            "deleted": rest[0] if rest else 0,
            "version": version,
        }
        for name, versions in _STALE_SCORE_CASES.items()
        for version, (span, payload, *rest) in enumerate(versions, start=1)
    ]
    for name, versions in {
        # Another project's score on a live span of the selected copy.
        "S10-foreign-project": [(foreign, "root")],
        # A backfill sets the project, then the span is cleared.
        "S14-project-backfill": [(None, "root"), (project, "root"), (project, None)],
    }.items():
        labels[name], ids[name] = str(uuid.uuid4()), str(uuid.uuid4())
        physical.extend(
            {
                "id": ids[name],
                "project": score_project,
                "trace_id": trace,
                "span": span,
                "label": labels[name],
                "value": f"{name}-v{version}",
                "deleted": 0,
                "version": version,
            }
            for version, (score_project, span) in enumerate(versions, start=1)
        )
    _insert_scores(ch_client, detail_tables, physical)

    read = _read_annotations(ch_client, project=project, foreign=foreign, trace=trace)

    oracle = _score_oracle(
        ch_client,
        detail_tables,
        project=project,
        trace=trace,
        live_spans={"root", "child"},
    )
    assert oracle == {
        ids["S6-child-to-root"]: ("root", labels["S6-child-to-root"], "S6-v2"),
        ids["S7-null-to-root"]: ("root", labels["S7-null-to-root"], "S7-v2"),
        ids["S8-root-null-child"]: ("child", labels["S8-root-null-child"], "S8-v3"),
        ids["S9-single"]: ("root", labels["S9-single"], "S9-v1"),
        ids["S13-five-versions"]: ("root", labels["S13-five-versions"], "S13-v5"),
    }
    id_of_label = _ids_by_label(ch_client, detail_tables)
    served = {
        id_of_label[row["label_id"]]: (row["span_id"], row["label_id"], row["value"])
        for row in read.annotations
    }
    assert len(served) == len(read.annotations)
    assert served == oracle
    # The prior statement serves each moved score's older version.
    prior = _prior_annotation_rows(
        ch_client, detail_tables, project=project, trace=trace, spans=["root", "child"]
    )
    for name in ("S1-null", "S2-dead", "S3-foreign", "S4-ghost"):
        assert prior[ids[name]] == ("root", labels[name], f"{name[:2]}-v1"), name
    assert prior[ids["S14-project-backfill"]] == (
        "root",
        labels["S14-project-backfill"],
        "S14-project-backfill-v2",
    )


def test_trace_detail_annotation_residual_cases_match_the_prior_statement(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-0000000000f4"
    foreign = "00000000-0000-4000-8000-0000000000f5"
    trace = "00000000-0000-4000-8000-0000000000f6"
    _seed_score_trace_spans(
        ch_client, detail_tables, project=project, foreign=foreign, trace=trace
    )
    label, other_label = str(uuid.uuid4()), str(uuid.uuid4())
    label_move, tombstoned = str(uuid.uuid4()), str(uuid.uuid4())

    def score(score_id, version, span, label_id, value):
        return {
            "id": score_id,
            "project": project,
            "trace_id": trace,
            "span": span,
            "label": label_id,
            "value": value,
            "deleted": 0,
            "version": version,
        }

    _insert_scores(
        ch_client,
        detail_tables,
        [
            # The label (a sort-key column) changes in the same version as the
            # span leaves the live spans.
            score(label_move, 1, "root", label, "S11-v1"),
            score(label_move, 2, None, other_label, "S11-v2"),
            score(tombstoned, 1, "root", label, "S12-v1"),
        ],
    )
    # A hard delete arrives as the id, the tombstone flag and a newer version;
    # every other column keeps its default.
    ch_client.execute(
        f"INSERT INTO {detail_tables.scores} (id, _peerdb_is_deleted, _peerdb_version)"
        " VALUES",
        [(tombstoned, 1, 2)],
    )

    read = _read_annotations(ch_client, project=project, foreign=foreign, trace=trace)

    assert (
        _score_oracle(
            ch_client,
            detail_tables,
            project=project,
            trace=trace,
            live_spans={"root", "child"},
        )
        == {}
    )
    prior = _prior_annotation_rows(
        ch_client, detail_tables, project=project, trace=trace, spans=["root", "child"]
    )
    assert prior == {
        label_move: ("root", label, "S11-v1"),
        tombstoned: ("root", label, "S12-v1"),
    }
    # model_hub_score reads every version of the candidates' sort keys; a
    # version under another key, or an id-only tombstone without project or
    # trace, is not one of them, so both scores stay as before.
    assert sorted(
        (row["span_id"], row["label_id"], row["value"]) for row in read.annotations
    ) == sorted(prior.values())


def test_trace_detail_annotation_read_past_the_row_limit_returns_only_live_rows(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-0000000000f7"
    foreign = "00000000-0000-4000-8000-0000000000f8"
    trace = "00000000-0000-4000-8000-0000000000f9"
    label = "00000000-0000-4000-8000-0000000000fa"
    _seed_score_trace_spans(
        ch_client, detail_tables, project=project, foreign=foreign, trace=trace
    )
    # 6,000 scores whose newest version moved to the tombstoned span, more
    # than the 5,000-row limit, and 10 scores live on a live span.
    for prefix, rows, span, versions in (
        ("stale", 12_000, "if(number % 2 = 0, 'root', 'dead')", "1 + number % 2"),
        ("live", 10, "'child'", "1"),
    ):
        score_id = (
            f"reinterpretAsUUID(sipHash128('{prefix}', intDiv(number, 2)))"
            if prefix == "stale"
            else f"reinterpretAsUUID(sipHash128('{prefix}', number))"
        )
        ch_client.execute(
            f"""
            INSERT INTO {detail_tables.scores}
                (id, tracer_project_id, trace_id, observation_span_id, label_id,
                 value, deleted, created_at, _peerdb_is_deleted,
                 _peerdb_version)
            SELECT
                {score_id},
                toUUID('{project}'),
                toUUID('{trace}'),
                {span},
                toUUID('{label}'),
                concat('{prefix}-', toString(number)),
                0,
                toDateTime64('2026-07-31 09:45:00', 6),
                0,
                {versions}
            FROM numbers({rows})
            """
        )

    read = _read_annotations(ch_client, project=project, foreign=foreign, trace=trace)

    assert sorted(row["value"] for row in read.annotations) == sorted(
        f"live-{number}" for number in range(10)
    )
    # The prior statement served all 6,010 scores, over the row limit.
    prior = _prior_annotation_rows(
        ch_client, detail_tables, project=project, trace=trace, spans=["root", "child"]
    )
    assert len(prior) == 6010
