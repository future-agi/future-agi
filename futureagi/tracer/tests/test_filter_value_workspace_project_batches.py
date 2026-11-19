"""Workspace-scale filter-value cursor coverage without an unbounded project list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from clickhouse_connect.driver.exceptions import (
    DatabaseError as ConnectDatabaseError,
)
from clickhouse_connect.driver.exceptions import (
    OperationalError as ConnectOperationalError,
)
from clickhouse_driver.errors import ErrorCodes
from clickhouse_driver.errors import NetworkError as DriverNetworkError
from clickhouse_driver.errors import ServerException as DriverServerException
from clickhouse_driver.errors import SocketTimeoutError as DriverSocketTimeoutError
from structlog.testing import capture_logs

from tracer.services.annotation_label_source import (
    AnnotationLabelScoresProjectPG,
)
from tracer.services.clickhouse import attribute_reads
from tracer.services.clickhouse.attribute_cursor_state import AttributeCursorSeenState
from tracer.services.clickhouse.attribute_reads import (
    ATTRIBUTE_READ_MAX_PROJECTS,
    AttributeReadMetadata,
    AttributeValueCursorPageRead,
    AttributeValueRow,
    attribute_value_cursor_digest,
)
from tracer.services.clickhouse.filter_value_reads import (
    FilterValueCursorPageRead,
    FilterValueRead,
    SessionFilterValueCursorPageRead,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.views import dashboard as dashboard_view

_REAL_RETAINED_WINDOW_START = attribute_reads.AttributeReadSelector.__dict__[
    "retained_window_start"
]


def _uuid(index: int) -> str:
    return str(UUID(int=index))


class _BoundedRows:
    def __init__(self, rows, slice_limits):
        self.rows = list(rows)
        self.slice_limits = slice_limits

    def __getitem__(self, item):
        if isinstance(item, slice):
            self.slice_limits.append(item.stop)
        return self.rows[item]

    def __iter__(self):
        return iter(self.rows)


class _ProjectQuery:
    """Small immutable QuerySet double with a shared mutable live-id source."""

    def __init__(self, live_ids, slice_limits, *, id_in=None, id_gt=None):
        self.live_ids = live_ids
        self.slice_limits = slice_limits
        self.id_in = None if id_in is None else set(id_in)
        self.id_gt = id_gt

    def _clone(self, *, id_in=None, id_gt=None):
        return _ProjectQuery(
            self.live_ids,
            self.slice_limits,
            id_in=self.id_in if id_in is None else id_in,
            id_gt=self.id_gt if id_gt is None else id_gt,
        )

    def filter(self, **kwargs):
        return self._clone(
            id_in=(
                {str(value) for value in kwargs["id__in"]}
                if "id__in" in kwargs
                else None
            ),
            id_gt=str(kwargs["id__gt"]) if "id__gt" in kwargs else None,
        )

    def order_by(self, *_args):
        return self

    def values_list(self, *_args, **_kwargs):
        rows = sorted(str(value) for value in self.live_ids)
        if self.id_in is not None:
            rows = [value for value in rows if value in self.id_in]
        if self.id_gt is not None:
            rows = [value for value in rows if value > self.id_gt]
        return _BoundedRows(rows, self.slice_limits)


def _request(params):
    organization = SimpleNamespace(pk=_uuid(10_001))
    workspace = SimpleNamespace(pk=_uuid(10_002), id=_uuid(10_002))
    return SimpleNamespace(
        validated_query_data=params,
        user=SimpleNamespace(pk=_uuid(10_003), organization=organization),
        organization=organization,
        workspace=workspace,
        auth=None,
    )


RETAINED_SPAN_AGE = timedelta(days=3)


def _install_scope_and_seen_state(monkeypatch, projects: _ProjectQuery):
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: projects._clone(),
    )
    # The span_system lane bounds a first workspace page at the retained
    # spans boundary (ClickHouse part metadata); keep these tests storage-free.
    monkeypatch.setattr(
        dashboard_view.AttributeReadSelector,
        "retained_window_start",
        lambda _self, _project_ids, *, window_end: window_end - RETAINED_SPAN_AGE,
    )
    monkeypatch.setattr(
        dashboard_view,
        "_run_filter_value_pg_read",
        lambda _deadline, read: read(),
    )

    def load_seen(reference, **_kwargs):
        return AttributeCursorSeenState(tuple(reference), state_id=None)

    def persist_seen(prior, appended, **_kwargs):
        return (*prior.digests, *tuple(appended))

    monkeypatch.setattr(
        dashboard_view,
        "load_attribute_cursor_seen_state",
        load_seen,
    )
    monkeypatch.setattr(
        dashboard_view,
        "persist_attribute_cursor_seen_state",
        persist_seen,
    )


def _invoke(params):
    return dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(),
        _request(params),
    )


def _result(response):
    assert response.status_code == 200
    return response.data["result"]


@pytest.mark.unit
def test_workspace_session_search_finishes_after_bounded_project_batches(
    monkeypatch,
):
    project_ids = [_uuid(index) for index in range(1, 66)]
    projects = _ProjectQuery(project_ids, [])
    _install_scope_and_seen_state(monkeypatch, projects)
    session_id = _uuid(90_001)
    observed_batches = []

    def read_page(_analytics, **kwargs):
        observed_batches.append(tuple(kwargs["project_ids"]))
        values = (session_id,) if len(observed_batches) == 1 else ()
        return SessionFilterValueCursorPageRead(
            values,
            False,
            None,
            "exhausted",
        )

    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        dashboard_view,
        "read_session_filter_value_cursor_page",
        read_page,
    )
    monkeypatch.setattr(
        dashboard_view,
        "_session_overlay_filter_value_ids",
        lambda **_kwargs: (),
    )

    from tracer.services.clickhouse.v2 import trace_session_dict_reader

    monkeypatch.setattr(
        trace_session_dict_reader,
        "resolve_session_fields",
        lambda values, **_kwargs: {
            value: {"external_session_id": "matching-session"} for value in values
        },
    )
    params = {
        "metric_name": "session",
        "metric_type": "system_metric",
        "source": "sessions",
        "project_ids": [],
        "search": "matching-session",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert first["values"] == [{"value": session_id, "label": "matching-session"}]
    assert first["has_more"] is True
    assert second["values"] == []
    assert second["has_more"] is False
    assert second["browse_status"] == "exhausted"
    assert second["next_cursor"] is None
    assert [len(batch) for batch in observed_batches] == [64, 1]


@pytest.mark.unit
def test_workspace_custom_values_walk_later_batch_without_duplicate(monkeypatch):
    all_project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(all_project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    observed_batches = []

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_value_cursor_page(self, batch, key, **kwargs):
            observed_batches.append(tuple(batch))
            candidates = ["shared"]
            if all_project_ids[-1] in batch:
                candidates.append("later-only")
            needle = str(kwargs.get("search") or "").casefold()
            candidates = [
                value
                for value in candidates
                if (not needle or needle in value.casefold())
                and not kwargs["seen_value_contains"](
                    attribute_value_cursor_digest("string", value)
                )
            ]
            appended = tuple(
                attribute_value_cursor_digest("string", value) for value in candidates
            )
            metadata = AttributeReadMetadata(
                True,
                "complete",
                None,
                kwargs["window_start"],
                kwargs["window_end"],
                1,
            )
            return AttributeValueCursorPageRead(
                tuple(AttributeValueRow(value, "string", 1) for value in candidates),
                metadata,
                False,
                kwargs["window_start"],
                None,
                None,
                0,
                appended,
                appended_value_digests=appended,
                seen_value_count=kwargs["seen_value_count"] + len(appended),
            )

    monkeypatch.setattr(dashboard_view, "AttributeReadSelector", Selector)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    params = {
        "metric_name": "customer.plan",
        "metric_type": "custom_attribute",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert [item["value"] for item in first["values"]] == ["shared"]
    assert [item["value"] for item in second["values"]] == ["later-only"]
    assert first["browse_status"] == "continuation"
    assert second["browse_status"] == "exhausted"
    assert [len(batch) for batch in observed_batches] == [64, 1]
    assert all(limit <= ATTRIBUTE_READ_MAX_PROJECTS + 1 for limit in slice_limits)


@pytest.mark.unit
def test_workspace_custom_values_keep_equal_values_with_distinct_types(monkeypatch):
    project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_value_cursor_page(self, batch, _key, **kwargs):
            value_type = "number" if project_ids[-1] not in batch else "array"
            value = 1
            digest = attribute_value_cursor_digest(value_type, value)
            rows = ()
            appended = ()
            if not kwargs["seen_value_contains"](digest):
                rows = (AttributeValueRow(value, value_type, 1),)
                appended = (digest,)
            metadata = AttributeReadMetadata(
                True,
                "complete",
                None,
                kwargs["window_start"],
                kwargs["window_end"],
                1,
            )
            return AttributeValueCursorPageRead(
                rows,
                metadata,
                False,
                kwargs["window_start"],
                None,
                None,
                0,
                appended,
                appended_value_digests=appended,
                seen_value_count=kwargs["seen_value_count"] + len(appended),
            )

    monkeypatch.setattr(dashboard_view, "AttributeReadSelector", Selector)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    params = {
        "metric_name": "customer.sequence",
        "metric_type": "custom_attribute",
        "source": "traces",
        "project_ids": [],
        "search": "1",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert [(item["value"], item["type"]) for item in first["values"]] == [
        (1, "number")
    ]
    assert [(item["value"], item["type"]) for item in second["values"]] == [
        (1, "array")
    ]
    assert second["browse_status"] == "exhausted"


@pytest.mark.unit
def test_workspace_voice_search_reaches_later_project_batch(monkeypatch):
    all_project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(all_project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    observed_batches = []
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)

    def read_page(_analytics, *, project_ids, **kwargs):
        observed_batches.append(tuple(project_ids))
        candidates = ["shared-ending"]
        if all_project_ids[-1] in project_ids:
            candidates.extend(["shared-ending", "later-customer-ending"])
        needle = str(kwargs.get("search") or "").casefold()
        values = tuple(
            value
            for value in candidates
            if (not needle or needle in value.casefold())
            and not kwargs["seen_value_contains"](
                dashboard_view._filter_value_digest(value)
            )
        )
        appended = tuple(dashboard_view._filter_value_digest(value) for value in values)
        return FilterValueCursorPageRead(
            values,
            kwargs["window_start"],
            kwargs["window_end"],
            False,
            kwargs["window_start"],
            None,
            None,
            appended,
            "exhausted",
            appended,
            kwargs["seen_value_count"] + len(appended),
        )

    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_page,
    )
    params = {
        "metric_name": "ended_reason",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [],
        "search": "later-customer",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert first["values"] == []
    assert first["query_complete"] is True
    assert first["has_more"] is True
    assert second["values"] == [
        {"value": "later-customer-ending", "label": "later-customer-ending"}
    ]
    assert [len(batch) for batch in observed_batches] == [64, 1]
    assert all(limit <= ATTRIBUTE_READ_MAX_PROJECTS + 1 for limit in slice_limits)


@pytest.mark.unit
def test_workspace_project_label_search_reaches_later_project_batch(monkeypatch):
    project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)

    def read_page(_analytics, **kwargs):
        value = str(kwargs["project_ids"][-1])
        digest = dashboard_view._filter_value_digest(value)
        return FilterValueCursorPageRead(
            (value,),
            kwargs["window_start"],
            kwargs["window_end"],
            False,
            kwargs["window_start"],
            None,
            None,
            (digest,),
            "exhausted",
            (digest,),
            kwargs["seen_value_count"] + 1,
        )

    class ProjectNames:
        def __init__(self):
            self.ids = ()

        def filter(self, **kwargs):
            self.ids = tuple(str(value) for value in kwargs["id__in"])
            return self

        def values_list(self, *_args):
            return [
                (
                    project_id,
                    (
                        "Needle project"
                        if project_id == project_ids[-1]
                        else "Unrelated project"
                    ),
                )
                for project_id in self.ids
            ]

    monkeypatch.setattr(
        dashboard_view,
        "Project",
        SimpleNamespace(objects=ProjectNames()),
    )
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_page,
    )
    params = {
        "metric_name": "project",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [],
        "search": "needle",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert first["values"] == []
    assert first["has_more"] is True
    assert second["values"] == [{"value": project_ids[-1], "label": "Needle project"}]
    assert second["has_more"] is False


@pytest.mark.unit
def test_deleted_only_later_batch_terminates_without_physical_read(monkeypatch):
    project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    physical_reads = []

    def read_page(_analytics, **kwargs):
        physical_reads.append(tuple(kwargs["project_ids"]))
        return FilterValueCursorPageRead(
            (),
            kwargs["window_start"],
            kwargs["window_end"],
            False,
            kwargs["window_start"],
            None,
            None,
            (),
            "exhausted",
        )

    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_page,
    )
    params = {
        "metric_name": "ended_reason",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }
    first = _result(_invoke(params))
    assert first["has_more"] is True
    assert len(physical_reads) == 1

    projects.live_ids.remove(project_ids[-1])
    terminal = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert terminal["values"] == []
    assert terminal["query_complete"] is True
    assert terminal["has_more"] is False
    assert terminal["browse_status"] == "exhausted"
    assert terminal["next_cursor"] is None
    assert len(physical_reads) == 1
    assert all(limit <= ATTRIBUTE_READ_MAX_PROJECTS + 1 for limit in slice_limits)


@pytest.mark.unit
def test_workspace_annotators_deduplicate_across_project_batches(monkeypatch):
    project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    common_id = _uuid(20_001)
    later_id = _uuid(20_002)
    observed_batches = []

    def read_annotators(_self, batch, **_kwargs):
        observed_batches.append(tuple(batch))
        users = [{"id": common_id, "name": "Common", "email": ""}]
        if project_ids[-1] in batch:
            users.append({"id": later_id, "name": "Later", "email": ""})
        return users, False

    monkeypatch.setattr(
        AnnotationLabelScoresProjectPG,
        "annotator_page_for_projects",
        read_annotators,
    )
    params = {
        "metric_name": "annotator",
        "metric_type": "annotation_metric",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert [item["value"] for item in first["values"]] == [common_id]
    assert [item["value"] for item in second["values"]] == [later_id]
    assert [len(batch) for batch in observed_batches] == [64, 1]
    assert all(limit <= ATTRIBUTE_READ_MAX_PROJECTS + 1 for limit in slice_limits)


@pytest.mark.unit
def test_workspace_cursor_reauthorizes_embedded_batch_and_rejects_mismatch(
    monkeypatch,
):
    project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    window_end = datetime.now(UTC)

    def read_page(_analytics, **kwargs):
        value = "first"
        digest = dashboard_view._filter_value_digest(value)
        return FilterValueCursorPageRead(
            (value,),
            kwargs["window_start"],
            kwargs["window_end"],
            True,
            window_end - timedelta(minutes=5),
            window_end - timedelta(minutes=10),
            value,
            (digest,),
            "continuation",
            (digest,),
            1,
        )

    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_page,
    )
    params = {
        "metric_name": "ended_reason",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }
    first = _result(_invoke(params))

    projects.live_ids.remove(project_ids[0])
    deleted_response = _invoke({**params, "cursor": first["next_cursor"]})
    assert deleted_response.status_code == 400
    assert deleted_response.data["code"] == "cursor_mismatch"

    mismatched_response = _invoke(
        {**params, "search": "different", "cursor": first["next_cursor"]}
    )
    assert mismatched_response.status_code == 400
    assert mismatched_response.data["code"] == "cursor_mismatch"

    tampered_response = _invoke({**params, "cursor": f"{first['next_cursor']}tampered"})
    assert tampered_response.status_code == 400
    assert tampered_response.data["code"] == "invalid_cursor"
    assert all(limit <= ATTRIBUTE_READ_MAX_PROJECTS + 1 for limit in slice_limits)


@pytest.mark.unit
def test_workspace_legacy_values_report_first_batch_as_sampled(monkeypatch):
    project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)

    def read_values(_analytics, **_kwargs):
        now = datetime.now(UTC)
        return FilterValueRead(("sample",), True, None, now - timedelta(days=7), now)

    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_values",
        read_values,
    )
    result = _result(
        _invoke(
            {
                "metric_name": "ended_reason",
                "metric_type": "system_metric",
                "source": "traces",
                "project_ids": [],
                "search": "",
            }
        )
    )

    assert result["values"] == [{"value": "sample", "label": "sample"}]
    assert result["query_complete"] is False
    assert result["query_status"] == "sampled"
    assert result["query_error_code"] == "sample_limit"
    assert result["browse_status"] == "limit_reached"
    assert result["has_more"] is False
    assert all(limit <= ATTRIBUTE_READ_MAX_PROJECTS + 1 for limit in slice_limits)


@pytest.mark.unit
def test_large_explicit_scope_authorizes_bounded_order_independent_batches(
    monkeypatch,
):
    requested = [_uuid(index) for index in range(1, 71)]
    foreign_id = requested[5]
    live_ids = [project_id for project_id in requested if project_id != foreign_id]
    slice_limits = []
    projects = _ProjectQuery(live_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)

    scope = dashboard_view._prepare_filter_value_project_scope(
        _request({}),
        list(reversed(requested)),
        deadline=SimpleNamespace(),
        cursor_token=None,
    )

    assert scope.mode == "explicit"
    assert len(scope.project_ids) == ATTRIBUTE_READ_MAX_PROJECTS - 1
    assert foreign_id not in scope.project_ids
    assert scope.has_later_projects is True
    assert scope.requested_project_ids == tuple(sorted(requested))
    assert all(limit <= ATTRIBUTE_READ_MAX_PROJECTS + 1 for limit in slice_limits)


@pytest.mark.unit
def test_thousand_foreign_explicit_projects_advance_one_pg_chunk_per_gesture(
    monkeypatch,
):
    requested = [_uuid(index) for index in range(1, 1_001)]
    slice_limits = []
    projects = _ProjectQuery([], slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        lambda *_args, **_kwargs: pytest.fail(
            "an empty authorized chunk must not issue a physical read"
        ),
    )
    params = {
        "metric_name": "ended_reason",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": requested,
        "search": "",
        "page_size": 10,
    }

    cursor = None
    gestures = 0
    while True:
        before_queries = len(slice_limits)
        result = _result(_invoke({**params, **({"cursor": cursor} if cursor else {})}))
        gestures += 1
        assert len(slice_limits) == before_queries + 1
        assert slice_limits[-1] == ATTRIBUTE_READ_MAX_PROJECTS + 1
        assert result["values"] == []
        assert result["query_complete"] is True
        cursor = result["next_cursor"]
        if cursor is None:
            assert result["has_more"] is False
            assert result["browse_status"] == "exhausted"
            break
        assert result["has_more"] is True
        assert result["browse_status"] == "continuation"

    assert gestures == 16


@pytest.mark.unit
def test_foreign_first_explicit_chunk_can_reach_valid_later_project(monkeypatch):
    requested = [_uuid(index) for index in range(1, 66)]
    valid_project_id = requested[-1]
    slice_limits = []
    projects = _ProjectQuery([valid_project_id], slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    physical_batches = []

    def read_page(_analytics, **kwargs):
        physical_batches.append(tuple(kwargs["project_ids"]))
        value = "later-value"
        digest = dashboard_view._filter_value_digest(value)
        return FilterValueCursorPageRead(
            (value,),
            kwargs["window_start"],
            kwargs["window_end"],
            False,
            kwargs["window_start"],
            None,
            None,
            (digest,),
            "exhausted",
            (digest,),
            1,
        )

    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_page,
    )
    params = {
        "metric_name": "ended_reason",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": requested,
        "search": "later",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert first["values"] == []
    assert first["has_more"] is True
    assert second["values"] == [{"value": "later-value", "label": "later-value"}]
    assert second["has_more"] is False
    assert physical_batches == [(valid_project_id,)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("metric_type", "metric_name"),
    [
        ("system_metric", "ended_reason"),
        ("custom_attribute", "customer.plan"),
        ("annotation_metric", "annotator"),
    ],
)
def test_empty_batched_legacy_scope_is_sampled_without_physical_read(
    monkeypatch,
    metric_type,
    metric_name,
):
    requested = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery([requested[-1]], slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_values",
        lambda *_args, **_kwargs: pytest.fail("unexpected system read"),
    )
    monkeypatch.setattr(
        dashboard_view,
        "AttributeReadSelector",
        lambda *_args, **_kwargs: pytest.fail("unexpected attribute read"),
    )
    monkeypatch.setattr(
        AnnotationLabelScoresProjectPG,
        "annotator_ids_for_projects",
        lambda *_args, **_kwargs: pytest.fail("unexpected Score read"),
    )

    result = _result(
        _invoke(
            {
                "metric_name": metric_name,
                "metric_type": metric_type,
                "source": "traces",
                "project_ids": requested,
                "search": "",
            }
        )
    )

    assert result["values"] == []
    assert result["query_complete"] is False
    assert result["query_status"] == "sampled"
    assert result["browse_status"] == "limit_reached"
    assert result["next_cursor"] is None
    assert slice_limits == [ATTRIBUTE_READ_MAX_PROJECTS + 1]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("metric_type", "metric_name"),
    [
        ("system_metric", "ended_reason"),
        ("custom_attribute", "customer.plan"),
        ("annotation_metric", "annotator"),
    ],
)
def test_fixed_all_foreign_scope_completes_empty_without_physical_read(
    monkeypatch,
    metric_type,
    metric_name,
):
    slice_limits = []
    projects = _ProjectQuery([], slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        lambda *_args, **_kwargs: pytest.fail("unexpected system read"),
    )
    monkeypatch.setattr(
        dashboard_view,
        "AttributeReadSelector",
        lambda *_args, **_kwargs: pytest.fail("unexpected attribute read"),
    )
    monkeypatch.setattr(
        AnnotationLabelScoresProjectPG,
        "annotator_page_for_projects",
        lambda *_args, **_kwargs: pytest.fail("unexpected Score read"),
    )

    result = _result(
        _invoke(
            {
                "metric_name": metric_name,
                "metric_type": metric_type,
                "source": "traces",
                "project_ids": [_uuid(40_001)],
                "search": "",
                "page_size": 10,
            }
        )
    )

    assert result["values"] == []
    assert result["query_complete"] is True
    assert result["has_more"] is False
    assert result["browse_status"] == "exhausted"


class _FirstQuery:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        return self

    def select_related(self, *_args):
        return self

    def first(self):
        return self.result


class _ConditionalQuery:
    def __init__(self, resolver, calls, filters=None):
        self.resolver = resolver
        self.calls = calls
        self.filters = dict(filters or {})

    def filter(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _ConditionalQuery(
            self.resolver,
            self.calls,
            {**self.filters, **kwargs},
        )

    def select_related(self, *_args):
        return self

    def first(self):
        return self.resolver(self.filters)


@pytest.mark.unit
def test_workspace_configured_eval_authorizes_target_beyond_first_batch(monkeypatch):
    project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    eval_template = SimpleNamespace(
        config={"output": "choices"},
        choices=["Later choice"],
    )
    eval_config = SimpleNamespace(
        project_id=project_ids[-1],
        eval_template=eval_template,
    )
    query = _FirstQuery(eval_config)
    monkeypatch.setattr(
        dashboard_view.CustomEvalConfig,
        "no_workspace_objects",
        query,
    )
    params = {
        "metric_name": _uuid(30_001),
        "metric_type": "eval_metric",
        "source": "traces",
        "project_ids": [],
        "search": "later",
        "page_size": 10,
    }

    result = _result(_invoke(params))

    assert result["values"] == [{"value": "Later choice", "label": "Later choice"}]
    assert result["query_complete"] is True
    assert any(
        kwargs.get("project__deleted") is False
        for name, _args, kwargs in query.calls
        if name == "filter"
    )


@pytest.mark.unit
def test_large_explicit_template_eval_reaches_config_in_later_project_batch(
    monkeypatch,
):
    requested = [_uuid(index) for index in range(1, 66)]
    later_project_id = requested[-1]
    slice_limits = []
    projects = _ProjectQuery([later_project_id], slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    template_id = _uuid(32_001)
    eval_template = SimpleNamespace(
        config={"output": "choices"},
        choices=["Later template choice"],
    )
    eval_config = SimpleNamespace(
        project_id=later_project_id,
        eval_template=eval_template,
    )
    query_calls = []

    def resolve(filters):
        if filters.get("id") == template_id:
            return None
        if filters.get("eval_template_id") != template_id:
            return None
        if later_project_id in {
            str(value) for value in filters.get("project_id__in", ())
        }:
            return eval_config
        return None

    monkeypatch.setattr(
        dashboard_view.CustomEvalConfig,
        "no_workspace_objects",
        _ConditionalQuery(resolve, query_calls),
    )
    params = {
        "metric_name": template_id,
        "metric_type": "eval_metric",
        "source": "traces",
        "project_ids": requested,
        "search": "later",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert first["values"] == []
    assert first["has_more"] is True
    assert second["values"] == [
        {"value": "Later template choice", "label": "Later template choice"}
    ]
    assert second["has_more"] is False
    template_batches = [
        kwargs["project_id__in"]
        for _args, kwargs in query_calls
        if kwargs.get("eval_template_id") == template_id and "project_id__in" in kwargs
    ]
    assert template_batches == [[later_project_id]]
    assert all(len(batch) <= ATTRIBUTE_READ_MAX_PROJECTS for batch in template_batches)


@pytest.mark.unit
def test_workspace_configured_annotation_authorizes_target_beyond_first_batch(
    monkeypatch,
):
    from model_hub.models.develop_annotations import AnnotationsLabels

    project_ids = [_uuid(index) for index in range(1, 66)]
    slice_limits = []
    projects = _ProjectQuery(project_ids, slice_limits)
    _install_scope_and_seen_state(monkeypatch, projects)
    label = SimpleNamespace(
        id=_uuid(31_001),
        project_id=project_ids[-1],
        type="star",
        settings={"no_of_stars": 2},
    )
    query = _FirstQuery(label)
    monkeypatch.setattr(
        AnnotationsLabels,
        "no_workspace_objects",
        query,
    )
    params = {
        "metric_name": str(label.id),
        "metric_type": "annotation_metric",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }

    result = _result(_invoke(params))

    assert [option["value"] for option in result["values"]] == ["1", "2"]
    assert result["query_complete"] is True
    assert any(
        kwargs.get("deleted") is False
        for name, _args, kwargs in query.calls
        if name == "filter"
    )


@pytest.mark.unit
def test_workspace_categorical_annotation_uses_only_configured_values(monkeypatch):
    from model_hub.models.develop_annotations import AnnotationsLabels

    project_ids = [_uuid(index) for index in range(1, 66)]
    projects = _ProjectQuery(project_ids, [])
    _install_scope_and_seen_state(monkeypatch, projects)
    label = SimpleNamespace(
        id=_uuid(33_001),
        project_id=project_ids[-1],
        type="categorical",
        settings={"options": ["Configured", "Second"]},
    )
    monkeypatch.setattr(
        AnnotationsLabels,
        "no_workspace_objects",
        _FirstQuery(label),
    )
    monkeypatch.setattr(
        AnnotationLabelScoresProjectPG,
        "categorical_values_for_label",
        lambda *_args, **_kwargs: pytest.fail("configured values must not scan Score"),
        raising=False,
    )

    result = _result(
        _invoke(
            {
                "metric_name": str(label.id),
                "metric_type": "annotation_metric",
                "source": "traces",
                "project_ids": [],
                "search": "",
                "page_size": 10,
            }
        )
    )

    assert result["values"] == [
        {"value": "Configured", "label": "Configured"},
        {"value": "Second", "label": "Second"},
    ]
    assert result["query_complete"] is True
    assert result["browse_status"] == "exhausted"
    assert result["has_more"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("retained_age", "expected_age"),
    [
        (RETAINED_SPAN_AGE, RETAINED_SPAN_AGE),
        # No active span part before the window: an empty interval, not 1970.
        (None, timedelta(microseconds=1)),
    ],
)
def test_workspace_span_system_walk_starts_at_retained_span_boundary(
    monkeypatch, retained_age, expected_age
):
    """A workspace Project picker must not walk empty slices back to 1970.

    The fixed-scope lane already starts at the retained-data boundary; the
    batched workspace lane used the Unix epoch, which made the picker issue
    dozens of sequential empty continuation requests per chain.
    """

    project_ids = [_uuid(index) for index in range(1, 3)]
    projects = _ProjectQuery(project_ids, [])
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(
        dashboard_view.AttributeReadSelector,
        "retained_window_start",
        lambda _self, _project_ids, *, window_end: (
            None if retained_age is None else window_end - retained_age
        ),
    )
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    windows = []

    def read_page(_analytics, **kwargs):
        windows.append((kwargs["window_start"], kwargs["window_end"]))
        return FilterValueCursorPageRead(
            (),
            kwargs["window_start"],
            kwargs["window_end"],
            True,
            kwargs["window_end"] - timedelta(minutes=5),
            None,
            None,
            (),
            "continuation",
        )

    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_page,
    )
    monkeypatch.setattr(
        dashboard_view,
        "Project",
        SimpleNamespace(
            objects=SimpleNamespace(
                filter=lambda **_kwargs: SimpleNamespace(values_list=lambda *_args: [])
            )
        ),
    )
    params = {
        "metric_name": "project",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }

    first = _result(_invoke(params))
    if retained_age is not None:
        assert first["next_cursor"]
        _result(_invoke({**params, "cursor": first["next_cursor"]}))
        assert len(windows) == 2

    assert windows
    start, end = windows[0]
    assert start > datetime(1971, 1, 1, tzinfo=UTC)
    assert end - start == expected_age
    # The continuation resumes the frozen window from the signed cursor.
    assert all(window == windows[0] for window in windows)


# Text a ClickHouse error may echo back; it must never reach the logs.
_LEAKY_DETAIL = "SELECT min(min_time) FROM system.parts WHERE secret-value-42"


def _executor_raising(exc):
    class _Executor:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute(self, *_args, **_kwargs):
            raise exc

    return _Executor


class _UnavailableClientExecutor:
    def __init__(self, *_args, **_kwargs):
        raise DriverNetworkError(f"connect refused: {_LEAKY_DETAIL}")


class _EmptyResultExecutor:
    """Returns an envelope the selector rejects as IncompleteLatestStateReplay."""

    def __init__(self, *_args, **_kwargs):
        pass

    def execute(self, *_args, **_kwargs):
        return None


@pytest.mark.unit
@pytest.mark.parametrize(
    "executor",
    [
        pytest.param(
            _executor_raising(DriverNetworkError(_LEAKY_DETAIL)),
            id="driver-network-error",
        ),
        pytest.param(
            _executor_raising(DriverSocketTimeoutError(_LEAKY_DETAIL)),
            id="driver-socket-timeout",
        ),
        pytest.param(
            _executor_raising(
                DriverServerException(_LEAKY_DETAIL, code=ErrorCodes.UNKNOWN_TABLE)
            ),
            id="driver-server-exception",
        ),
        pytest.param(
            _executor_raising(
                DriverServerException(_LEAKY_DETAIL, code=ErrorCodes.TIMEOUT_EXCEEDED)
            ),
            id="driver-server-timeout",
        ),
        pytest.param(
            _executor_raising(ConnectOperationalError(_LEAKY_DETAIL)),
            id="connect-operational-error",
        ),
        pytest.param(
            _executor_raising(ConnectDatabaseError(_LEAKY_DETAIL)),
            id="connect-database-error",
        ),
        pytest.param(
            _executor_raising(ReadDeadlineExceeded(_LEAKY_DETAIL)),
            id="read-deadline-exceeded",
        ),
        pytest.param(
            _executor_raising(TimeoutError(_LEAKY_DETAIL)),
            id="builtin-timeout",
        ),
        pytest.param(
            _executor_raising(ConnectionResetError(_LEAKY_DETAIL)),
            id="connection-reset",
        ),
        pytest.param(
            _executor_raising(RuntimeError(_LEAKY_DETAIL)),
            id="unexpected-client-error",
        ),
        pytest.param(_EmptyResultExecutor, id="incomplete-latest-state-replay"),
        pytest.param(_UnavailableClientExecutor, id="client-unavailable"),
    ],
)
def test_workspace_span_system_retained_lookup_failure_falls_back_to_epoch(
    monkeypatch, executor
):
    """A failed retained-part lookup must not fail the workspace picker.

    It is only an optimisation of the first page's lower bound, so any error
    from it falls back to the pre-floor walk from 1970 and logs one sanitized
    warning.
    """

    project_ids = [_uuid(index) for index in range(1, 3)]
    projects = _ProjectQuery(project_ids, [])
    _install_scope_and_seen_state(monkeypatch, projects)
    # Run the real selector lookup against a client that fails.
    monkeypatch.setattr(
        attribute_reads.AttributeReadSelector,
        "retained_window_start",
        _REAL_RETAINED_WINDOW_START,
    )
    monkeypatch.setattr(attribute_reads, "V2AttributeQueryExecutor", executor)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    windows = []

    def read_page(_analytics, **kwargs):
        windows.append((kwargs["window_start"], kwargs["window_end"]))
        return FilterValueCursorPageRead(
            (),
            kwargs["window_start"],
            kwargs["window_end"],
            True,
            kwargs["window_end"] - timedelta(minutes=5),
            None,
            None,
            (),
            "continuation",
        )

    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        read_page,
    )
    monkeypatch.setattr(
        dashboard_view,
        "Project",
        SimpleNamespace(
            objects=SimpleNamespace(
                filter=lambda **_kwargs: SimpleNamespace(values_list=lambda *_args: [])
            )
        ),
    )
    params = {
        "metric_name": "project",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }

    with capture_logs() as logs:
        first = _result(_invoke(params))

    assert first["next_cursor"]
    assert len(windows) == 1
    assert windows[0][0] == datetime(1970, 1, 1, tzinfo=UTC)
    # The continuation keeps the epoch window from the signed cursor.
    _result(_invoke({**params, "cursor": first["next_cursor"]}))
    assert windows[1] == windows[0]

    # Exactly one warning: the selector's own read-budget warning, or the
    # picker's fallback warning for every other failure class.
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert [entry["event"] for entry in warnings] in (
        ["attribute_retained_window_metadata_budget_exceeded"],
        ["dashboard_filter_value_retained_window_lookup_failed"],
    )
    assert "secret-value-42" not in repr(logs)
    assert "system.parts" not in repr(logs)


@pytest.mark.unit
@pytest.mark.parametrize("fallback", [False, True], ids=["retained", "epoch"])
def test_workspace_page_keeps_read_budget_and_scope(monkeypatch, fallback):
    from tracer.services.clickhouse import client as native_client
    from tracer.services.clickhouse import read_budget
    from tracer.services.clickhouse.v2 import query_service

    now = [0.0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(read_budget, "time", clock)
    monkeypatch.setattr(native_client, "time", clock)
    monkeypatch.setattr(dashboard_view, "_FILTER_VALUES_INTERACTIVE_TIMEOUT_MS", 1450)
    owners = {_uuid(201): _uuid(301), _uuid(202): _uuid(302)}
    _install_scope_and_seen_state(monkeypatch, _ProjectQuery([], []))
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda request: _ProjectQuery([owners[str(request.workspace.pk)]], []),
    )
    monkeypatch.setattr(
        attribute_reads.AttributeReadSelector,
        "retained_window_start",
        _REAL_RETAINED_WINDOW_START,
    )
    calls = []

    class Driver:
        connection = SimpleNamespace(connected=True, send_receive_timeout=30.0)

        def execute(self, query, params, *, settings, with_column_types):
            if "system.parts" not in query:
                return [], []
            seconds = self.connection.send_receive_timeout
            calls.append((seconds, settings["max_execution_time"]))
            now[0] += seconds * 0.9
            if fallback:
                raise DriverNetworkError(_LEAKY_DETAIL)
            return [(datetime(2020, 1, 1, tzinfo=UTC),)], [
                ("retained_start", "DateTime")
            ]

    client = native_client.ClickHouseClient(server_enforced_readonly=False)
    monkeypatch.setattr(client, "_create_client", lambda: Driver())
    monkeypatch.setattr(query_service, "get_v2_query_client", lambda: client)
    reads = []

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert "PREWHERE project_id IN %(project_ids)s" in query
            ids = params["project_ids"]
            reads.append((ids, timeout_ms))
            return SimpleNamespace(data=[{"val": item} for item in ids])

    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", Analytics)
    params = {
        "metric_name": "name",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 1,
    }

    def invoke(workspace_id, **extra):
        request = _request({**params, **extra})
        request.workspace = SimpleNamespace(id=workspace_id, pk=workspace_id)
        return dashboard_view.DashboardViewSet.filter_values.__wrapped__(
            dashboard_view.DashboardViewSet(),
            request,
        )

    cursors = []
    with capture_logs() as logs:
        for workspace_id, project_id in owners.items():
            page = _result(invoke(workspace_id))
            assert page["values"] == [{"value": project_id, "label": project_id}]
            assert datetime.fromisoformat(page["query_window_start"]) == datetime(
                1970 if fallback else 2020, 1, 1, tzinfo=UTC
            )
            assert reads[-1][0] == (project_id,)
            assert reads[-1][1] >= 1000
            cursors.append(page["next_cursor"])
        assert all(cursors)
        before = len(reads)
        assert invoke(_uuid(202), cursor=cursors[0]).status_code == 400
        assert len(reads) == before
    assert len(calls) == 2
    for transport, server in calls:
        assert 0 < server <= 0.45
        assert transport == pytest.approx(server)
    assert all("query" not in entry and "error" not in entry for entry in logs)
    assert "system.parts" not in repr(logs) and "secret-value-42" not in repr(logs)
    with capture_logs() as ordinary:
        client.execute_read("SELECT 1")
    assert any(entry.get("query") == "SELECT 1" for entry in ordinary)


@pytest.mark.unit
def test_stalled_retained_driver_does_not_block_page_or_queue_lookups(monkeypatch):
    from threading import Event
    from time import monotonic

    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.read_budget import ReadDeadline
    from tracer.services.clickhouse.v2 import query_service

    project = _uuid(1)
    _install_scope_and_seen_state(monkeypatch, _ProjectQuery([project], []))
    monkeypatch.setattr(
        attribute_reads.AttributeReadSelector,
        "retained_window_start",
        _REAL_RETAINED_WINDOW_START,
    )
    request_deadlines = []

    def read_projects(deadline, read):
        request_deadlines.append(deadline)
        return read()

    monkeypatch.setattr(dashboard_view, "_run_filter_value_pg_read", read_projects)
    wall_ms = 1_750
    monkeypatch.setattr(dashboard_view, "_FILTER_VALUES_INTERACTIVE_TIMEOUT_MS", wall_ms)
    entered, release, finished = Event(), Event(), Event()
    metadata_calls = []
    page_calls = []

    class Driver:
        def execute(self, query, params, **_kwargs):
            if "system.parts" in query:
                metadata_calls.append(1)
                entered.set()
                release.wait(timeout=5)  # Bound failure on the old shared-permit path.
                finished.set()
                return [(None,)], [("retained_start", "Nullable(DateTime)")]
            page_calls.append((monotonic(), entered.is_set(), finished.is_set()))
            assert "PREWHERE project_id IN %(project_ids)s" in query
            assert params["project_ids"] == (project,)
            return [("scoped name",)], [("val", "String")]

    client = ClickHouseClient(server_enforced_readonly=False, pool_size=1)
    monkeypatch.setattr(client, "_create_client", Driver)
    monkeypatch.setattr(query_service, "get_v2_query_client", lambda: client)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)

    def lookup(ms):
        return dashboard_view._batched_span_system_retained_window_start(
            [project], window_end=datetime.now(UTC), deadline=ReadDeadline.start(ms)
        )

    assert lookup(1_024) == epoch
    assert not metadata_calls
    try:
        page = _result(
            _invoke({
                "metric_name": "name",
                "metric_type": "system_metric",
                "source": "traces",
                "project_ids": [],
                "search": "",
                "page_size": 1,
            })
        )
        assert datetime.fromisoformat(page["query_window_start"]) == epoch
        assert page["values"] == [{"value": "scoped name", "label": "scoped name"}]
        [(started, metadata_entered, metadata_finished)] = page_calls
        assert metadata_entered and not metadata_finished
        parent = request_deadlines[0]
        parent_end = parent.started + parent.total_ms / 1000
        assert started < parent_end
        assert lookup(wall_ms) == epoch
        assert metadata_calls == [1]
        assert not finished.wait(timeout=max(0, parent_end - monotonic()))
    finally:
        release.set()
        # Wait for the worker's cleanup before restoring dependencies.
        slot = dashboard_view._RETAINED_LOOKUP_SLOT
        assert slot.acquire(timeout=5)
        slot.release()


@pytest.mark.unit
def test_held_retained_lookup_allows_process_exit():
    import os
    import subprocess
    import sys
    from pathlib import Path

    program = """
from datetime import UTC, datetime
from threading import Event
from unittest.mock import patch
import django
django.setup()
from tracer.views import dashboard
from tracer.services.clickhouse.read_budget import ReadDeadline
entered = Event()
def hold(*args, **kwargs):
    entered.set()
    Event().wait()  # No release: this worker must not be joined at shutdown.
with patch.object(dashboard, 'AttributeReadSelector') as selector:
    selector.return_value.retained_window_start.side_effect = hold
    result = dashboard._batched_span_system_retained_window_start(
        ['00000000-0000-0000-0000-000000000001'], window_end=datetime.now(UTC),
        deadline=ReadDeadline.start(2000),
    )
    assert entered.is_set() and result == datetime(1970, 1, 1, tzinfo=UTC)
"""
    subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tfc.settings.test",
            "NO_STARTUP_DB_MUTATIONS": "true",
        },
        check=True,
        timeout=30,
    )
