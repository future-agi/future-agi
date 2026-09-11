"""Execute session Score membership on an explicitly isolated ClickHouse fixture.

This is SQL integration coverage, not a replacement for the session UI journey.
No source/application database is accepted by this fixture.
"""

import json
import os
import uuid
from datetime import timedelta

import pytest
from clickhouse_driver import Client

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests import test_score_filter_hard_tombstone_ch25 as score_fixtures
from tracer.tests.test_score_filter_hard_tombstone_ch25 import (
    LABEL_ID,
    PROJECT_ID,
    SCORE_ID,
    SESSION_ID,
    SPAN_ID,
    STARTED_AT,
    TRACE_ID,
)

pytestmark = pytest.mark.integration
score_filter_tables = score_fixtures.score_filter_tables
OTHER_PROJECT = "00000000-0000-4000-8000-000000000611"
OTHER_SESSION = "00000000-0000-4000-8000-000000000613"
OTHER_TRACE = "00000000-0000-4000-8000-000000000612"
SESSION_ALIAS = "00000000-0000-4000-8000-000000000614"
SECOND_LABEL = "00000000-0000-4000-8000-000000000615"
SECOND_SCORE = "00000000-0000-4000-8000-000000000616"


@pytest.fixture(scope="module")
def ch_client():
    if os.environ.get("CATALOG_SESSION_INTEGRATION") != "1":
        pytest.skip("requires the owned isolated session-annotation ClickHouse fixture")
    client = Client(host="127.0.0.1", port=29073, connect_timeout=3)
    assert client.execute("SELECT hostName()") == [("catalog-session-test",)]
    database = f"catalog_session_test_{uuid.uuid4().hex}"
    client.execute(f"CREATE DATABASE {database}")
    client.disconnect()
    client = Client(host="127.0.0.1", port=29073, database=database)
    try:
        yield client
    finally:
        # Retain the isolated database for inspection; never drop a source DB.
        client.disconnect()


