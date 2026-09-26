"""Unit tests for dataset-source support in the AI filter smart agent.

TH-4400 follow-up. The trace AI filter grounds values against the real
column values; previously the dataset filter path fell
through to schema-agnostic ``build_filters`` (no grounding). These
tests lock in the refactor:

  * ``_run_smart_agent`` now takes a generic ``fetch_values(field_id)``
    callable so trace and dataset paths share the loop.
  * ``_fetch_dataset_column_values`` returns distinct Cell.value strings
    for a (dataset, column) pair, flattening list / dict JSON blobs for
    array / json columns.
  * ``_resolve_dataset_id`` rejects datasets outside the caller's
    workspace.

The PostgreSQL read + LLM dependencies are mocked — we're testing plumbing, not
the model or the query engine.
"""

import json
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from rest_framework.test import APIRequestFactory, force_authenticate

DATASET_ID = "11111111-1111-4111-8111-111111111111"
COLUMN_ID = "22222222-2222-4222-8222-222222222222"


class FetchDatasetColumnValuesTests(unittest.TestCase):
    """``_fetch_dataset_column_values`` parses array/json cells correctly."""

    def _fetch(self, rows, data_type="text", search_query="ish"):
        """Run the helper with ``rows`` as the PostgreSQL value read."""
        from model_hub.views import ai_filter

        with (
            mock.patch.object(
                ai_filter, "_dataset_column_value_rows", return_value=rows
            ) as read,
            mock.patch("model_hub.models.develop_dataset.Column.objects") as cols,
        ):
            cols.only.return_value.get.return_value = mock.Mock(data_type=data_type)
            values = ai_filter._fetch_dataset_column_values(
                DATASET_ID, COLUMN_ID, search_query=search_query
            )
        return values, read

    def test_text_column_returns_raw_values(self):
        vals, read = self._fetch(["English", "Spanish", "French"])
        self.assertEqual(vals, ["English", "Spanish", "French"])
        sql, params = read.call_args.args
        self.assertEqual(params["search"], "ish")
        self.assertEqual(params["result_limit"], 101)
        self.assertLessEqual(read.call_args.kwargs["deadline"].remaining_ms(), 4000)

    def test_values_are_read_from_postgres_not_the_cdc_mirror(self):
        """The ClickHouse mirror is ordered by cell id and trails every write,
        so grounding reads the column from PostgreSQL through its index."""
        _vals, read = self._fetch([], search_query="x")
        sql, params = read.call_args.args
        self.assertIn(
            "FROM model_hub_cell "
            "WHERE dataset_id = %(dataset_id)s "
            "AND column_id = %(column_id)s "
            "AND deleted = false "
            "AND value <> '' "
            "AND strpos(lower(value), lower(%(search)s)) > 0 ",
            sql,
        )
        self.assertNotIn("FINAL", sql)
        self.assertEqual(params["dataset_id"], uuid.UUID(DATASET_ID))
        self.assertEqual(params["column_id"], uuid.UUID(COLUMN_ID))

    def test_array_column_flattens_list_elements(self):
        """Array cells stored as JSON lists should surface their elements."""
        vals, _read = self._fetch(
            [
                json.dumps(["English", "French"]),
                json.dumps(["Spanish"]),
                json.dumps(["English", "Spanish"]),
            ],
            data_type="array",
        )
        # Dedup + order-preserving
        self.assertEqual(sorted(vals), sorted(["English", "Spanish"]))
        self.assertNotIn('["English", "French"]', vals)

    def test_json_column_dict_extracts_leaf_strings(self):
        vals, _read = self._fetch(
            [
                json.dumps({"name": "Arthur", "role": "admin"}),
                json.dumps({"name": "Betty", "role": "admin"}),
            ],
            data_type="json",
            search_query="arth",
        )
        self.assertIn("Arthur", vals)
        self.assertNotIn("Betty", vals)
        self.assertNotIn("admin", vals)

    def test_array_column_unparseable_cell_falls_back_to_raw(self):
        """A cell that isn't valid JSON should still contribute a value."""
        vals, _read = self._fetch(
            ["not-json,just,text"], data_type="array", search_query="json"
        )
        self.assertEqual(vals, ["not-json,just,text"])

    def test_read_failure_returns_typed_unavailable(self):
        from django.db import OperationalError

        from model_hub.views import ai_filter

        with (
            mock.patch.object(
                ai_filter,
                "_dataset_column_value_rows",
                side_effect=OperationalError("canceling statement due to timeout"),
            ),
            mock.patch("model_hub.models.develop_dataset.Column.objects") as cols,
        ):
            cols.only.return_value.get.return_value = mock.Mock(data_type="text")
            with self.assertRaises(ai_filter.SmartFilterGroundingError) as error:
                ai_filter._fetch_dataset_column_values(
                    DATASET_ID, COLUMN_ID, search_query="english"
                )
            self.assertEqual(error.exception.status_code, 503)

    def test_a_full_sentinel_page_is_typed_too_broad(self):
        from model_hub.views import ai_filter

        with self.assertRaises(ai_filter.SmartFilterGroundingError) as error:
            self._fetch([f"value-{index}" for index in range(101)])
        self.assertEqual(error.exception.status_code, 422)

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
