from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tracer.models.project import Project
from tracer.serializers.span_attributes import (
    SpanAttributeDetailQuerySerializer,
    SpanAttributeDetailResponseSerializer,
    SpanAttributeValuesResponseSerializer,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService, QueryResult
from tracer.services.clickhouse.span_attribute_lookups import AttributeKey
from tracer.views import span_attributes

KEYS_PATH = "/api/traces/span-attribute-keys/"
VALUES_PATH = "/api/traces/span-attribute-values/"
DETAIL_PATH = "/api/traces/span-attribute-detail/"
NOW = datetime(2026, 7, 30, 12, 34, 56, tzinfo=UTC)


def _project_query(path, project_id):
    query = {"project_id": str(project_id)}
    if path != KEYS_PATH:
        query["key"] = "final_status"
    return query


def test_span_attribute_detail_query_type_is_optional_and_validated():
    base_query = {
        "project_id": "11111111-2222-4333-8444-555555555555",
        "key": "customer_stage",
    }
    legacy = SpanAttributeDetailQuerySerializer(data=base_query)
    typed = SpanAttributeDetailQuerySerializer(data={**base_query, "type": "number"})
    invalid = SpanAttributeDetailQuerySerializer(data={**base_query, "type": "integer"})

    assert legacy.is_valid(), legacy.errors
    assert typed.is_valid(), typed.errors
    assert typed.validated_data["type"] == "number"
    assert not invalid.is_valid()
    assert "type" in invalid.errors


@pytest.mark.parametrize("path", [KEYS_PATH, VALUES_PATH, DETAIL_PATH])
@pytest.mark.django_db
def test_span_attribute_endpoints_hide_projects_outside_org_and_workspace(
    path,
    auth_client,
    organization,
    workspace,
    user,
    monkeypatch,
):
    other_workspace = Workspace.no_workspace_objects.create(
        name=f"Other workspace for {path}",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_workspace_project = Project.no_workspace_objects.create(
        name="Hidden same-org project",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    other_organization = Organization.objects.create(name=f"Other org for {path}")
    other_user = User.objects.create_user(
        email=f"other-{path.strip('/').replace('/', '-')}@example.com",
        password="testpassword123",
        name="Other User",
        organization=other_organization,
        organization_role=OrganizationRoles.OWNER,
    )
    other_org_workspace = Workspace.no_workspace_objects.create(
        name="Other org workspace",
        organization=other_organization,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    other_org_project = Project.no_workspace_objects.create(
        name="Hidden other-org project",
        organization=other_organization,
        workspace=other_org_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    ch_factory = MagicMock()
    key_lookup = MagicMock()
    monkeypatch.setattr(span_attributes, "ClickHouseClient", ch_factory)
    monkeypatch.setattr(span_attributes, "list_attribute_keys_for_project", key_lookup)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)

    for project in (other_workspace_project, other_org_project):
        response = auth_client.get(path, data=_project_query(path, project.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert project.name.encode() not in response.content

    ch_factory.assert_not_called()
    key_lookup.assert_not_called()


@pytest.mark.django_db
def test_span_attribute_keys_reports_bounded_discovery_as_sampled(
    auth_client,
    observe_project,
    monkeypatch,
):
    captured = {}

    def _list_keys(project_id, **kwargs):
        captured["project_id"] = project_id
        captured.update(kwargs)
        return [
            AttributeKey(key="customer_stage", type="string", count=12),
            AttributeKey(key="final_status", type="string"),
        ]

    monkeypatch.setattr(span_attributes, "list_attribute_keys_for_project", _list_keys)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        KEYS_PATH,
        data={"project_id": str(observe_project.id)},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "result": [
            {"key": "customer_stage", "type": "string", "count": 12},
            {"key": "final_status", "type": "string"},
        ],
        "query_complete": False,
        "query_status": "sampled",
        "query_error_code": "sample_limit",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }
    assert captured == {
        "project_id": str(observe_project.id),
        "window_start": datetime(2026, 7, 23, 12, 34, 56, tzinfo=UTC),
        "window_end": NOW,
    }


@pytest.mark.django_db
def test_span_attribute_keys_exact_probe_finds_rare_typed_key_without_browse(
    auth_client,
    observe_project,
    monkeypatch,
):
    captured = {}
    browse = MagicMock()

    def _find_key(project_id, key, **kwargs):
        captured.update(
            {
                "project_id": project_id,
                "key": key,
                **kwargs,
            }
        )
        return AttributeKey(key=key, type="boolean")

    monkeypatch.setattr(span_attributes, "list_attribute_keys_for_project", browse)
    monkeypatch.setattr(
        span_attributes,
        "find_attribute_key_for_project",
        _find_key,
    )
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        KEYS_PATH,
        data={
            "project_id": str(observe_project.id),
            "q": "rare_feature_flag",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "result": [{"key": "rare_feature_flag", "type": "boolean"}],
        "query_complete": True,
        "query_status": "complete",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }
    assert captured == {
        "project_id": str(observe_project.id),
        "key": "rare_feature_flag",
        "window_start": datetime(2026, 7, 23, 12, 34, 56, tzinfo=UTC),
        "window_end": NOW,
    }
    browse.assert_not_called()


@pytest.mark.django_db
def test_span_attribute_keys_exact_probe_budget_error_is_safe_and_degraded(
    auth_client,
    observe_project,
    monkeypatch,
):
    def _raise_budget(*args, **kwargs):
        raise TimeoutError("Code 159 private ClickHouse stack")

    monkeypatch.setattr(
        span_attributes,
        "find_attribute_key_for_project",
        _raise_budget,
    )
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        KEYS_PATH,
        data={
            "project_id": str(observe_project.id),
            "q": "rare_feature_flag",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "result": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }
    assert b"private ClickHouse" not in response.content


@pytest.mark.django_db
def test_span_attribute_keys_budget_error_returns_guaranteed_unknown_count(
    auth_client,
    observe_project,
    monkeypatch,
):
    def _raise_budget(*args, **kwargs):
        raise TimeoutError("Code 159 secret ClickHouse key stack trace")

    monkeypatch.setattr(
        span_attributes,
        "list_attribute_keys_for_project",
        _raise_budget,
    )
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        KEYS_PATH,
        data={"project_id": str(observe_project.id)},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "result": [{"key": "final_status", "type": "string"}],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }
    assert "count" not in response.json()["result"][0]
    assert b"secret ClickHouse" not in response.content


def test_span_attribute_key_discovery_query_uses_exact_bounded_window(
    monkeypatch,
):
    captured = {}
    analytics = AnalyticsQueryService()

    def _execute(query, params, timeout_ms, settings):
        captured.update(
            {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "settings": settings,
            }
        )
        return QueryResult(
            data=[{"key": "customer_stage", "type": "string", "count": 12}],
            row_count=1,
            backend_used="clickhouse",
            query_time_ms=4.0,
            columns=["key", "type", "count"],
        )

    monkeypatch.setattr(analytics, "execute_ch_query", _execute)
    window_start = NOW - timedelta(days=7)
    result = analytics.get_span_attribute_keys_ch_for_projects(
        ["11111111-2222-4333-8444-555555555555"],
        include_counts=True,
        order_by_count_desc=True,
        window_start=window_start,
        window_end=NOW,
    )

    assert result == [
        {"key": "customer_stage", "type": "string", "count": 12},
        {"key": "final_status", "type": "string"},
    ]
    query = captured["query"]
    assert query.count("start_time >= %(window_start)s") == 3
    assert query.count("start_time < %(window_end)s") == 3
    assert query.count("is_deleted = 0") == 3
    # Three per-map samples plus the existing outer catalog cap.
    assert query.count("LIMIT 1000") == 4
    assert "LIMIT 10000" not in query
    assert "FINAL" not in query
    assert captured["params"]["window_start"] == window_start
    assert captured["params"]["window_end"] == NOW
    assert "recent_days" not in captured["params"]
    assert captured["timeout_ms"] == 750
    assert captured["settings"]["max_threads"] == 2
    assert captured["settings"]["max_memory_usage"] == 256 * 1024 * 1024
    assert captured["settings"]["max_bytes_to_read"] == 1024 * 1024 * 1024
    assert captured["settings"]["read_overflow_mode"] == "throw"
    assert captured["settings"]["timeout_overflow_mode"] == "throw"


def test_span_attribute_exact_key_probe_is_parameterized_latest_state_and_limited(
    monkeypatch,
):
    captured = []
    analytics = AnalyticsQueryService()

    def _execute(query, params, timeout_ms, settings):
        captured.append(
            {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "settings": settings,
            }
        )
        if len(captured) == 1:
            return QueryResult(
                data=[{"id": "candidate-span", "start_time": NOW}],
                row_count=1,
                backend_used="clickhouse",
                query_time_ms=3.0,
                columns=["id", "start_time"],
            )
        return QueryResult(
            data=[{"type": "number"}],
            row_count=1,
            backend_used="clickhouse",
            query_time_ms=3.0,
            columns=["type"],
        )

    monkeypatch.setattr(analytics, "execute_ch_query", _execute)
    key = "rare_key_%') OR 1"
    window_start = NOW - timedelta(days=7)

    result = analytics.find_span_attribute_key_ch_for_project(
        "11111111-2222-4333-8444-555555555555",
        key,
        window_start=window_start,
        window_end=NOW,
    )

    assert result == {"key": key, "type": "number"}
    assert len(captured) == 2

    candidate = captured[0]
    assert "FROM spans\n" in candidate["query"]
    assert "FROM spans FINAL" not in candidate["query"]
    assert "PREWHERE project_id = %(project_id)s" in candidate["query"]
    assert "start_time >= %(window_start)s" in candidate["query"]
    assert "start_time < %(window_end)s" in candidate["query"]
    assert candidate["query"].count("mapContains(") == 3
    assert "LIMIT 1 BY" not in candidate["query"]
    assert "LIMIT %(candidate_limit)s" in candidate["query"]
    assert key not in candidate["query"]
    assert candidate["params"]["key"] == key
    assert candidate["params"]["candidate_limit"] == 17
    assert candidate["params"]["window_start"] == window_start
    assert candidate["params"]["window_end"] == NOW
    assert 25 <= candidate["timeout_ms"] <= 250
    assert candidate["settings"]["max_threads"] == 2
    assert candidate["settings"]["max_memory_usage"] == 256 * 1024 * 1024
    assert candidate["settings"]["max_bytes_to_read"] == 1024 * 1024 * 1024
    assert candidate["settings"]["max_result_rows"] == 17
    assert candidate["settings"]["read_overflow_mode"] == "throw"
    assert candidate["settings"]["timeout_overflow_mode"] == "throw"
    assert "use_skip_indexes_if_final" not in candidate["settings"]

    verify = captured[1]
    assert "FROM spans FINAL" in verify["query"]
    assert "PREWHERE project_id = %(project_id)s" in verify["query"]
    assert "start_time >= %(slice_start)s" in verify["query"]
    assert "start_time < %(slice_end)s" in verify["query"]
    assert "id IN %(candidate_ids)s" in verify["query"]
    assert verify["query"].count("mapContains(") == 5
    assert "LIMIT 1" in verify["query"]
    assert key not in verify["query"]
    assert verify["params"]["key"] == key
    assert verify["params"]["candidate_ids"] == ("candidate-span",)
    assert verify["params"]["slice_start"] == datetime(2026, 7, 30, 12, 30, tzinfo=UTC)
    assert verify["params"]["slice_end"] == datetime(2026, 7, 30, 12, 35, tzinfo=UTC)
    assert 25 <= verify["timeout_ms"] <= 250
    assert verify["settings"]["max_result_rows"] == 1
    assert verify["settings"]["use_skip_indexes_if_final"] == 0


def test_span_attribute_exact_probe_discards_partial_verification_on_error(
    monkeypatch,
):
    analytics = AnalyticsQueryService()
    calls = 0

    class PartialResultTimeout(TimeoutError):
        partial_rows = [{"type": "string"}]

    def _execute(query, params, timeout_ms, settings):
        nonlocal calls
        calls += 1
        if calls == 1:
            return QueryResult(
                data=[{"id": "candidate-span", "start_time": NOW}],
                row_count=1,
                backend_used="clickhouse",
                query_time_ms=3.0,
                columns=["id", "start_time"],
            )
        raise PartialResultTimeout("Code 241 after streaming a partial row")

    monkeypatch.setattr(analytics, "execute_ch_query", _execute)

    with pytest.raises(PartialResultTimeout):
        analytics.find_span_attribute_key_ch_for_project(
            "11111111-2222-4333-8444-555555555555",
            "final_status",
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        )

    assert calls == 2


def test_span_attribute_exact_probe_candidate_cap_is_incomplete_not_not_found(
    monkeypatch,
):
    analytics = AnalyticsQueryService()
    calls = 0

    def _execute(query, params, timeout_ms, settings):
        nonlocal calls
        calls += 1
        if calls == 1:
            return QueryResult(
                data=[
                    {"id": f"stale-{index}", "start_time": NOW} for index in range(17)
                ],
                row_count=17,
                backend_used="clickhouse",
                query_time_ms=3.0,
                columns=["id", "start_time"],
            )
        return QueryResult(
            data=[],
            row_count=0,
            backend_used="clickhouse",
            query_time_ms=3.0,
            columns=["type"],
        )

    monkeypatch.setattr(analytics, "execute_ch_query", _execute)

    with pytest.raises(TimeoutError, match="candidate cap"):
        analytics.find_span_attribute_key_ch_for_project(
            "11111111-2222-4333-8444-555555555555",
            "removed_rare_key",
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        )

    assert calls == 2


@pytest.mark.django_db
def test_span_attribute_values_uses_generic_bounded_recent_string_query(
    auth_client,
    observe_project,
    settings,
    monkeypatch,
):
    settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = 7
    settings.TRACE_FILTER_VALUES_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2000, 1, 1, tzinfo=UTC)
    client = MagicMock()
    client.execute_read.return_value = (
        [("100%_done\\path",)] * 8,
        [("value", "String")],
        12.5,
    )
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        VALUES_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "customer_stage",
            "q": "100%_done\\path",
            "limit": 20,
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "result": [{"value": "100%_done\\path", "count": 8}],
        "query_complete": True,
        "query_status": "complete",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }

    query, params = client.execute_read.call_args.args
    kwargs = client.execute_read.call_args.kwargs
    assert "FROM spans" in query
    assert "FROM dashboard_attr_rollup" not in query
    assert "PREWHERE project_id = %(project_id)s" in query
    assert "start_time >= %(window_start)s" in query
    assert "start_time < %(window_end)s" in query
    assert "is_deleted = 0" in query
    assert "mapContains(attrs_string, %(key)s)" in query
    assert "parent_span_id = ''" not in query
    assert "LIKE %(q_pattern)s" in query
    assert "ESCAPE" not in query
    assert "GROUP BY" not in query
    assert "ORDER BY" not in query
    assert "LIMIT %(sample_limit)s" in query
    assert params["q_pattern"] == "%100\\%\\_done\\\\path%"
    assert params["sample_limit"] == 1000
    assert params["window_end"] - params["window_start"] == timedelta(days=7)
    assert kwargs["timeout_ms"] == 750
    assert kwargs["settings"]["max_memory_usage"] == 256 * 1024 * 1024
    assert kwargs["settings"]["max_bytes_to_read"] == 1024 * 1024 * 1024
    assert kwargs["settings"]["read_overflow_mode"] == "throw"
    assert kwargs["settings"]["timeout_overflow_mode"] == "throw"


@pytest.mark.django_db
def test_final_status_values_use_rollup_and_include_the_active_hour(
    auth_client,
    observe_project,
    settings,
    monkeypatch,
):
    settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = 7
    settings.TRACE_FILTER_VALUES_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2026, 7, 1, tzinfo=UTC)
    client = MagicMock()
    client.execute_read.return_value = (
        [("completed", 101)],
        [("value", "String"), ("cnt", "UInt64")],
        4.2,
    )
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        VALUES_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "final_status",
            "q": "complete_100%",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["query_window_start"] == "2026-07-23T12:00:00Z"
    assert response.json()["query_window_end"] == "2026-07-30T12:34:56Z"
    query, params = client.execute_read.call_args.args
    assert "FROM dashboard_attr_rollup" in query
    assert "FROM spans" not in query
    assert "countMerge(n)" in query
    assert "hour >= %(window_start)s" in query
    assert "hour < %(window_end)s" in query
    assert "attr_value LIKE %(q_pattern)s" in query
    assert "ESCAPE" not in query
    assert params["key"] == "final_status"
    assert params["q_pattern"] == "%complete\\_100\\%%"
    assert params["window_start"] == datetime(2026, 7, 23, 12, tzinfo=UTC)
    assert params["window_end"] == NOW


@pytest.mark.django_db
def test_country_values_do_not_use_root_only_rollup(
    auth_client,
    observe_project,
    settings,
    monkeypatch,
):
    settings.TRACE_FILTER_VALUES_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2000, 1, 1, tzinfo=UTC)
    client = MagicMock()
    client.execute_read.return_value = ([("US",)], [("value", "String")], 4.0)
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        VALUES_PATH,
        data={"project_id": str(observe_project.id), "key": "country"},
    )

    assert response.status_code == status.HTTP_200_OK
    query, params = client.execute_read.call_args.args
    assert "FROM dashboard_attr_rollup" not in query
    assert "FROM spans" in query
    assert "mapContains(attrs_string, %(key)s)" in query
    assert "parent_span_id" not in query
    assert params["key"] == "country"


