"""Trace page root pruning on an isolated production-shaped CH25 table."""

import re
from datetime import UTC, timedelta, timezone
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_span_primary_prefix import primary_indexes


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


class PreviousRootReplay(TraceListQueryBuilderV2):
    def _root_replay_query(self, identities, *, content):
        sql, params = super()._root_replay_query(identities, content=content)
        prefix = "content" if content else "page_hydration"
        return re.sub(
            r"AND \(observation_type, service_name,\s*toStartOfHour\(start_time\), trace_id\)"
            + rf"\s*IN %\({prefix}_primary_prefixes\)s",
            "",
            sql,
        ), params


def root(**changes):
    return {
        "project_id": PROJECT,
        "trace_id": "trace",
        "root_span_id": "0",
        "start_time": START + timedelta(minutes=20),
        "_root_version": 1,
        "_root_observation_type": "span",
        "_root_service_name": "service-a",
        "_root_start_hour": START,
        **changes,
    }


def builder(previous=False, **kwargs):
    cls = PreviousRootReplay if previous else TraceListQueryBuilderV2
    scope = {} if "project_ids" in kwargs else {"project_id": PROJECT}
    return cls(**scope, filters=[time_filter(end=START + timedelta(days=2))], **kwargs)


def queries(subject, rows):
    return (
        subject.build_filter_page_hydration_query(rows),
        subject.build_content_query(
            list(dict.fromkeys(r["trace_id"] for r in rows)),
            root_identities=subject.content_root_identities_for_rows(rows),
        ),
    )


@pytest.fixture
def trace_engine(request):
    execute, insert = request.getfixturevalue("engine")
    execute("ALTER TABLE spans ADD COLUMN trace_name String DEFAULT ''")
    execute("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    execute("ALTER TABLE spans ADD COLUMN metadata Map(String,String)")
    execute("""CREATE TABLE traces (
        project_id UUID, id String, tags String DEFAULT '[]',
        _version UInt64, is_deleted UInt8 DEFAULT 0
    ) ENGINE = ReplacingMergeTree(_version) ORDER BY (project_id, id)""")
    return execute, insert


@pytest.mark.integration
def test_root_primary_prefix_prunes_same_physical_results(
    trace_engine, record_property
):
    execute, _ = trace_engine
    assert execute("SELECT version() v")[0]["v"].startswith("25.")
    execute(
        """INSERT INTO spans
        (project_id, observation_type, service_name, start_time, trace_id, id, parent_span_id, _version)
        SELECT toUUID(%(project)s), 'span', 'service-a',
               toDateTime64(%(start)s, 6, 'UTC') + toIntervalHour(intDiv(number,8192)),
               'trace', toString(number), '', 1
        FROM numbers(262144)""",
        {"project": PROJECT, "start": START + timedelta(minutes=20)},
    )
    for name, old, new in zip(
        ("page", "content"),
        queries(builder(previous=True), [root()]),
        queries(builder(), [root()]),
        strict=True,
    ):
        old_marks = sum(
            i["Selected Granules"]
            for i in primary_indexes(
                execute("EXPLAIN indexes=1, json=1 " + old[0], old[1])
            )
        )
        new_marks = sum(
            i["Selected Granules"]
            for i in primary_indexes(
                execute("EXPLAIN indexes=1, json=1 " + new[0], new[1])
            )
        )
        record_property(name + "_granules", f"{old_marks}->{new_marks}")
        assert new_marks <= old_marks
        expected = execute(*old)
        assert len(expected) == 1 and expected[0]["root_span_id"] == "0"
        assert execute(*new) == expected


@pytest.mark.unit
@pytest.mark.parametrize("offset", [0, 330, 345, -210])
def test_root_prefix_binds_utc_without_driver_timezone_conversion(offset):
    instant = (START + timedelta(minutes=20)).replace(tzinfo=UTC)
    rows = [
        root(start_time=instant.astimezone(timezone(timedelta(minutes=offset)))),
        root(trace_id="second", root_span_id="1", start_time=instant),
    ]
    for sql, params in queries(builder(), rows):
        prefix = "content" if "content_primary_prefixes" in params else "page_hydration"
        assert params[prefix + "_primary_prefixes"][0] == (
            "span",
            "service-a",
            START,
            "trace",
        )
        assert params[prefix + "_physical_keys"][0][3] == START
        rendered = []
        for zone in ("UTC", "Asia/Kolkata", "America/Los_Angeles"):
            formatter = Client("invalid.invalid")  # formatting only
            formatter.connection.context.server_info = SimpleNamespace(
                get_timezone=lambda zone=zone: zone
            )
            rendered.append(
                formatter.substitute_params(sql, params, formatter.connection.context)
            )
        assert len(set(rendered)) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "replacement",
    [
        {"is_deleted": 1},
        {"parent_span_id": "now-child"},
        {"start_time": START + timedelta(minutes=10), "input": "corrected"},
    ],
)
def test_root_prefix_keeps_latest_replacement_and_complete_project_key(
    trace_engine, replacement
):
    execute, insert = trace_engine
    insert(id="0", parent_span_id="", input="old")
    insert(id="0", project_id=OTHER_PROJECT, parent_span_id="", input="foreign")
    insert(**{"id": "0", "_version": 2, "parent_span_id": "", **replacement})
    for old, new in zip(
        queries(builder(previous=True), [root()]),
        queries(builder(), [root()]),
        strict=True,
    ):
        expected = execute(*old)
        assert execute(*new) == expected
        if replacement.get("is_deleted") or replacement.get("parent_span_id"):
            assert expected == []
        else:
            assert len(expected) == 1 and expected[0]["_root_version"] == 2
