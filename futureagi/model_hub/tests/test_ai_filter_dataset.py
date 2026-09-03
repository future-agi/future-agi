"""Unit tests for dataset-source support in the AI filter smart agent.

TH-4400 follow-up. The trace AI filter grounds values against the real
column values in ClickHouse; previously the dataset filter path fell
through to schema-agnostic ``build_filters`` (no grounding). These
tests lock in the refactor:

  * ``_run_smart_agent`` now takes a generic ``fetch_values(field_id)``
    callable so trace and dataset paths share the loop.
  * ``_fetch_dataset_column_values`` returns distinct Cell.value strings
    for a (dataset, column) pair, flattening list / dict JSON blobs for
    array / json columns.
  * ``_resolve_dataset_id`` rejects datasets outside the caller's
    workspace.

The CH + LLM dependencies are mocked — we're testing plumbing, not
the model or the query engine.
"""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from rest_framework.test import APIRequestFactory, force_authenticate


class FetchDatasetColumnValuesTests(unittest.TestCase):
    """``_fetch_dataset_column_values`` parses array/json cells correctly."""

    def _patch_ch(self, values):
        """Patch CH + Column lookup so the helper sees `values` as raw rows."""

        class _Result:
            def __init__(self, rows):
                self.data = [{"val": v} for v in rows]

        class _Col:
            data_type = "text"

        return (
            mock.patch.multiple(
                "model_hub.views.ai_filter",
                is_clickhouse_enabled=mock.DEFAULT,
                AnalyticsQueryService=mock.DEFAULT,
            ),
            _Result(values),
            _Col,
        )

    def test_text_column_returns_raw_values(self):
        from model_hub.views import ai_filter

        with (
            mock.patch(
                "tracer.services.clickhouse.client.is_clickhouse_enabled",
                return_value=True,
            ),
            mock.patch(
                "tracer.services.clickhouse.query_service.AnalyticsQueryService"
            ) as aq,
            mock.patch("model_hub.models.develop_dataset.Column.objects") as cols,
        ):
            aq.return_value.execute_ch_query.return_value = mock.Mock(
                data=[{"val": "English"}, {"val": "Spanish"}, {"val": "French"}]
            )
            cols.only.return_value.get.return_value = mock.Mock(data_type="text")

            vals = ai_filter._fetch_dataset_column_values(
                "ds-1", "col-1", search_query="ish"
            )
            self.assertEqual(vals, ["English", "Spanish", "French"])
            call = aq.return_value.execute_ch_query.call_args
            self.assertEqual(call.args[1]["search"], "ish")
            self.assertEqual(call.args[1]["result_limit"], 101)
            self.assertLessEqual(call.kwargs["timeout_ms"], 4000)

    def test_array_column_flattens_list_elements(self):
        """Array cells stored as JSON lists should surface their elements."""
        from model_hub.views import ai_filter

        with (
            mock.patch(
                "tracer.services.clickhouse.client.is_clickhouse_enabled",
                return_value=True,
            ),
            mock.patch(
                "tracer.services.clickhouse.query_service.AnalyticsQueryService"
            ) as aq,
            mock.patch("model_hub.models.develop_dataset.Column.objects") as cols,
        ):
            aq.return_value.execute_ch_query.return_value = mock.Mock(
                data=[
                    {"val": json.dumps(["English", "French"])},
                    {"val": json.dumps(["Spanish"])},
                    {"val": json.dumps(["English", "Spanish"])},
                ]
            )
            cols.only.return_value.get.return_value = mock.Mock(data_type="array")

            vals = ai_filter._fetch_dataset_column_values(
                "ds-1", "col-1", search_query="ish"
            )
            # Dedup + order-preserving
            self.assertEqual(sorted(vals), sorted(["English", "Spanish"]))
            self.assertNotIn('["English", "French"]', vals)

    def test_json_column_dict_extracts_leaf_strings(self):
        from model_hub.views import ai_filter

        with (
            mock.patch(
                "tracer.services.clickhouse.client.is_clickhouse_enabled",
                return_value=True,
            ),
            mock.patch(
                "tracer.services.clickhouse.query_service.AnalyticsQueryService"
            ) as aq,
            mock.patch("model_hub.models.develop_dataset.Column.objects") as cols,
        ):
            aq.return_value.execute_ch_query.return_value = mock.Mock(
                data=[
                    {"val": json.dumps({"name": "Arthur", "role": "admin"})},
                    {"val": json.dumps({"name": "Betty", "role": "admin"})},
                ]
            )
            cols.only.return_value.get.return_value = mock.Mock(data_type="json")

            vals = ai_filter._fetch_dataset_column_values(
                "ds-1", "col-1", search_query="arth"
            )
            self.assertIn("Arthur", vals)
            self.assertNotIn("Betty", vals)
            self.assertNotIn("admin", vals)

    def test_array_column_unparseable_cell_falls_back_to_raw(self):
        """A cell that isn't valid JSON should still contribute a value."""
        from model_hub.views import ai_filter

        with (
            mock.patch(
                "tracer.services.clickhouse.client.is_clickhouse_enabled",
                return_value=True,
            ),
            mock.patch(
                "tracer.services.clickhouse.query_service.AnalyticsQueryService"
            ) as aq,
            mock.patch("model_hub.models.develop_dataset.Column.objects") as cols,
        ):
            aq.return_value.execute_ch_query.return_value = mock.Mock(
                data=[{"val": "not-json,just,text"}]
            )
            cols.only.return_value.get.return_value = mock.Mock(data_type="array")

            vals = ai_filter._fetch_dataset_column_values(
                "ds-1", "col-1", search_query="json"
            )
            self.assertEqual(vals, ["not-json,just,text"])

    def test_ch_disabled_returns_typed_unavailable(self):
        from model_hub.views import ai_filter

        with mock.patch(
            "tracer.services.clickhouse.client.is_clickhouse_enabled",
            return_value=False,
        ):
            with self.assertRaises(ai_filter.SmartFilterGroundingError) as error:
                ai_filter._fetch_dataset_column_values(
                    "ds-1", "col-1", search_query="english"
                )
            self.assertEqual(error.exception.status_code, 503)

    def test_missing_ids_return_typed_too_broad(self):
        from model_hub.views import ai_filter

        with self.assertRaises(ai_filter.SmartFilterGroundingError) as error:
            ai_filter._fetch_dataset_column_values("", "col-1", search_query="english")
        self.assertEqual(error.exception.status_code, 422)
        with self.assertRaises(ai_filter.SmartFilterGroundingError):
            ai_filter._fetch_dataset_column_values("ds-1", "", search_query="english")