@pytest.mark.django_db
def test_span_attribute_values_full_bounded_sample_is_usable_sampled_data(
    auth_client,
    observe_project,
    monkeypatch,
):
    rows = [(f"value-{index:04d}",) for index in range(1000)]
    client = MagicMock()
    client.execute_read.return_value = (rows, [("value", "String")], 5.0)
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        VALUES_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "customer_stage",
            "limit": 50,
        },
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert len(payload["result"]) == 50
    assert payload["query_complete"] is False
    assert payload["query_status"] == "sampled"
    assert payload["query_error_code"] == "sample_limit"
    serializer = SpanAttributeValuesResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_final_status_values_fall_back_to_bounded_root_scan_when_not_covered(
    auth_client,
    observe_project,
    settings,
    monkeypatch,
):
    settings.TRACE_FILTER_VALUES_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2026, 7, 24, tzinfo=UTC)
    client = MagicMock()
    client.execute_read.return_value = ([], [], 2.0)
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        VALUES_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "final_status",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    query, _ = client.execute_read.call_args.args
    assert "FROM dashboard_attr_rollup" not in query
    assert "FROM spans" in query
    assert "(parent_span_id IS NULL OR parent_span_id = '')" in query
    assert "is_deleted = 0" in query


@pytest.mark.django_db
def test_span_attribute_values_read_budget_error_returns_safe_incomplete_200(
    auth_client,
    observe_project,
    monkeypatch,
):
    client = MagicMock()
    client.execute_read.side_effect = TimeoutError(
        "Code 159 secret ClickHouse stack trace"
    )
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        VALUES_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "customer_stage",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "result": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }
    assert b"secret ClickHouse" not in response.content


