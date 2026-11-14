"""Exact displayed session counts on local CH25 replacement/remap fixtures."""

from datetime import timedelta

import pytest

from tracer.services.users_list_manager import UsersListManager
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine

USER = "33333333-3333-4333-8333-333333333333"
OTHER_USER = "44444444-4444-4444-8444-444444444444"
SESSION_A = "55555555-5555-4555-8555-555555555555"
SESSION_B = "66666666-6666-4666-8666-666666666666"
GROUP = "77777777-7777-4777-8777-777777777777"


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def session_count_engine(request):
    execute, insert = request.getfixturevalue("engine")
    execute("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    execute("""CREATE TABLE end_users (end_user_id UUID, _version UInt64)
        ENGINE=ReplacingMergeTree(_version) ORDER BY end_user_id""")
    execute("""CREATE TABLE trace_session_id_remap (old_id UUID, new_id UUID, _version UInt64)
        ENGINE=ReplacingMergeTree(_version) ORDER BY old_id""")
    execute("INSERT INTO end_users VALUES (%(user)s, 1)", {"user": USER})
    execute(
        "INSERT INTO trace_session_id_remap VALUES (%(a)s,%(group)s,1),(%(b)s,%(group)s,1)",
        {"a": SESSION_A, "b": SESSION_B, "group": GROUP},
    )
    return execute, insert


def count_query(*, workspace=False):
    filters = [time_filter()]
    manager = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        project_id=None if workspace else PROJECT,
        filters=filters,
        requested_columns=["num_sessions"],
        attribute_keys=[],
    )
    subject = manager._exact_candidate_builder(
        candidate_ids=[USER],
        candidate_scan_ids=[USER],
        candidate_end_user_id_map={USER: USER},
        frozen_filters=filters,
    )
    assert manager.approximate_num_sessions is False
    assert subject.embedded_page_metric_fields == frozenset()
    queries = subject.build_requested_page_metric_queries([USER], manager.metric_keys)
    assert len(queries) == 1
    return queries[0][:2]


@pytest.mark.integration
@pytest.mark.parametrize("workspace", [False, True])
def test_display_count_folds_aliases_and_excludes_foreign_project(
    session_count_engine, workspace
):
    execute, insert = session_count_engine
    insert(id="a", end_user_id=USER, trace_session_id=SESSION_A)
    insert(id="b", end_user_id=USER, trace_session_id=SESSION_B)
    insert(
        id="foreign",
        project_id=OTHER_PROJECT,
        end_user_id=USER,
        trace_session_id=OTHER_USER,
    )
    assert execute(*count_query(workspace=workspace)) == [
        {"end_user_id": USER, "num_sessions": 1}
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "replacement",
    [
        {"is_deleted": 1},
        {"end_user_id": OTHER_USER},
        {"trace_session_id": None},
    ],
)
def test_display_count_replays_latest_tombstone_user_and_session_membership(
    session_count_engine, replacement
):
    execute, insert = session_count_engine
    insert(id="a", end_user_id=USER, trace_session_id=SESSION_A)
    insert(
        **{
            "id": "a",
            "end_user_id": USER,
            "trace_session_id": SESSION_A,
            "_version": 2,
            "start_time": START + timedelta(minutes=10),
            **replacement,
        }
    )
    assert execute(*count_query()) == []