class ResolveDatasetIdTests(unittest.TestCase):
    """Workspace isolation: smart mode must refuse datasets not in workspace."""

    def test_missing_id_returns_none(self):
        from model_hub.views import ai_filter

        self.assertIsNone(ai_filter._resolve_dataset_id(mock.Mock(), None))
        self.assertIsNone(ai_filter._resolve_dataset_id(mock.Mock(), ""))

    def test_foreign_dataset_returns_none(self):
        from model_hub.models.develop_dataset import Dataset
        from model_hub.views import ai_filter

        with mock.patch.object(
            Dataset.objects, "only", side_effect=Dataset.DoesNotExist
        ):
            self.assertIsNone(ai_filter._resolve_dataset_id(mock.Mock(), "ds-1"))

    def test_owned_dataset_returns_id_string(self):
        from model_hub.models.develop_dataset import Dataset
        from model_hub.views import ai_filter

        only = mock.Mock()
        only.get.return_value = mock.Mock(id="ds-1")
        with mock.patch.object(Dataset.objects, "only", return_value=only):
            self.assertEqual(ai_filter._resolve_dataset_id(mock.Mock(), "ds-1"), "ds-1")


class RunSmartAgentFetchValuesTests(unittest.TestCase):
    """The agent only performs query-scoped value reads requested by a tool."""

    def test_string_fields_are_not_prefetched_or_sampled(self):
        from model_hub.views import ai_filter

        schema = [
            {"field": "col-lang", "label": "language", "type": "string"},
            {"field": "col-score", "label": "score", "type": "number"},
        ]
        calls = []

        def fv(field_id, *, search_query):
            calls.append((field_id, search_query))
            return ["English", "Spanish"] if field_id == "col-lang" else []

        # Short-circuit the LLM call by returning zero tool calls — we only
        # care that fetch_values was invoked during prompt construction.
        llm_response = mock.Mock()
        llm_response.choices = [mock.Mock(message=mock.Mock(tool_calls=None))]
        with mock.patch("agentic_eval.core.llm.llm.LLM") as llm_cls:
            llm_cls.return_value._get_completion_with_tools.return_value = llm_response
            ai_filter._run_smart_agent("show english rows", schema, fv)

        self.assertEqual(calls, [])

    def test_tool_read_is_query_scoped_and_model_calls_use_remaining_wall(self):
        from model_hub.views import ai_filter

        schema = [
            {"field": "col-lang", "label": "language", "type": "string"},
        ]
        fetch_calls = []

        def fetch_values(field_id, *, search_query):
            fetch_calls.append((field_id, search_query))
            return ["English"]

        lookup_call = SimpleNamespace(
            id="lookup-1",
            function=SimpleNamespace(
                name="get_field_values",
                arguments=json.dumps(
                    {"field_id": "col-lang", "search_query": "english"}
                ),
            ),
        )
        submit_call = SimpleNamespace(
            id="submit-1",
            function=SimpleNamespace(
                name="submit_filter",
                arguments=json.dumps(
                    {
                        "filters": [
                            {
                                "field": "col-lang",
                                "operator": "is",
                                "value": "english",
                            }
                        ]
                    }
                ),
            ),
        )

        def response(tool_call):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="", tool_calls=[tool_call])
                    )
                ]
            )

        with mock.patch("agentic_eval.core.llm.llm.LLM") as llm_cls:
            completion = llm_cls.return_value._get_completion_with_tools
            completion.side_effect = [response(lookup_call), response(submit_call)]
            filters = ai_filter._run_smart_agent(
                "show english rows", schema, fetch_values
            )

        self.assertEqual(fetch_calls, [("col-lang", "english")])
        self.assertEqual(
            filters,
            [{"field": "col-lang", "operator": "is", "value": "English"}],
        )
        self.assertEqual(completion.call_count, 2)
        for call in completion.call_args_list:
            self.assertGreater(call.kwargs["timeout_ms"], 0)
            self.assertLessEqual(call.kwargs["timeout_ms"], 9000)