@pytest.mark.parametrize(
    ("detail_rows", "expected_type", "expected_payload", "map_name"),
    [
        (
            [("complete",), ("complete",), ("complete",), ("failed",)],
            "string",
            {
                "count": 4,
                "unique_values": 2,
                "top_values": [
                    {"value": "complete", "count": 3, "percentage": 75.0},
                    {"value": "failed", "count": 1, "percentage": 25.0},
                ],
            },
            "attrs_string",
        ),
        (
            [(4, 1.0, 9.0, 4.5, 4.0, 8.0)],
            "number",
            {
                "count": 4,
                "min": 1.0,
                "max": 9.0,
                "avg": 4.5,
                "p50": 4.0,
                "p95": 8.0,
            },
            "attrs_number",
        ),
        (
            [(True,), (True,), (True,), (False,)],
            "boolean",
            {
                "count": 4,
                "unique_values": 2,
                "top_values": [
                    {"value": True, "count": 3, "percentage": 75.0},
                    {"value": False, "count": 1, "percentage": 25.0},
                ],
            },
            "attrs_bool",
        ),
    ],
)
@pytest.mark.django_db
def test_span_attribute_detail_uses_validated_type_without_discovery_scan(
    detail_rows,
    expected_type,
    expected_payload,
    map_name,
    auth_client,
    observe_project,
    settings,
    monkeypatch,
):
    settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = 7
    client = MagicMock()
    client.execute_read.return_value = (detail_rows, [], 4.0)
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "custom_metric",
            "type": expected_type,
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "key": "custom_metric",
        "type": expected_type,
        **expected_payload,
        "query_complete": True,
        "query_status": "complete",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }

    assert client.execute_read.call_count == 1
    detail_query = client.execute_read.call_args.args[0]
    assert f"mapContains({map_name}, %(key)s)" in detail_query

    for call in client.execute_read.call_args_list:
        query, params = call.args
        kwargs = call.kwargs
        assert "FROM spans" in query
        assert "PREWHERE project_id = %(project_id)s" in query
        assert "start_time >= %(window_start)s" in query
        assert "start_time < %(window_end)s" in query
        assert "is_deleted = 0" in query
        assert params["project_id"] == str(observe_project.id)
        assert params["key"] == "custom_metric"
        assert params["window_end"] - params["window_start"] == timedelta(days=7)
        assert kwargs["timeout_ms"] == 750
        assert kwargs["settings"]["max_memory_usage"] == 256 * 1024 * 1024
        assert kwargs["settings"]["max_bytes_to_read"] == 1024 * 1024 * 1024
        assert kwargs["settings"]["read_overflow_mode"] == "throw"
        assert kwargs["settings"]["timeout_overflow_mode"] == "throw"


