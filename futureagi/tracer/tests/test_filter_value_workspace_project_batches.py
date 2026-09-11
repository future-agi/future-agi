"""Workspace-scale filter-value cursor coverage without an unbounded project list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.test import override_settings

from tracer.services.annotation_label_source import (
    AnnotationLabelScoresProjectPG,
)
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
from tracer.services.clickhouse.v2 import attribute_catalog_cutover as cutover
from tracer.views import dashboard as dashboard_view

SNAPSHOT_WINDOW_START = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
SNAPSHOT_WINDOW_END = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
SNAPSHOT_SETTINGS = {
    "ENV_TYPE": "dev",
    "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "read",
    "SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK": cutover.CATALOG_DEV_READ_ACK,
    "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": True,
    "SPAN_ATTRIBUTE_CATALOG_HANDOFF_START": SNAPSHOT_WINDOW_START,
    "SPAN_ATTRIBUTE_CATALOG_HANDOFF_END": SNAPSHOT_WINDOW_END,
}


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


def _install_scope_and_seen_state(monkeypatch, projects: _ProjectQuery):
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: projects._clone(),
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
@override_settings(**SNAPSHOT_SETTINGS)
@pytest.mark.parametrize(
    "first_snapshot_enabled",
    [
        pytest.param(True, id="snapshot-to-baseline-runtime"),
        pytest.param(False, id="baseline-to-snapshot-runtime"),
    ],
)
def test_workspace_custom_value_cursor_keeps_its_signed_window_mode(
    monkeypatch,
    first_snapshot_enabled,
):
    project_ids = [_uuid(index) for index in range(1, 66)]
    projects = _ProjectQuery(project_ids, [])
    _install_scope_and_seen_state(monkeypatch, projects)
    observed_windows = []
    calls = 0

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_value_cursor_page(self, _batch, _key, **kwargs):
            nonlocal calls
            calls += 1
            value = f"custom-{calls}"
            digest = attribute_value_cursor_digest("string", value)
            observed_windows.append((kwargs["window_start"], kwargs["window_end"]))
            return AttributeValueCursorPageRead(
                rows=(AttributeValueRow(value, "string", 1),),
                metadata=AttributeReadMetadata(
                    True,
                    "complete",
                    None,
                    kwargs["window_start"],
                    kwargs["window_end"],
                    1,
                ),
                has_more=False,
                next_segment_end=kwargs["window_start"],
                next_before_identity=None,
                next_resume_identity=None,
                next_resume_member_offset=0,
                seen_value_digests=(digest,),
                browse_status="exhausted",
                appended_value_digests=(digest,),
                seen_value_count=kwargs["seen_value_count"] + 1,
            )

    monkeypatch.setattr(dashboard_view, "AttributeReadSelector", Selector)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        dashboard_view,
        "try_catalog_value_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(
            False,
            None,
            "runtime_disabled",
        ),
    )
    monkeypatch.setattr(
        dashboard_view,
        "_run_catalog_value_shadow_fail_open",
        lambda **_kwargs: None,
    )
    params = {
        "metric_name": "customer.plan",
        "metric_type": "custom_attribute",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=first_snapshot_enabled
    ):
        first = _result(_invoke(params))
    assert first["next_cursor"]

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=not first_snapshot_enabled
    ):
        second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert observed_windows[1] == observed_windows[0]
    assert [item["value"] for item in second["values"]] == ["custom-2"]
    if first_snapshot_enabled:
        assert first["query_window_mode"] == "frozen_snapshot"
        assert second["query_window_mode"] == "frozen_snapshot"
        assert observed_windows == [
            (SNAPSHOT_WINDOW_START, SNAPSHOT_WINDOW_END),
            (SNAPSHOT_WINDOW_START, SNAPSHOT_WINDOW_END),
        ]
    else:
        assert "query_window_mode" not in first
        assert "query_window_mode" not in second


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
@override_settings(**SNAPSHOT_SETTINGS)
@pytest.mark.parametrize(
    "first_snapshot_enabled",
    [
        pytest.param(True, id="enabled-to-disabled"),
        pytest.param(False, id="disabled-to-enabled"),
    ],
)
def test_workspace_system_value_cursor_is_stable_across_snapshot_flag_toggle(
    monkeypatch,
    first_snapshot_enabled,
):
    project_ids = [_uuid(index) for index in range(1, 66)]
    projects = _ProjectQuery(project_ids, [])
    _install_scope_and_seen_state(monkeypatch, projects)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    observed_windows = []
    calls = 0

    def read_page(_analytics, **kwargs):
        nonlocal calls
        calls += 1
        value = f"system-{calls}"
        digest = dashboard_view._filter_value_digest(value)
        observed_windows.append((kwargs["window_start"], kwargs["window_end"]))
        return FilterValueCursorPageRead(
            values=(value,),
            query_window_start=kwargs["window_start"],
            query_window_end=kwargs["window_end"],
            has_more=False,
            next_segment_end=kwargs["window_start"],
            next_segment_start=None,
            next_value_after=None,
            seen_value_digests=(digest,),
            browse_status="exhausted",
            appended_value_digests=(digest,),
            seen_value_count=kwargs["seen_value_count"] + 1,
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

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=first_snapshot_enabled
    ):
        first = _result(_invoke(params))
    assert first["next_cursor"]

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=not first_snapshot_enabled
    ):
        second = _result(_invoke({**params, "cursor": first["next_cursor"]}))

    assert observed_windows[1] == observed_windows[0]
    assert second["values"] == [{"value": "system-2", "label": "system-2"}]


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
                    "Needle project"
                    if project_id == project_ids[-1]
                    else "Unrelated project",
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