def _leaf(value, *, kind="text", operation="equals"):
    return {
        "column_id": LABEL_ID,
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": kind,
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _query(tables, leaf, *, remapped=False, org=False, labels=(LABEL_ID,)):
    spans, scores = tables
    builder = SessionListQueryBuilderV2(
        **(
            {"project_ids": [PROJECT_ID, OTHER_PROJECT]}
            if org
            else {"project_id": PROJECT_ID}
        ),
        annotation_label_ids=list(labels),
        annotation_label_ids_by_project={PROJECT_ID: list(labels), OTHER_PROJECT: []},
        eval_config_ids=[],
        bounded_internal_scan=True,
    )
    params = {"project_id": PROJECT_ID}
    ctes, predicates, binds = builder._bounded_relational_membership_plan(
        [leaf], scope_to_request_window=False, available_params=params
    )
    params.update(binds)
    # Source reads use real latest span rows; both project and remap identities
    # remain present in the finite relation consumed by the production compiler.
    sql = f"""
    WITH ts_survivor_map AS (
        SELECT toUUID('{SESSION_ALIAS}') AS any_id,
               toUUID('{SESSION_ID}') AS survivor_id
        WHERE {1 if remapped else 0}
    ), resolved_root_sessions AS (
        SELECT project_id, trace_id, trace_session_id AS session_id
        FROM {spans} FINAL
        WHERE is_deleted = 0 AND parent_span_id IS NULL
    ){ctes}
    SELECT DISTINCT toString(session_id)
    FROM resolved_root_sessions
    WHERE {predicates[0]}
    ORDER BY session_id
    """
    return (
        sql.replace("model_hub_score", scores).replace("FROM spans ", f"FROM {spans} "),
        params,
    )


def _score(
    client,
    tables,
    value,
    *,
    source="session",
    version=1,
    deleted=False,
    hard_deleted=0,
    project=PROJECT_ID,
    remapped=False,
    score_id=SCORE_ID,
    label_id=LABEL_ID,
):
    client.execute(
        f"INSERT INTO {tables[1]} "
        "(id, trace_id, trace_session_id, observation_span_id, tracer_project_id, "
        "label_id, value, deleted, created_at, _peerdb_is_deleted, _peerdb_version) VALUES",
        [
            (
                score_id,
                TRACE_ID if source == "trace" else None,
                (
                    (SESSION_ALIAS if remapped else SESSION_ID)
                    if source == "session"
                    else None
                ),
                SPAN_ID if source == "span" else None,
                project,
                label_id,
                json.dumps(value),
                deleted,
                STARTED_AT - timedelta(days=90),
                hard_deleted,
                version,
            )
        ],
    )


@pytest.mark.parametrize("source", ["session", "trace", "span"])
@pytest.mark.parametrize(
    "old,new,kind,old_filter,new_filter",
    [
        ({"text": "old"}, {"text": "new"}, "text", "old", "new"),
        ({"value": 25}, {"value": 75}, "number", 25, 75),
        ({"rating": 2}, {"rating": 4}, "number", 2, 4),
        ({"value": "up"}, {"value": "down"}, "categorical", "thumbs_up", "thumbs_down"),
        (
            {"selected": ["alpha"]},
            {"selected": ["beta"]},
            "categorical",
            "alpha",
            "beta",
        ),
        (
            {"selected": ["alpha", "gamma"]},
            {"selected": ["beta"]},
            "categorical",
            "alpha",
            "beta",
        ),
    ],
)
def test_current_session_annotation_membership(
    ch_client, score_filter_tables, source, old, new, kind, old_filter, new_filter
):
    tables = score_filter_tables
    old_sql, old_params = _query(tables, _leaf(old_filter, kind=kind))
    new_sql, new_params = _query(tables, _leaf(new_filter, kind=kind))
    assert ch_client.execute(old_sql, old_params) == []
    _score(ch_client, tables, old, source=source)
    assert ch_client.execute(old_sql, old_params) == [(SESSION_ID,)]
    assert ch_client.execute(new_sql, new_params) == []
    _score(ch_client, tables, new, source=source, version=2)
    assert ch_client.execute(old_sql, old_params) == []
    assert ch_client.execute(new_sql, new_params) == [(SESSION_ID,)]
    _score(ch_client, tables, new, source=source, version=3, hard_deleted=1)
    assert ch_client.execute(new_sql, new_params) == []


@pytest.mark.parametrize("org", [False, True])
@pytest.mark.parametrize("remapped", [False, True])
def test_session_scores_keep_project_and_identity_scope(
    ch_client, score_filter_tables, org, remapped
):
    tables = score_filter_tables
    positive, params = _query(tables, _leaf("approved"), remapped=remapped, org=org)
    _score(
        ch_client,
        tables,
        {"text": "approved"},
        project=OTHER_PROJECT,
        remapped=remapped,
    )
    assert ch_client.execute(positive, params) == []
    _score(ch_client, tables, {"text": "approved"}, version=2, remapped=remapped)
    assert ch_client.execute(positive, params) == [(SESSION_ID,)]
    _score(
        ch_client,
        tables,
        {"text": "approved"},
        version=3,
        deleted=True,
        remapped=remapped,
    )
    assert ch_client.execute(positive, params) == []


def test_session_annotation_presence_and_absence_are_complements(
    ch_client, score_filter_tables
):
    tables = score_filter_tables
    ch_client.execute(
        f"INSERT INTO {tables[0]} "
        "(id, project_id, trace_id, trace_session_id, start_time, created_at, _version) VALUES",
        [
            (
                "other-root",
                PROJECT_ID,
                OTHER_TRACE,
                OTHER_SESSION,
                STARTED_AT,
                STARTED_AT,
                1,
            )
        ],
    )
    present, params = _query(tables, _leaf(None, operation="is_not_null"))
    absent, absent_params = _query(tables, _leaf(None, operation="is_null"))
    both = sorted([(SESSION_ID,), (OTHER_SESSION,)])
    assert ch_client.execute(present, params) == []
    assert ch_client.execute(absent, absent_params) == both
    _score(ch_client, tables, {"text": "approved"})
    assert ch_client.execute(present, params) == [(SESSION_ID,)]
    assert ch_client.execute(absent, absent_params) == [(OTHER_SESSION,)]
    _score(ch_client, tables, {"text": "approved"}, version=2, deleted=True)
    assert ch_client.execute(present, params) == []
    assert ch_client.execute(absent, absent_params) == both


def test_annotation_completeness_combines_session_and_trace_scores(
    ch_client, score_filter_tables
):
    tables = score_filter_tables
    leaf = {
        "column_id": "has_annotation",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": True,
        },
    }
    sql, params = _query(tables, leaf, labels=(LABEL_ID, SECOND_LABEL))
    _score(ch_client, tables, {"text": "session label"})
    assert ch_client.execute(sql, params) == []
    _score(
        ch_client,
        tables,
        {"text": "trace label"},
        source="trace",
        score_id=SECOND_SCORE,
        label_id=SECOND_LABEL,
    )
    assert ch_client.execute(sql, params) == [(SESSION_ID,)]
    _score(
        ch_client,
        tables,
        {"text": "trace label"},
        source="trace",
        version=2,
        score_id=SECOND_SCORE,
        label_id=SECOND_LABEL,
        hard_deleted=1,
    )
    assert ch_client.execute(sql, params) == []