@pytest.mark.django_db
def test_span_attribute_detail_rejects_invalid_type_before_clickhouse(
    auth_client,
    observe_project,
    monkeypatch,
):
    client_factory = MagicMock()
    monkeypatch.setattr(span_attributes, "ClickHouseClient", client_factory)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "custom_metric",
            "type": "integer",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    client_factory.assert_not_called()


@pytest.mark.parametrize(
    ("attribute_type", "detail_rows", "map_name"),
    [
        ("string", [], "attrs_string"),
        ("number", [(0, None, None, None, None, None)], "attrs_number"),
        ("boolean", [], "attrs_bool"),
    ],
)
@pytest.mark.django_db
def test_span_attribute_detail_stale_requested_type_does_not_return_zero_data(
    attribute_type,
    detail_rows,
    map_name,
    auth_client,
    observe_project,
    monkeypatch,
):
    client = MagicMock()
    client.execute_read.return_value = (detail_rows, [], 2.0)
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "custom_metric",
            "type": attribute_type,
        },
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "No data found for this attribute type" in response.json()["message"]
    assert client.execute_read.call_count == 1
    assert map_name in client.execute_read.call_args.args[0]


@pytest.mark.django_db
def test_final_status_guaranteed_type_skips_raw_type_probes(
    auth_client,
    observe_project,
    settings,
    monkeypatch,
):
    settings.TRACE_FILTER_VALUES_ATTR_ROLLUP_ENABLED = False
    client = MagicMock()
    client.execute_read.return_value = ([("complete",)], [], 4.0)
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "final_status",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["type"] == "string"
    assert client.execute_read.call_count == 1
    query = client.execute_read.call_args.args[0]
    assert "mapContains(attrs_string, %(key)s)" in query
    assert "attrs_number" not in query
    assert "attrs_bool" not in query


