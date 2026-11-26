"""No-database request-wall tests for every AI-filter mode."""

import json
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import DatabaseError
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.serializers.ai_filter import AIFilterRequestSerializer
from model_hub.views import ai_filter
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded


class _FakeDeadline:
    def __init__(self, *remaining):
        self._remaining = list(remaining) or [8_000]
        self.calls = []

    def remaining_ms(self, cap_ms, **kwargs):
        self.calls.append((cap_ms, kwargs))
        value = (
            self._remaining.pop(0) if len(self._remaining) > 1 else self._remaining[0]
        )
        if isinstance(value, BaseException):
            raise value
        return value


def _completion(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _request(*, mode, schema=None, source=None):
    body = {
        "mode": mode,
        "query": "show failed calls",
        "schema": schema
        or [
            {
                "field": "status",
                "label": "Status",
                "type": "enum",
                "category": "system",
                "operators": ["is", "is_not"],
                "choices": ["failed", "completed"],
            }
        ],
    }
    if source:
        body["source"] = source
    request = APIRequestFactory().post("/model-hub/ai-filter/", body, format="json")
    request.workspace = mock.Mock()
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    return request


@pytest.mark.parametrize(
    ("mode", "content", "expected"),
    [
        (
            "build_filters",
            '[{"field":"status","operator":"is","value":"failed"}]',
            {"filters": [{"field": "status", "operator": "is", "value": "failed"}]},
        ),
        ("select_fields", '{"fields":["status"]}', {"fields": ["status"]}),
    ],
)
def test_non_smart_modes_propagate_one_request_deadline(mode, content, expected):
    deadline = _FakeDeadline(8_321)
    with (
        mock.patch(
            "tracer.services.clickhouse.read_budget.ReadDeadline.start",
            return_value=deadline,
        ) as start,
        mock.patch("agentic_eval.core.llm.llm.LLM") as llm_cls,
    ):
        llm = llm_cls.return_value
        llm._get_completion_with_tools.return_value = _completion(content)

        response = ai_filter.AIFilterView.as_view()(_request(mode=mode))

    assert response.status_code == 200
    assert response.data["result"] == expected
    start.assert_called_once_with(ai_filter.SMART_FILTER_REQUEST_WALL_MS)
    llm._get_completion_with_tools.assert_called_once()
    assert llm._get_completion_with_tools.call_args.args[1] == []
    assert llm._get_completion_with_tools.call_args.kwargs["timeout_ms"] == 8_321
    llm._get_completion_content.assert_not_called()
    assert deadline.calls
    assert {cap for cap, _kwargs in deadline.calls} == {
        ai_filter.SMART_FILTER_REQUEST_WALL_MS
    }


@pytest.mark.parametrize("mode", ["build_filters", "select_fields"])
def test_non_smart_completion_failure_is_typed_and_sanitized(mode):
    private_detail = "provider failure: private-token-value"
    deadline = _FakeDeadline(8_000)
    with (
        mock.patch(
            "tracer.services.clickhouse.read_budget.ReadDeadline.start",
            return_value=deadline,
        ),
        mock.patch("agentic_eval.core.llm.llm.LLM") as llm_cls,
    ):
        llm = llm_cls.return_value
        llm._get_completion_with_tools.side_effect = TimeoutError(private_detail)

        response = ai_filter.AIFilterView.as_view()(_request(mode=mode))

    assert response.status_code == 503
    assert response.data["code"] == "ai_filter_unavailable"
    assert private_detail not in json.dumps(response.data)
    llm._get_completion_content.assert_not_called()


@pytest.mark.parametrize("content", ["not-json", "[]"])
def test_build_filter_does_not_run_local_fallback_after_deadline(content):
    deadline = _FakeDeadline(
        8_000,
        7_000,
        ReadDeadlineExceeded("private deadline detail"),
    )
    with (
        mock.patch(
            "tracer.services.clickhouse.read_budget.ReadDeadline.start",
            return_value=deadline,
        ),
        mock.patch("agentic_eval.core.llm.llm.LLM") as llm_cls,
        mock.patch.object(ai_filter, "_query_token_phrases") as fallback,
    ):
        llm_cls.return_value._get_completion_with_tools.return_value = _completion(
            content
        )

        response = ai_filter.AIFilterView.as_view()(_request(mode="build_filters"))

    assert response.status_code == 503
    assert response.data["code"] == "ai_filter_unavailable"
    assert "private deadline detail" not in json.dumps(response.data)
    fallback.assert_not_called()


def test_smart_mode_reuses_the_request_owned_deadline():
    deadline = _FakeDeadline(8_000)
    schema = [
        {
            "field": "status",
            "property_id": "system_attribute:traces:status",
            "label": "Status",
            "type": "string",
            "category": "system",
        }
    ]
    with (
        mock.patch(
            "tracer.services.clickhouse.read_budget.ReadDeadline.start",
            return_value=deadline,
        ) as start,
        mock.patch.object(
            ai_filter,
            "_resolve_project_ids",
            return_value=["00000000-0000-4000-8000-000000000001"],
        ),
        mock.patch.object(
            ai_filter,
            "_authorize_smart_property_schema",
            return_value=schema,
        ),
        mock.patch.object(ai_filter, "_run_smart_agent", return_value=[]) as run_agent,
    ):
        response = ai_filter.AIFilterView.as_view()(
            _request(mode="smart", schema=schema, source="traces")
        )

    assert response.status_code == 200
    start.assert_called_once_with(ai_filter.SMART_FILTER_REQUEST_WALL_MS)
    assert run_agent.call_args.kwargs["deadline"] is deadline
    assert len(deadline.calls) == 3
    assert {cap for cap, _kwargs in deadline.calls} == {
        ai_filter.SMART_FILTER_REQUEST_WALL_MS
    }


def test_smart_completion_failure_is_typed_and_sanitized():
    private_detail = "provider failure: private-smart-token"
    deadline = _FakeDeadline(8_000)
    schema = [
        {
            "field": "status",
            "property_id": "system_attribute:traces:status",
            "label": "Status",
            "type": "string",
            "category": "system",
        }
    ]
    with (
        mock.patch(
            "tracer.services.clickhouse.read_budget.ReadDeadline.start",
            return_value=deadline,
        ),
        mock.patch.object(
            ai_filter,
            "_resolve_project_ids",
            return_value=["00000000-0000-4000-8000-000000000001"],
        ),
        mock.patch.object(
            ai_filter,
            "_authorize_smart_property_schema",
            return_value=schema,
        ),
        mock.patch("agentic_eval.core.llm.llm.LLM") as llm_cls,
    ):
        llm_cls.return_value._get_completion_with_tools.side_effect = TimeoutError(
            private_detail
        )
        response = ai_filter.AIFilterView.as_view()(
            _request(mode="smart", schema=schema, source="traces")
        )

    assert response.status_code == 503
    assert response.data["code"] == "ai_filter_grounding_unavailable"
    assert private_detail not in json.dumps(response.data)
    assert (
        llm_cls.return_value._get_completion_with_tools.call_args.kwargs["timeout_ms"]
        == 8_000
    )


def test_postgres_timeout_shrinks_before_each_smart_scope_statement():
    raw_cursor = SimpleNamespace(calls=[])
    raw_cursor.execute = lambda sql, params: raw_cursor.calls.append((sql, params))
    deadline = _FakeDeadline(8_100, 7_900)
    executed = []

    def execute(sql, params, many, context):
        executed.append((sql, params, many, context))
        return "rows"

    context = {"cursor": SimpleNamespace(cursor=raw_cursor)}
    result = ai_filter._execute_ai_filter_query_with_deadline(
        deadline,
        execute,
        "SELECT scope",
        (),
        False,
        context,
    )

    assert result == "rows"
    assert raw_cursor.calls == [
        ("SELECT set_config('statement_timeout', %s, true)", ("8100",))
    ]
    assert executed == [("SELECT scope", (), False, context)]
    assert deadline.calls == [
        (ai_filter.SMART_FILTER_REQUEST_WALL_MS, {}),
        (ai_filter.SMART_FILTER_REQUEST_WALL_MS, {}),
    ]


def test_dataset_ownership_database_failure_is_not_swallowed_as_not_found():
    private_detail = "private database timeout"
    with mock.patch("model_hub.models.develop_dataset.Dataset.objects.only") as only:
        only.return_value.get.side_effect = DatabaseError(private_detail)

        with pytest.raises(DatabaseError, match=private_detail):
            ai_filter._resolve_dataset_id(
                SimpleNamespace(),
                "00000000-0000-4000-8000-000000000001",
            )


def test_request_schema_and_prompt_inputs_are_finitely_bounded():
    field = {
        "field": "status",
        "operators": ["is"],
        "choices": ["failed"],
    }
    serializer = AIFilterRequestSerializer(
        data={
            "query": "q" * 4_097,
            "schema": [field] * 513,
        }
    )

    assert not serializer.is_valid()
    assert set(serializer.errors) == {"query", "schema"}

    choice_serializer = AIFilterRequestSerializer(
        data={
            "query": "failed",
            "schema": [{**field, "choices": list(range(257))}],
        }
    )
    assert not choice_serializer.is_valid()
    assert "choices" in choice_serializer.errors["schema"][0]
