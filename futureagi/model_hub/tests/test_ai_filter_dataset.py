"""Unit tests for dataset-source support in the AI filter smart agent.

TH-4400 followup + TH-6624. Dataset column values are grounded against
Postgres (source of truth), not the CH mirror — CH lag on dev used to
stall the endpoint until gunicorn timed out. These tests lock in:

  * ``_run_smart_agent`` takes a generic ``fetch_values(field_id)``
    callable so trace and dataset paths share the loop.
  * ``_fetch_dataset_column_values`` returns distinct Cell.value strings
    for a (dataset, column) pair, flattening list / dict JSON blobs for
    array / json columns.
  * ``_resolve_dataset_id`` rejects datasets outside the caller's
    workspace.

The ORM + LLM dependencies are mocked — we're testing plumbing, not
the model or the DB.
"""

import json
import unittest
from unittest import mock
from types import SimpleNamespace

from rest_framework.test import APIRequestFactory, force_authenticate


class _CellQS:
    """Minimal chainable stand-in for `Cell.objects.filter(...).exclude(...)...`."""

    def __init__(self, values):
        self._values = values

    def filter(self, **_):
        return self

    def exclude(self, **_):
        return self

    def values_list(self, *_, **__):
        return self

    def distinct(self):
        return self

    def order_by(self, *_):
        return self

    def __getitem__(self, _slice):
        return list(self._values)


def _patch_cell_and_column(cell_values, data_type):
    """Patch `Cell` and `Column` at the ai_filter module boundary.

    `_fetch_dataset_column_values` imports both from
    `model_hub.models.develop_dataset` inside the function, so we patch
    the source module and the helper picks the mocks up on next call.
    """
    col = mock.Mock(data_type=data_type)
    col_manager = mock.Mock()
    col_manager.only.return_value.get.return_value = col
    return mock.patch.multiple(
        "model_hub.models.develop_dataset",
        Cell=mock.Mock(objects=_CellQS(cell_values)),
        Column=mock.Mock(objects=col_manager),
    )


class FetchDatasetColumnValuesTests(unittest.TestCase):
    """``_fetch_dataset_column_values`` parses array/json cells correctly."""

    def test_text_column_returns_raw_values(self):
        from model_hub.views import ai_filter

        with _patch_cell_and_column(
            ["English", "Spanish", "French"], data_type="text"
        ):
            vals = ai_filter._fetch_dataset_column_values("ds-1", "col-1")
            self.assertEqual(vals, ["English", "Spanish", "French"])

    def test_array_column_flattens_list_elements(self):
        """Array cells stored as JSON lists should surface their elements."""
        from model_hub.views import ai_filter

        with _patch_cell_and_column(
            [
                json.dumps(["English", "French"]),
                json.dumps(["Spanish"]),
                json.dumps(["English", "Spanish"]),
            ],
            data_type="array",
        ):
            vals = ai_filter._fetch_dataset_column_values("ds-1", "col-1")
            self.assertEqual(sorted(vals), sorted(["English", "French", "Spanish"]))
            self.assertNotIn('["English", "French"]', vals)

    def test_json_column_dict_extracts_leaf_strings(self):
        from model_hub.views import ai_filter

        with _patch_cell_and_column(
            [
                json.dumps({"name": "Arthur", "role": "admin"}),
                json.dumps({"name": "Betty", "role": "admin"}),
            ],
            data_type="json",
        ):
            vals = ai_filter._fetch_dataset_column_values("ds-1", "col-1")
            self.assertIn("Arthur", vals)
            self.assertIn("Betty", vals)
            self.assertIn("admin", vals)

    def test_array_column_unparseable_cell_falls_back_to_raw(self):
        """A cell that isn't valid JSON should still contribute a value."""
        from model_hub.views import ai_filter

        with _patch_cell_and_column(
            ["not-json,just,text"], data_type="array"
        ):
            vals = ai_filter._fetch_dataset_column_values("ds-1", "col-1")
            self.assertEqual(vals, ["not-json,just,text"])

    def test_missing_ids_return_empty(self):
        from model_hub.views import ai_filter

        self.assertEqual(ai_filter._fetch_dataset_column_values("", "col-1"), [])
        self.assertEqual(ai_filter._fetch_dataset_column_values("ds-1", ""), [])


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
            self.assertIsNone(
                ai_filter._resolve_dataset_id(mock.Mock(), "ds-1")
            )

    def test_owned_dataset_returns_id_string(self):
        from model_hub.models.develop_dataset import Dataset
        from model_hub.views import ai_filter

        only = mock.Mock()
        only.get.return_value = mock.Mock(id="ds-1")
        with mock.patch.object(Dataset.objects, "only", return_value=only):
            self.assertEqual(
                ai_filter._resolve_dataset_id(mock.Mock(), "ds-1"), "ds-1"
            )


class RunSmartAgentFetchValuesTests(unittest.TestCase):
    """The agent loop calls ``fetch_values(field_id)`` — not a source-specific helper."""

    def test_fetch_values_invoked_for_string_fields(self):
        """Low-cardinality string fields should get their values pre-fetched
        and inlined into the prompt so the LLM can ground without a tool call.
        """
        from model_hub.views import ai_filter

        schema = [
            {"field": "col-lang", "label": "language", "type": "string"},
            {"field": "col-score", "label": "score", "type": "number"},
        ]
        calls = []

        def fv(field_id):
            calls.append(field_id)
            return ["English", "Spanish"] if field_id == "col-lang" else []

        # Short-circuit the LLM call by returning zero tool calls — we only
        # care that fetch_values was invoked during prompt construction.
        llm_response = mock.Mock()
        llm_response.choices = [mock.Mock(message=mock.Mock(tool_calls=None))]
        with mock.patch(
            "agentic_eval.core.llm.llm.LLM"
        ) as llm_cls:
            llm_cls.return_value._get_completion_with_tools.return_value = (
                llm_response
            )
            ai_filter._run_smart_agent("show english rows", schema, fv)

        # Only the string field gets pre-fetched; numeric fields don't.
        self.assertIn("col-lang", calls)
        self.assertNotIn("col-score", calls)


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
            llm_cls.return_value._get_completion_content.return_value = (
                '{"fields": ["status"]}'
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