@pytest.mark.django_db
def test_final_status_rejects_a_conflicting_requested_type(
    auth_client,
    observe_project,
    monkeypatch,
):
    client_factory = MagicMock()
    monkeypatch.setattr(span_attributes, "ClickHouseClient", client_factory)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "final_status",
            "type": "number",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["result"] == (
        "Attribute key has guaranteed type 'string'; the requested type does not match"
    )
    client_factory.assert_not_called()


@pytest.mark.django_db
def test_final_status_detail_uses_exact_aligned_rollup_without_raw_type_scan(
    auth_client,
    observe_project,
    settings,
    monkeypatch,
):
    aligned_now = datetime(2026, 7, 30, 13, tzinfo=UTC)
    settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = 7
    settings.TRACE_FILTER_VALUES_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2026, 7, 1, tzinfo=UTC)
    client = MagicMock()
    client.execute_read.return_value = (
        [("complete", 3), ("failed", 1)],
        [("value", "String"), ("cnt", "UInt64")],
        4.0,
    )
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: aligned_now)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "final_status",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "key": "final_status",
        "type": "string",
        "count": 4,
        "unique_values": 2,
        "top_values": [
            {"value": "complete", "count": 3, "percentage": 75.0},
            {"value": "failed", "count": 1, "percentage": 25.0},
        ],
        "query_complete": True,
        "query_status": "complete",
        "query_window_start": "2026-07-23T13:00:00Z",
        "query_window_end": "2026-07-30T13:00:00Z",
    }
    assert client.execute_read.call_count == 1
    query, params = client.execute_read.call_args.args
    assert "FROM dashboard_attr_rollup" in query
    assert "FROM spans" not in query
    assert "countMerge(n)" in query
    assert params["window_start"] == datetime(2026, 7, 23, 13, tzinfo=UTC)
    assert params["window_end"] == aligned_now


