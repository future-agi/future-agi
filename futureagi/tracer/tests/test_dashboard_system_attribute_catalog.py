from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.attribute_cursor_state import AttributeCursorSeenState
from tracer.services.clickhouse.v2.attribute_catalog_cutover import CatalogReadAttempt
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CatalogValueCheckpoint,
)
from tracer.views import dashboard as dashboard_view

SNAPSHOT_START = datetime(2026, 8, 1, tzinfo=UTC)
SNAPSHOT_END = datetime(2026, 8, 14, tzinfo=UTC)


def _uuid(index: int) -> str:
    return str(UUID(int=index))


def _request(params):
    organization = SimpleNamespace(pk=UUID(int=90_001))
    workspace = SimpleNamespace(pk=UUID(int=90_002), id=UUID(int=90_002))
    return SimpleNamespace(
        validated_query_data=params,
        user=SimpleNamespace(pk=UUID(int=90_003), organization=organization),
        organization=organization,
        workspace=workspace,
        auth=None,
    )


def _invoke(params):
    return dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(),
        _request(params),
    )


def _candidate(value: str):
    return SimpleNamespace(value=value, attribute_type="string")


def _page(*values: str, has_more=False, checkpoint=None, query_count=1):
    return SimpleNamespace(
        candidates=tuple(_candidate(value) for value in values),
        has_more=has_more,
        next_checkpoint=checkpoint,
        query_count=query_count,
    )


def _checkpoint(value_fingerprint: str = "a" * 64):
    return CatalogValueCheckpoint(
        "span_attribute_catalog_qualification_v1",
        302,
        "scope",
        SNAPSHOT_START,
        SNAPSHOT_END,
        "model",
        ("string",),
        "",
        "query",
        "qualification",
        value_fingerprint,
        1,
    )


def _install_common(monkeypatch):
    snapshot_window = {"value": (SNAPSHOT_START, SNAPSHOT_END)}
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        dashboard_view,
        "catalog_dev_snapshot_window",
        lambda: snapshot_window["value"],
    )
    monkeypatch.setattr(
        dashboard_view,
        "load_attribute_cursor_seen_state",
        lambda reference, **_kwargs: AttributeCursorSeenState(
            tuple(reference), state_id=None
        ),
    )
    monkeypatch.setattr(
        dashboard_view,
        "persist_attribute_cursor_seen_state",
        lambda prior, appended, **_kwargs: (*prior.digests, *tuple(appended)),
    )
    return snapshot_window


@pytest.mark.unit
def test_fixed_model_uses_catalog_page_size_and_fails_closed_on_catalog_cursor(
    monkeypatch,
):
    snapshot_window = _install_common(monkeypatch)
    project_id = _uuid(1)
    monkeypatch.setattr(
        dashboard_view,
        "_prepare_filter_value_project_scope",
        lambda *_args, **_kwargs: dashboard_view._FilterValueProjectScope(
            mode="fixed",
            requested_project_ids=(project_id,),
            project_ids=(project_id,),
            batch_end_project_id=project_id,
        ),
    )
    calls = []

    def read_catalog(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return CatalogReadAttempt(
                True,
                _page(
                    "gpt-4.1",
                    has_more=True,
                    checkpoint=_checkpoint(),
                    query_count=2,
                ),
            )
        return CatalogReadAttempt(True, None, "catalog_query_failed")

    monkeypatch.setattr(
        dashboard_view,
        "try_catalog_system_value_page",
        read_catalog,
    )
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        lambda *_args, **_kwargs: pytest.fail("catalog continuation fell back"),
    )
    params = {
        "metric_name": "model",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [project_id],
        "search": "",
        "page_size": 7,
    }

    first = _invoke(params)
    assert first.status_code == 200
    payload = first.data["result"]
    assert payload["values"] == [{"value": "gpt-4.1", "label": "gpt-4.1"}]
    assert payload["query_window_mode"] == "frozen_snapshot"
    assert payload["next_cursor"] is not None
    assert first["X-FutureAGI-Attribute-Catalog"] == "catalog"
    assert calls[0]["page_size"] == 7
    assert calls[0]["window_start"] == SNAPSHOT_START
    assert calls[0]["window_end"] == SNAPSHOT_END

    # Runtime settings govern fresh walks only. A signed continuation must
    # retain its original frozen-window identity after the flag is disabled.
    snapshot_window["value"] = None
    resumed = _invoke({**params, "cursor": payload["next_cursor"]})
    assert resumed.status_code == 503
    assert resumed["X-FutureAGI-Attribute-Catalog"] == "fallback"
    assert resumed["X-FutureAGI-Attribute-Catalog-Fallback"] == ("catalog_query_failed")
    assert calls[1]["after"] == _checkpoint()


@pytest.mark.unit
def test_batched_model_catalog_advances_past_64_and_deduplicates_values(
    monkeypatch,
):
    _install_common(monkeypatch)
    project_ids = tuple(_uuid(index) for index in range(1, 66))
    first_scope = dashboard_view._FilterValueProjectScope(
        mode="workspace",
        requested_project_ids=(),
        project_ids=project_ids[:64],
        batch_end_project_id=project_ids[63],
        has_later_projects=True,
    )
    monkeypatch.setattr(
        dashboard_view,
        "_prepare_filter_value_project_scope",
        lambda *_args, **_kwargs: first_scope,
    )
    monkeypatch.setattr(
        dashboard_view,
        "_next_filter_value_project_batch",
        lambda *_args, **_kwargs: dashboard_view._FilterValueProjectScope(
            mode="workspace",
            requested_project_ids=(),
            project_ids=(project_ids[-1],),
            batch_end_project_id=project_ids[-1],
            has_later_projects=False,
        ),
    )
    observed_batches = []

    def read_catalog(**kwargs):
        batch = tuple(kwargs["project_ids"])
        observed_batches.append(batch)
        values = ("shared", "later-only") if len(batch) == 1 else ("shared",)
        return CatalogReadAttempt(True, _page(*values))

    monkeypatch.setattr(
        dashboard_view,
        "try_catalog_system_value_page",
        read_catalog,
    )
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        lambda *_args, **_kwargs: pytest.fail("catalog read fell back"),
    )
    params = {
        "metric_name": "model",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": [],
        "search": "",
        "page_size": 10,
    }

    first = _invoke(params)
    assert first.status_code == 200
    first_payload = first.data["result"]
    monkeypatch.setattr(dashboard_view, "catalog_dev_snapshot_window", lambda: None)
    second = _invoke({**params, "cursor": first_payload["next_cursor"]})
    assert second.status_code == 200
    second_payload = second.data["result"]

    assert first_payload["values"] == [{"value": "shared", "label": "shared"}]
    assert second_payload["values"] == [{"value": "later-only", "label": "later-only"}]
    assert first_payload["browse_status"] == "continuation"
    assert second_payload["browse_status"] == "exhausted"
    assert [len(batch) for batch in observed_batches] == [64, 1]