class LLMToolCompletionTimeoutTests(unittest.TestCase):
    def test_timeout_disables_litellm_retries_and_uses_remaining_wall(self):
        from agentic_eval.core.llm.llm import LLM

        llm = LLM.__new__(LLM)
        llm.provider = "openai"
        llm._prepare_completion_payload = mock.Mock(
            return_value={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hello"}],
            }
        )
        llm._try_gateway_completion = mock.Mock(return_value=None)
        llm._set_last_finish_reason_from_response = mock.Mock()
        llm._update_token_usage = mock.Mock()
        llm._update_cost = mock.Mock()
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

        with mock.patch("agentic_eval.core.llm.llm.litellm") as litellm:
            litellm.completion.return_value = response
            self.assertIs(
                llm._get_completion_with_tools(
                    [{"role": "user", "content": "hello"}],
                    [],
                    timeout_ms=9000,
                ),
                response,
            )

        kwargs = litellm.completion.call_args.kwargs
        self.assertGreater(kwargs["timeout"], 0)
        self.assertLessEqual(kwargs["timeout"], 9)
        self.assertEqual(kwargs["num_retries"], 0)
        gateway_kwargs = llm._try_gateway_completion.call_args.kwargs
        self.assertIsNotNone(gateway_kwargs["deadline_monotonic"])

    def test_bounded_tool_completion_refuses_unbounded_managed_transport(self):
        from agentic_eval.core.llm.llm import LLM

        llm = LLM.__new__(LLM)
        llm._requires_managed_transport = mock.Mock(return_value=True)
        llm._try_managed_ai_completion = mock.Mock()

        with self.assertRaisesRegex(
            TimeoutError,
            "bounded tool completion is unavailable",
        ):
            llm._try_gateway_completion(
                {"model": "turing_small"},
                deadline_monotonic=1.0,
            )

        llm._try_managed_ai_completion.assert_not_called()