@pytest.mark.django_db
def test_span_attribute_string_detail_marks_top_100_as_sampled(
    auth_client,
    observe_project,
    monkeypatch,
):
    client = MagicMock()
    client.execute_read.return_value = (
        [(f"value-{index:03d}",) for index in range(101)],
        [],
        4.0,
    )
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "synthetic_status",
            "type": "string",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["count"] == 101
    assert payload["unique_values"] == 101
    assert len(payload["top_values"]) == 100
    assert payload["query_complete"] is False
    assert payload["query_status"] == "sampled"
    assert payload["query_error_code"] == "sample_limit"
    detail_query, detail_params = client.execute_read.call_args.args
    assert "GROUP BY" not in detail_query
    assert "LIMIT %(sample_limit)s" in detail_query
    assert detail_params["sample_limit"] == 2000
    serializer = SpanAttributeDetailResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_span_attribute_detail_type_budget_error_returns_safe_degraded_200(
    auth_client,
    observe_project,
    monkeypatch,
):
    client = MagicMock()
    client.execute_read.side_effect = TimeoutError(
        "Code 159 secret ClickHouse type stack trace"
    )
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "customer_stage",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "key": "customer_stage",
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }
    assert b"secret ClickHouse" not in response.content


@pytest.mark.django_db
def test_span_attribute_detail_data_budget_error_preserves_detected_type_only(
    auth_client,
    observe_project,
    monkeypatch,
):
    client = MagicMock()
    client.execute_read.side_effect = TimeoutError(
        "Code 159 secret ClickHouse detail stack trace"
    )
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "final_status",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "key": "final_status",
        "type": "string",
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
        "query_window_start": "2026-07-23T12:34:56Z",
        "query_window_end": "2026-07-30T12:34:56Z",
    }
    assert "count" not in response.json()
    assert b"secret ClickHouse" not in response.content


