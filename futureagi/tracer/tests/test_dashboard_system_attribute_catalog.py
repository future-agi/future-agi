"""Dashboard suggestions use their declared, authorization-bound value reader."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_codec import encode_catalog_scalar
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    PropertyCatalogValueReader,
)
from tracer.views import dashboard as dashboard_view


def _uuid(index: int) -> str:
    return str(UUID(int=index))


def _request(params):
    organization = SimpleNamespace(pk=UUID(int=90_001))
    workspace = SimpleNamespace(
        pk=UUID(int=90_002), id=UUID(int=90_002), organization_id=organization.pk
    )
    return SimpleNamespace(
        validated_query_data=params,
        user=SimpleNamespace(pk=UUID(int=90_003), organization=organization),
        organization=organization,
        workspace=workspace,
        auth=None,
    )


def _invoke(params):
    return dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), _request(params)
    )


def _row(value):
    encoded = encode_catalog_scalar(value)
    return {
        "attribute_type": "string",
        "value_fingerprint": encoded.fingerprint,
        "value_json": encoded.value_json,
        "value_search_text_folded": encoded.search_text.casefold(),
        "first_seen": datetime(2026, 8, 1, tzinfo=UTC),
        "last_seen": datetime(2026, 8, 14, tzinfo=UTC),
    }


def _params(project_ids, *, page_size):
    return {
        "property_id": "system_attribute:traces:model",
        "_property_kind": "system_attribute",
        "metric_name": "model",
        "metric_type": "system_metric",
        "source": "traces",
        "project_ids": project_ids,
        "search": "",
        "page_size": page_size,
    }


def _install_reader(monkeypatch, project_ids, *pages):
    pages = iter(pages)
    calls = []
    scope_calls = []

    def resolve(workspace, requested, **kwargs):
        scope_calls.append((workspace, tuple(requested), kwargs))
        return list(project_ids)

    def execute(sql, params, **kwargs):
        calls.append((sql, params.copy(), kwargs))
        page = next(pages)
        if isinstance(page, Exception):
            raise page
        return SimpleNamespace(data=page)

    monkeypatch.setattr(
        dashboard_view, "resolve_property_catalog_project_scope", resolve
    )
    monkeypatch.setattr(
        dashboard_view,
        "PropertyCatalogValueReader",
        lambda **kwargs: PropertyCatalogValueReader(
            SimpleNamespace(execute=execute), **kwargs
        ),
    )
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_value_cursor_page",
        lambda *_args, **_kwargs: pytest.fail("observed read fell back to span scan"),
    )
    return calls, scope_calls


@pytest.mark.unit
def test_fixed_model_uses_current_page_size_and_fails_closed_on_read_error(
    monkeypatch,
):
    project_id = _uuid(1)
    rows = sorted(
        [_row(f"model-{index}") for index in range(8)],
        key=lambda row: (row["value_fingerprint"], row["value_json"]),
    )
    calls, scope_calls = _install_reader(
        monkeypatch,
        [project_id],
        [{"attribute_type": "string"}],
        rows,
        RuntimeError("index unavailable"),
    )
    params = _params([project_id], page_size=7)

    first = _invoke(params)
    assert first.status_code == 200, first.data
    payload = first.data["result"]
    assert [item["value"] for item in payload["values"]] == [
        PropertyCatalogValueReader._decode_value(row).value for row in rows[:7]
    ]
    assert payload["next_cursor"]
    assert payload["query_count"] == 2
    assert payload["query_provenance"] == "current_property_catalog"
    assert payload["query_exact"] is False
    assert calls[1][1]["limit"] == 8

    resumed = _invoke({**params, "cursor": payload["next_cursor"]})
    assert resumed.status_code == 503
    assert resumed.data["code"] == "service_unavailable"
    assert len(calls) == 3
    assert len(scope_calls) == 2
    assert all(call[1] == (project_id,) for call in scope_calls)


@pytest.mark.unit
def test_workspace_model_catalog_reaches_project_65_without_duplicate(monkeypatch):
    project_ids = [_uuid(index) for index in range(1, 66)]
    rows = sorted(
        [_row("shared"), _row("later-only")],
        key=lambda row: (row["value_fingerprint"], row["value_json"]),
    )
    calls, scope_calls = _install_reader(
        monkeypatch,
        project_ids,
        [{"attribute_type": "string"}],
        rows,
        [{"attribute_type": "string"}],
        rows[1:],
    )
    params = _params([], page_size=1)

    first = _invoke(params)
    assert first.status_code == 200, first.data
    first_payload = first.data["result"]
    second = _invoke({**params, "cursor": first_payload["next_cursor"]})
    assert second.status_code == 200, second.data
    second_payload = second.data["result"]

    values = [
        item["value"]
        for page in (first_payload, second_payload)
        for item in page["values"]
    ]
    assert len(values) == 2 and set(values) == {"shared", "later-only"}
    assert first_payload["browse_status"] == "continuation"
    assert second_payload["browse_status"] == "exhausted"
    assert second_payload["next_cursor"] is None
    assert len(scope_calls) == 2
    assert all(call[2]["include_workspace_projects"] for call in scope_calls)
    # Observed rows group the full authorized project set; only native fact
    # scans retain the 64-project batching protocol tested in the sibling file.
    assert all(call[1]["project_ids"] == tuple(project_ids) for call in calls)
    for sql, _params_used, _kwargs in calls[1::2]:
        assert "GROUP BY k.attribute_type, k.value_fingerprint, k.value_json" in sql
    assert calls[3][1]["after_fingerprint"] == rows[0]["value_fingerprint"]
    assert calls[3][1]["after_json"] == rows[0]["value_json"]


@pytest.mark.unit
def test_workspace_model_cursor_reauthorizes_membership_before_index_read(monkeypatch):
    project_ids = [_uuid(index) for index in range(1, 66)]
    rows = sorted(
        [_row("shared"), _row("later-only")],
        key=lambda row: (row["value_fingerprint"], row["value_json"]),
    )
    calls, scope_calls = _install_reader(
        monkeypatch, project_ids, [{"attribute_type": "string"}], rows
    )
    params = _params([], page_size=1)
    first = _invoke(params)
    assert first.status_code == 200, first.data

    project_ids.pop()
    resumed = _invoke({**params, "cursor": first.data["result"]["next_cursor"]})
    assert resumed.status_code == 400
    assert resumed.data["code"] == "cursor_mismatch"
    assert len(scope_calls) == 2
    assert len(calls) == 2


@pytest.mark.unit
@pytest.mark.parametrize("page_size", [None, 1])
def test_users_hash_uses_native_end_user_reader_and_preserves_paging(
    monkeypatch, page_size
):
    from tracer.serializers.dashboard import DashboardFilterValuesQuerySerializer

    project_id = _uuid(1)
    scope_reads, calls = [], []

    def authorize(request, requested, **_kwargs):
        scope_reads.append((request.workspace.id, requested))
        return requested

    def execute(sql, params, **_kwargs):
        calls.append((sql, params))
        values = ["0", "hash-z"]
        if params.get("value_after") is not None:
            values = [value for value in values if value > params["value_after"]]
        return SimpleNamespace(data=[{"val": value} for value in values])

    monkeypatch.setattr(
        dashboard_view, "_bounded_authorized_filter_value_projects", authorize
    )
    monkeypatch.setattr(
        dashboard_view,
        "V2AnalyticsQueryService",
        lambda: SimpleNamespace(execute_ch_query=execute),
    )
    raw = {
        "source": "sessions",
        "property_id": "system_attribute:users:user_id_hash",
        "project_ids": project_id,
    }
    if page_size is not None:
        raw["page_size"] = page_size
    serializer = DashboardFilterValuesQuerySerializer(data=raw)
    assert serializer.is_valid(), serializer.errors
    params = serializer.validated_data
    first = _invoke(params)
    assert first.status_code == 200, first.data
    payload = first.data["result"]
    assert payload["values"] == [
        {"value": value, "label": value}
        for value in (["0", "hash-z"] if page_size is None else ["0"])
    ]
    assert "FROM end_users" in calls[0][0]
    assert "user_id_hash" in calls[0][0]
    assert scope_reads == [(UUID(int=90_002), (project_id,))]
    if page_size is not None:
        assert payload["has_more"] is True
        assert payload["query_complete"] is True
        second = _invoke({**params, "cursor": payload["next_cursor"]})
        assert second.status_code == 200, second.data
        assert second.data["result"]["values"] == [
            {"value": "hash-z", "label": "hash-z"}
        ]
        assert second.data["result"]["has_more"] is False
        assert second.data["result"]["next_cursor"] is None
        assert second.data["result"]["query_complete"] is True
        assert calls[1][1]["value_after"] == "0"
        assert len(scope_reads) == 2