class AIFilterViewContractTests(unittest.TestCase):
    """The view should use its declared request serializer at runtime."""

    def test_select_fields_uses_validated_request_payload(self):
        from model_hub.views.ai_filter import AIFilterView

        factory = APIRequestFactory()
        request = factory.post(
            "/model-hub/ai-filter/",
            {
                "mode": "select_fields",
                "query": "show failed rows",
                "schema": [
                    {
                        "field": "status",
                        "label": "Status",
                        "type": "enum",
                        "category": "system",
                    }
                ],
            },
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True),
        )

        with mock.patch("agentic_eval.core.llm.llm.LLM") as llm_cls:
            llm_cls.return_value._get_completion_with_tools.return_value = (
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content='{"fields": ["status"]}')
                        )
                    ]
                )
            )
            response = AIFilterView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"], {"fields": ["status"]})

    def test_invalid_request_returns_management_error_envelope(self):
        from model_hub.views.ai_filter import AIFilterView

        factory = APIRequestFactory()
        request = factory.post(
            "/model-hub/ai-filter/",
            {"mode": "select_fields", "schema": []},
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True),
        )

        response = AIFilterView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data["status"])
        self.assertIn("query", response.data["details"])
        self.assertIn("query", response.data["result"])

    def test_smart_grounding_refusal_keeps_typed_http_contract(self):
        from model_hub.views import ai_filter

        factory = APIRequestFactory()
        request = factory.post(
            "/model-hub/ai-filter/",
            {
                "mode": "smart",
                "query": "show model gpt",
                "project_id": "00000000-0000-4000-8000-000000000001",
                "schema": [
                    {
                        "field": "model",
                        "property_id": "system_attribute:traces:model",
                        "label": "Model",
                        "type": "string",
                        "category": "system",
                    }
                ],
            },
            format="json",
        )
        request.workspace = mock.Mock()
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True),
        )

        with (
            mock.patch.object(
                ai_filter,
                "_resolve_project_ids",
                return_value=["00000000-0000-4000-8000-000000000001"],
            ),
            mock.patch.object(
                ai_filter,
                "_run_smart_agent",
                side_effect=ai_filter._grounding_too_broad(),
            ),
        ):
            response = ai_filter.AIFilterView.as_view()(request)

        self.assertEqual(response.status_code, 422)
        self.assertFalse(response.data["status"])
        self.assertEqual(response.data["code"], "ai_filter_grounding_too_broad")
        self.assertNotIn("ClickHouse", response.data["result"])

    def test_unknown_request_fields_are_rejected(self):
        from model_hub.views.ai_filter import AIFilterView

        factory = APIRequestFactory()
        request = factory.post(
            "/model-hub/ai-filter/",
            {
                "mode": "select_fields",
                "query": "show failed rows",
                "schema": [
                    {
                        "field": "status",
                        "label": "Status",
                        "type": "enum",
                    }
                ],
                "projectId": "legacy camel alias",
            },
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True),
        )

        response = AIFilterView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["details"]["projectId"], ["Unknown field."])


if __name__ == "__main__":
    unittest.main()