def test_span_attribute_detail_type_probes_ignore_row_order_and_stably_order_maps():
    client = MagicMock()

    def execute_read(query, *_args, **_kwargs):
        present = "attrs_string" in query or "attrs_bool" in query
        return ([(1,)] if present else [], [], 2.0)

    client.execute_read.side_effect = execute_read
    params = {
        "project_id": "11111111-2222-4333-8444-555555555555",
        "key": "customer_stage",
        "window_start": NOW - timedelta(days=7),
        "window_end": NOW,
    }

    result = span_attributes.SpanAttributeDetailView()._detect_types(client, params)

    assert result == ("string", "boolean")
    assert client.execute_read.call_count == 3
    expected_maps = ("attrs_string", "attrs_number", "attrs_bool")
    for call, expected_map in zip(
        client.execute_read.call_args_list, expected_maps, strict=True
    ):
        query, query_params = call.args
        assert "SELECT 1" in query
        assert "LIMIT 1" in query
        assert f"mapContains({expected_map}, %(key)s)" in query
        assert sum(name in query for name in expected_maps) == 1
        assert "multiIf" not in query
        assert query_params == params


def test_span_attribute_detail_type_probes_share_one_deadline(monkeypatch):
    client = MagicMock()
    client.execute_read.return_value = ([], [], 2.0)
    monotonic = MagicMock(side_effect=[100.0, 100.05, 100.20, 100.30])
    monkeypatch.setattr(span_attributes.time, "monotonic", monotonic)
    params = {
        "project_id": "11111111-2222-4333-8444-555555555555",
        "key": "customer_stage",
        "window_start": NOW - timedelta(days=7),
        "window_end": NOW,
    }

    result = span_attributes.SpanAttributeDetailView()._detect_types(client, params)

    assert result == ()
    timeouts = [
        call.kwargs["timeout_ms"] for call in client.execute_read.call_args_list
    ]
    assert 750 >= timeouts[0] > timeouts[1] > timeouts[2] > 0