@pytest.mark.parametrize("org", [False, True])
@pytest.mark.parametrize("remapped", [False, True])
def test_full_session_classifier_executes_with_current_and_historical_scores(
    ch_client, score_filter_tables, org, remapped
):
    tables = score_filter_tables
    remap_table = f"_test_session_remap_{uuid.uuid4().hex}"
    # Same latest-state engine/key as v2/schema/019_id_remap.sql.
    ch_client.execute(
        f"CREATE TABLE {remap_table} (old_id UUID, new_id UUID, "
        "version DateTime64(6, 'UTC') DEFAULT now64(6, 'UTC')) "
        "ENGINE = ReplacingMergeTree(version) ORDER BY old_id"
    )
    try:
        if remapped:
            survivor = "00000000-0000-4000-8000-000000000699"
            ch_client.execute(
                f"INSERT INTO {remap_table} (old_id, new_id) VALUES",
                [
                    (SESSION_ID, survivor),
                    (SESSION_ALIAS, survivor),
                ],
            )

        class FixtureBuilder(SessionListQueryBuilderV2):
            TABLE = tables[0]

        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        (STARTED_AT - timedelta(minutes=1)).isoformat(),
                        (STARTED_AT + timedelta(minutes=1)).isoformat(),
                    ],
                },
            },
            _leaf("approved"),
        ]
        builder = FixtureBuilder(
            **(
                {"project_ids": [PROJECT_ID, OTHER_PROJECT]}
                if org
                else {"project_id": PROJECT_ID}
            ),
            filters=filters,
            annotation_label_ids=[LABEL_ID],
            annotation_label_ids_by_project={PROJECT_ID: [LABEL_ID], OTHER_PROJECT: []},
            eval_config_ids=[],
            bounded_internal_scan=True,
        )
        sql, params = builder.build_filter_match_query([SESSION_ID])
        sql = (
            sql.replace("trace_session_id_remap", remap_table)
            .replace("model_hub_score", tables[1])
            .replace("FROM spans ", f"FROM {tables[0]} ")
        )
        source_before = ch_client.execute(f"SELECT * FROM {tables[0]} FINAL")
        assert ch_client.execute(sql, params) == []
        _score(ch_client, tables, {"text": "approved"}, remapped=remapped)
        assert [str(row[0]) for row in ch_client.execute(sql, params)] == [SESSION_ID]
        _score(ch_client, tables, {"text": "changed"}, version=2, remapped=remapped)
        assert ch_client.execute(sql, params) == []
        assert ch_client.execute(f"SELECT * FROM {tables[0]} FINAL") == source_before
    finally:
        ch_client.execute(f"DROP TABLE {remap_table}")
