"""Real ReplacingMergeTree proofs in a disposable embedded ClickHouse database.

No environment credential, server socket, production schema or shared table is
used. Run with chdb installed on a disposable cloud worker, never on a laptop.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client

from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.entity_tag_targets import (
    TagTargetError,
    resolve_tag_target,
)
from tracer.services.entity_tags import effective_tags
from tracer.tests.test_entity_tag_target import HOUR, PROJECT, START, TRACE, reference

OTHER_PROJECT = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    chdb = pytest.importorskip("chdb")
    session = chdb.session.Session(str(tmp_path_factory.mktemp("tag-target-ch25")))
    session.query(
        """
        CREATE TABLE spans (
            project_id UUID, trace_id String, id String,
            observation_type String, service_name String,
            start_time DateTime64(6, 'UTC'), end_time DateTime64(6, 'UTC'),
            parent_span_id Nullable(String), _version UInt64, is_deleted UInt8,
            name String, input String, output String, model String,
            latency_ms Float64, prompt_tokens UInt64, completion_tokens UInt64,
            total_tokens UInt64, cost Float64, status String, status_message String,
            tags String DEFAULT '[]', span_events String, provider String,
            attributes_extra String, project_version_id Nullable(UUID),
            custom_eval_config_id Nullable(UUID), trace_session_id Nullable(UUID),
            metadata Map(String, String), resource_attrs Map(String, String),
            attrs_string Map(String, String), attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8)
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)
    """
    )
    client = Client("unused.invalid")  # parameter quoting only; never connects

    class Engine:
        supports_per_query_read_settings = True

        def execute_ch_query(self, sql, params, **kwargs):
            cap = kwargs["server_execution_cap_ms"]
            assert 25 <= cap <= 3000
            settings = {**kwargs["settings"], "max_execution_time": cap / 1000}
            clauses = ", ".join(f"{name}=%({name})s" for name in settings)
            sql = client.substitute_params(
                f"{sql} SETTINGS {clauses}",
                {**params, **settings},
                client.connection.context,
            )
            data = [
                json.loads(line)
                for line in str(session.query(sql, "JSONEachRow")).splitlines()
                if line
            ]
            for row in data:
                for key in ("start_time", "start_hour"):
                    row[key] = datetime.fromisoformat(row[key]).replace(tzinfo=UTC)
            return SimpleNamespace(data=data, complete=True)

        def insert(self, **changes):
            row = {
                "project_id": PROJECT,
                "trace_id": TRACE,
                "id": "0123456789abcdef",
                "observation_type": "span",
                "service_name": "svc",
                "start_time": START.isoformat(sep=" ").replace("+00:00", ""),
                "parent_span_id": "",
                "_version": 1,
                "is_deleted": 0,
                "tags": '["sdk"]',
                **changes,
            }
            session.query("INSERT INTO spans FORMAT JSONEachRow " + json.dumps(row))

        def reset(self):
            # Only this provisioned embedded table is ever cleared.
            session.query("TRUNCATE TABLE spans")

        def checksum(self):
            return str(
                session.query(
                    "SELECT count(), sum(_version), groupArray(tags) FROM spans",
                    "JSONEachRow",
                )
            )

    try:
        yield Engine()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def reset(engine):
    engine.reset()


def resolve(engine, *, trace_id=None, **changes):
    return resolve_tag_target(
        analytics=engine,
        authorized_project_id=PROJECT,
        reference=reference(**changes),
        trace_id=trace_id,
        deadline=ReadDeadline.start(3000, enforce_on_server=True),
    )


def test_exact_old_hour_resolves_without_any_postgres_telemetry(engine):
    engine.insert(_version=2)
    engine.insert(project_id=OTHER_PROJECT, _version=99, tags='["foreign"]')
    before = engine.checksum()
    target = resolve(engine)
    assert effective_tags(target.row, manual_tags=[{"name": "ui", "color": "#123456"}])[
        "tags"
    ] == [{"name": "sdk"}, {"name": "ui", "color": "#123456"}]
    assert engine.checksum() == before


def test_within_hour_correction_does_not_resurrect_old_expected_timestamp(engine):
    engine.insert(_version=1)
    corrected = START - timedelta(minutes=10)
    engine.insert(
        _version=2,
        start_time=corrected.replace(tzinfo=None).isoformat(sep=" "),
        tags='["new-sdk"]',
    )
    with pytest.raises(TagTargetError, match="tag_target_changed"):
        resolve(engine, expected_version="1")
    target = resolve(engine, expected_start_time=corrected)
    assert target.row["tags"] == '["new-sdk"]'
    assert target.key[1] == "trace"


def test_latest_tombstone_cannot_be_recovered_by_expected_version(engine):
    engine.insert(_version=1)
    engine.insert(_version=2, is_deleted=1)
    with pytest.raises(TagTargetError, match="tag_target_not_found"):
        resolve(engine, expected_version="1")


def test_parent_role_is_checked_after_latest_replacement(engine):
    engine.insert(_version=1)
    engine.insert(_version=2, parent_span_id="parent")
    with pytest.raises(TagTargetError, match="tag_target_changed"):
        resolve(engine, trace_id=TRACE)
    assert resolve(engine).key[1] == "span"


def test_physical_correction_does_not_implicitly_retarget_span_labels(engine):
    engine.insert(_version=2, parent_span_id="parent")
    engine.insert(_version=3, parent_span_id="parent", service_name="corrected")
    old = resolve(engine)
    new = resolve(engine, service_name="corrected", expected_version="3")
    assert old.key != new.key
    assert old.key[-1] == "svc" and new.key[-1] == "corrected"


def test_attribute_only_sdk_tags_remain_visible_with_populated_dedicated_column(engine):
    engine.insert(
        _version=2,
        attrs_string={"tag.tags": '["map-sdk"]'},
        attributes_extra='{"tag.tags":["extra-sdk"]}',
    )
    target = resolve(engine)
    assert effective_tags(target.row, manual_tags=[])["source_tag_names"] == [
        "sdk",
        "map-sdk",
        "extra-sdk",
    ]


@pytest.mark.parametrize(
    "different_key",
    [
        {"project_id": OTHER_PROJECT},
        {"trace_id": "other-trace"},
        {"id": "fedcba9876543210"},
        {"observation_type": "different-type"},
        {"service_name": "different-service"},
        {"start_time": "2020-01-01 01:00:00.000000"},
    ],
)
def test_every_physical_key_dimension_isolates_same_span_decoys(engine, different_key):
    engine.insert(_version=2, parent_span_id="parent")
    engine.insert(_version=99, tags='["foreign"]', **different_key)
    target = resolve(engine)
    assert target.key == (
        PROJECT,
        "span",
        TRACE,
        "0123456789abcdef",
        HOUR,
        "span",
        "svc",
    )
    assert target.row["tags"] == '["sdk"]'


@pytest.mark.parametrize("start", [HOUR, HOUR + timedelta(hours=1, microseconds=-1)])
def test_full_hour_bounds_include_both_boundary_winners(engine, start):
    engine.insert(_version=2, start_time=start.replace(tzinfo=None).isoformat(sep=" "))
    assert resolve(engine, expected_start_time=start).row["start_time"] == start