@pytest.mark.parametrize(
    ("method_name", "rows"),
    [
        ("_string_detail", []),
        ("_number_detail", [(0, None, None, None, None, None)]),
        ("_boolean_detail", []),
    ],
)
def test_typed_detail_methods_reject_empty_data(method_name, rows):
    client = MagicMock()
    client.execute_read.return_value = (rows, [], 2.0)
    params = {
        "project_id": "11111111-2222-4333-8444-555555555555",
        "key": "customer_stage",
        "window_start": NOW - timedelta(days=7),
        "window_end": NOW,
    }

    response = getattr(span_attributes.SpanAttributeDetailView(), method_name)(
        client, params
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.data["result"] == "No data found for this attribute type"


def test_mixed_type_error_is_sanitized_and_matches_text_error_contract():
    response = span_attributes.SpanAttributeDetailView()._mixed_type_response(
        ("string", "boolean")
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["result"] == (
        "Attribute key has multiple stored types (string, boolean); "
        "specify the type query parameter"
    )
    assert "customer_secret" not in str(response.data)
    serializer = ApiTextErrorResponseSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_span_attribute_detail_mixed_types_are_disclosed_in_stable_order(
    auth_client,
    observe_project,
    monkeypatch,
):
    client = MagicMock()

    def execute_read(query, *_args, **_kwargs):
        present = "attrs_string" in query or "attrs_bool" in query
        return ([(1,)] if present else [], [], 2.0)

    client.execute_read.side_effect = execute_read
    monkeypatch.setattr(span_attributes, "ClickHouseClient", lambda: client)
    monkeypatch.setattr(span_attributes, "is_clickhouse_enabled", lambda: True)
    monkeypatch.setattr(span_attributes.timezone, "now", lambda: NOW)

    response = auth_client.get(
        DETAIL_PATH,
        data={
            "project_id": str(observe_project.id),
            "key": "mixed_attribute",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    payload = response.json()
    assert payload["result"] == (
        "Attribute key has multiple stored types (string, boolean); "
        "specify the type query parameter"
    )
    serializer = ApiTextErrorResponseSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert client.execute_read.call_count == 3


def test_span_attribute_string_detail_bounds_rows_and_discloses_sample():
    client = MagicMock()
    client.execute_read.return_value = (
        [("active",)] * span_attributes.SPAN_ATTRIBUTE_VALUE_SAMPLE_MAX_ROWS,
        [],
        3.0,
    )
    params = {
        "project_id": "11111111-2222-4333-8444-555555555555",
        "key": "customer_stage",
        "window_start": NOW - timedelta(days=7),
        "window_end": NOW,
    }

    response = span_attributes.SpanAttributeDetailView()._string_detail(client, params)

    assert response.data["count"] == 2000
    assert response.data["top_values"] == [
        {"value": "active", "count": 2000, "percentage": 100.0}
    ]
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "sampled"
    assert response.data["query_error_code"] == "sample_limit"
    query, query_params = client.execute_read.call_args.args
    assert "GROUP BY" not in query
    assert "LIMIT %(sample_limit)s" in query
    assert query_params["sample_limit"] == 2000


def test_span_attribute_string_rollup_detail_avoids_raw_spans():
    client = MagicMock()
    client.execute_read.return_value = ([("complete", 4)], [], 1.0)
    params = {
        "project_id": "11111111-2222-4333-8444-555555555555",
        "key": "final_status",
        "window_start": NOW - timedelta(days=7),
        "window_end": NOW,
    }

    response = span_attributes.SpanAttributeDetailView()._string_rollup_detail(
        client, params
    )

    assert response.data["type"] == "string"
    assert response.data["count"] == 4
    assert response.data["query_complete"] is True
    query = client.execute_read.call_args.args[0]
    assert "FROM dashboard_attr_rollup" in query
    assert "FROM spans" not in query
    assert "countMerge(n)" in query


def test_detail_rollup_rejects_non_aligned_picker_expansion(settings):
    settings.TRACE_FILTER_VALUES_ATTR_ROLLUP_ENABLED = True
    settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2026, 7, 1, tzinfo=UTC)
    window_start = NOW - timedelta(days=7)

    # Value suggestions deliberately include the leading partial hour.
    assert span_attributes._covered_attribute_rollup_window(window_start, NOW) == (
        datetime(2026, 7, 23, 12, tzinfo=UTC),
        NOW,
    )
    # Detail counts must not call that expanded interval complete.
    assert (
        span_attributes._covered_attribute_detail_rollup_window(window_start, NOW)
        is None
    )

    aligned_end = datetime(2026, 7, 30, 13, tzinfo=UTC)
    aligned_start = aligned_end - timedelta(days=7)
    assert span_attributes._covered_attribute_detail_rollup_window(
        aligned_start, aligned_end
    ) == (aligned_start, aligned_end)
