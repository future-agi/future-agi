"""Current facts never bypass source qualification or post-collapse predicates."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.derived_metrics.base_list_current import CurrentFactsRead
from tracer.services.derived_metrics.publication import (
    committed_rows_select,
    physical_table,
)
from tracer.services.derived_metrics.storage import (
    LIST_CURRENT_MEMBERS,
    LIST_CURRENT_ROOTS,
    LIST_CURRENT_TABLES,
    LIST_USER_ACTIVITY,
)
from tracer.tests._base_list_parity import (
    FixtureAuthority,
    FixtureCurrentFactsRead,
    assert_sql_and_parameters_equal,
    baseline_fixture,
    baseline_statements,
    filters,
    historical_session_builder,
    native_session_builder,
    parity_hashes,
)

PROJECT = UUID(int=1)
ATTEMPT = UUID(int=2)
MANIFEST = UUID(int=3)


def qualified_read():
    manifest = SimpleNamespace(
        id=MANIFEST,
        project_id=PROJECT,
        remap_revision=4,
        revoked_at=None,
        checkpoint_attempt_id=ATTEMPT,
        allowed_attempts=lambda: ((1, ATTEMPT),),
    )
    return FixtureCurrentFactsRead(
        manifest,
        UUID(int=5),
        FixtureAuthority(
            str(MANIFEST),
            4,
            True,
            True,
            True,
            True,
        ),
    )


def builder(cls=SessionListQueryBuilderV2):
    return cls(
        project_id=str(PROJECT),
        page_size=25,
        filters=filters(
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 1, tzinfo=UTC),
        ),
    )


def test_vendored_baseline_runs_without_git_history(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", "")

    def reject_git(*args, **kwargs):
        raise AssertionError("parity tests must not execute Git")

    monkeypatch.setattr(subprocess, "check_output", reject_git)
    fixture = baseline_fixture()
    expected = fixture["statements"]
    assert baseline_statements(historical_session_builder()) == expected
    # The old recorded fixture remains an exact historical control. Native
    # fallback instead follows the independently pinned dev parent of B1, which
    # intentionally added coverage/null-cost/remap semantics before this seam.
    actual = baseline_statements(SessionListQueryBuilderV2)
    for name, statement in baseline_statements(native_session_builder()).items():
        assert_sql_and_parameters_equal(
            (actual[name]["sql"], actual[name]["params"]),
            (statement["sql"], statement["params"]),
        )


def test_production_current_facts_read_has_no_caller_authority_switch():
    fixture_read = qualified_read()
    read = CurrentFactsRead(fixture_read.manifest, fixture_read.org_id)
    assert not read.permits(PROJECT)
    with pytest.raises(TypeError):
        CurrentFactsRead(
            fixture_read.manifest, fixture_read.org_id, authority=fixture_read.authority
        )
    # Even binding a production read cannot activate any current-fact statement.
    current = builder()
    current.bind_current_facts(read)
    assert not current._current_facts_available()
    for method, args in (
        ("build_candidate_page_query", ()),
        ("build_candidate_count_query", ()),
        ("build_candidate_cursor_page_query", ()),
        ("build_page_hydration_query", ([str(UUID(int=101))],)),
    ):
        assert getattr(current, method)(*args) == getattr(builder(), method)(*args)
    with pytest.raises(ValueError, match="active qualified pin"):
        read.rows()


def test_native_unbound_entry_point_is_identical_to_recorded_baseline():
    current, old = builder(), builder(native_session_builder())
    for method in (
        "build_candidate_page_query",
        "build_candidate_count_query",
        "build_candidate_cursor_page_query",
    ):
        assert_sql_and_parameters_equal(
            getattr(current, method)(), getattr(old, method)()
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("complete_retention_coverage", False),
        ("fresh", False),
        ("unique_physical_versions", False),
        ("anchor_authoritative", False),
        ("remap_revision", 5),
        ("manifest_id", str(UUID(int=99))),
    ],
)
def test_each_unproven_authority_condition_uses_native_sql(field, value):
    read = qualified_read()
    read.authority = replace(read.authority, **{field: value})
    current = builder()
    current.bind_current_facts(read)
    assert (
        current.build_candidate_page_query() == builder().build_candidate_page_query()
    )


def test_released_pin_wrong_project_and_nondefault_filter_use_native():
    read = qualified_read()
    current = builder()
    current.bind_current_facts(read)
    assert current.candidate_slice_narrows_root_scan() is False
    read.active = False
    assert (
        current.build_candidate_count_query() == builder().build_candidate_count_query()
    )
    with pytest.raises(ValueError, match="active qualified pin"):
        read.rows()
    read = qualified_read()
    read.manifest.project_id = UUID(int=101)
    current.bind_current_facts(read)
    assert (
        current.build_candidate_page_query() == builder().build_candidate_page_query()
    )
    current.bind_current_facts(qualified_read())
    current.user_id = "user-detail"
    assert not current._current_facts_available()


def test_qualified_count_has_no_limit_or_mutable_predicate_before_collapse():
    current = builder()
    current.bind_current_facts(qualified_read())
    sql, params = current.build_candidate_count_query()
    assert "list_current_root_facts" in sql
    assert "candidate_root_identities" not in sql
    assert "min(start_time) AS session_start" in sql
    assert "LIMIT %(limit)" not in sql
    assert params["pub_base"] == ((1, ATTEMPT),)
    source, _ = committed_rows_select(
        LIST_CURRENT_ROOTS,
        qualified_read().manifest,
        org_id=UUID(int=5),
        project_id=PROJECT,
    )
    assert "unit_start >=" not in source
    assert "is_deleted = 0" not in source
    assert "LIMIT 1 BY" in source
    for table in LIST_CURRENT_TABLES:
        assert physical_table(table, checkpoint=True) == table
        assert "canonical_session_id" not in table.logical_key
        assert "unit_start" not in table.logical_key
        if table in (LIST_CURRENT_ROOTS, LIST_CURRENT_MEMBERS):
            assert "canonical_user_id" not in table.logical_key
        else:
            # The activity index replaces BOTH keys on a user move; roots and
            # members instead collapse on the native physical span identity.
            assert table == LIST_USER_ACTIVITY
            assert "canonical_user_id" in table.logical_key
        source, _ = committed_rows_select(
            table,
            qualified_read().manifest,
            org_id=UUID(int=5),
            project_id=PROJECT,
        )
        assert "unit_start >=" not in source
        assert "is_deleted = 0" not in source
        assert "LIMIT %(limit)" not in source
        assert "ORDER BY generation DESC, _version DESC LIMIT 1 BY" in source


def test_current_fact_table_roles_and_keys_are_exhaustive():
    # Independent contract, not derived from LIST_CURRENT_KEY: the two physical
    # fact tables must collapse corrections without keeping a stale user/session
    # key. The per-user index deliberately adds the user, so its publisher must
    # atomically retract the old key (covered by the real reassignment test).
    physical_key = (
        "org_id",
        "project_id",
        "observation_type",
        "service_name",
        "start_hour",
        "trace_id",
        "span_id",
    )
    activity_key = (
        "org_id",
        "project_id",
        "canonical_user_id",
        "observation_type",
        "service_name",
        "start_hour",
        "trace_id",
        "span_id",
    )
    roles = {
        "list_current_root_facts": (LIST_CURRENT_ROOTS, physical_key),
        "list_current_members": (LIST_CURRENT_MEMBERS, physical_key),
        "list_current_user_activity": (LIST_USER_ACTIVITY, activity_key),
    }
    assert len(LIST_CURRENT_TABLES) == len(roles)
    assert {table.name for table in LIST_CURRENT_TABLES} == set(roles)
    for table in LIST_CURRENT_TABLES:
        expected_table, expected_key = roles[table.name]
        assert table == expected_table
        assert table.logical_key == expected_key
        assert table.order_by == (*expected_key, "generation", "attempt_id")
        assert physical_table(table, checkpoint=False) == table
        assert physical_table(table, checkpoint=True) == table


def test_float_parity_is_separate_and_exact():
    before = parity_hashes([{"value": 0.1}])
    after = parity_hashes([{"value": 0.10000000000000002}])
    assert before["structural_sha256"] == after["structural_sha256"]
    assert before["float_sha256"] != after["float_sha256"]


@pytest.mark.unit
def test_exact_numbered_count_timeout_keeps_existing_sanitized_failure():
    from inspect import unwrap
    from unittest import mock

    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
    from tracer.tests.test_session_list_bounded_view import (
        _builder_class_mock,
        _view_and_request,
    )
    from tracer.views import trace_session

    view, request = _view_and_request()
    view.request = request
    request.validated_query_data = {
        "project_id": str(PROJECT),
        "filters": [],
        "sort_params": [],
        "page_number": 100,
        "page_size": 25,
    }
    stub = mock.MagicMock()
    stub.supports_candidate_first_page.return_value = True
    stub.build_candidate_page_query.return_value = ("candidate page", {})
    stub.build_candidate_count_query.return_value = ("exact count", {})
    analytics = mock.MagicMock()

    def execute(sql, params, **kwargs):
        if sql == "candidate page":
            return SimpleNamespace(data=[])
        assert sql == "exact count"
        raise ReadDeadlineExceeded("private query text must not escape")

    analytics.execute_ch_query.side_effect = execute
    projects = mock.MagicMock()
    projects.get.return_value = SimpleNamespace(id=str(PROJECT), source="observe")
    with (
        mock.patch.object(
            trace_session, "_project_queryset_for_request", return_value=projects
        ),
        mock.patch.object(
            trace_session, "V2AnalyticsQueryService", return_value=analytics
        ),
        mock.patch.object(
            trace_session,
            "SessionListQueryBuilderV2",
            _builder_class_mock(return_value=stub),
        ),
        mock.patch.object(view, "_build_bookmark_filter", return_value=None),
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
        mock.patch.object(trace_session, "read_bounded_filter_page") as lower_bound,
    ):
        response = unwrap(trace_session.TraceSessionView.list_sessions)(view, request)
    assert response == (
        "error",
        503,
        "Session data is temporarily unavailable. Please retry.",
        "service_unavailable",
    )
    stub.build_candidate_count_query.assert_called_once_with()
    lower_bound.assert_not_called()


def test_source_ceiling_keeps_microseconds_and_rejects_unbounded_key_input():
    from tracer.services.clickhouse.query_builders.base import _unix_microseconds
    from tracer.services.derived_metrics.base_list_current_clickhouse import (
        source_select,
    )

    moment = datetime(2026, 10, 20, 12, 0, 0, 999999, tzinfo=UTC)
    sql, params, ties = source_select(
        org_id=UUID(int=5), project_id=PROJECT, keys=[("trace", "span")], as_of=moment
    )
    assert params["current_as_of_us"] == _unix_microseconds(moment)
    assert "fromUnixTimestamp64Micro(%(current_as_of_us)s, 'UTC')" in sql
    assert "HAVING uniqExact(tuple(" in ties
    assert "parent_span_id = ''" not in ties
    with pytest.raises(ValueError, match="one publication batch"):
        source_select(
            org_id=UUID(int=5),
            project_id=PROJECT,
            keys=[("t", "s")] * 501,
            as_of=moment,
        )


def test_native_hydration_source_seam_preserves_baseline_sql_and_parameters():
    ids = [str(UUID(int=101))]
    sql, params = builder().build_page_hydration_query(ids)
    old_sql, old_params = builder(native_session_builder()).build_page_hydration_query(
        ids
    )
    # CTE extraction changes only lexer whitespace relative to pinned native dev.
    assert_sql_and_parameters_equal((sql, params), (old_sql, old_params))


def test_current_hydration_uses_members_and_loses_access_after_pin_release():
    ids = [str(UUID(int=101))]
    current = builder()
    read = qualified_read()
    current.bind_current_facts(read)
    sql, _ = current.build_page_hydration_query(ids)
    assert "list_current_members" in sql
    assert "FROM spans" not in sql
    assert "candidate_member_identities" not in sql
    assert "member_rollup_rows" in sql
    assert "message_inputs" in sql
    read.active = False
    assert current.build_page_hydration_query(
        ids
    ) == builder().build_page_hydration_query(ids)
