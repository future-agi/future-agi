import json

from tracer.serializers.eval_task import EditEvalTaskSerializer
from tracer.serializers.trace import UsersQuerySerializer


def _span_attr_filter(filter_op="equals", filter_value="alpha"):
    return {
        "column_id": "customer_tier",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


class TestFilterSerializerContracts:
    def test_users_query_serializer_decodes_strict_filter_query_param(self):
        serializer = UsersQuerySerializer(
            data={
                "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                "filters": json.dumps([_span_attr_filter()]),
            }
        )

        assert serializer.is_valid(), serializer.errors
        filters = serializer.validated_data["filters"]
        assert filters[0]["filter_config"]["filter_op"] == "equals"

    def test_users_query_serializer_rejects_camel_case_filter_config(self):
        payload = _span_attr_filter()
        payload["filterConfig"] = payload.pop("filter_config")
        serializer = UsersQuerySerializer(data={"filters": json.dumps([payload])})

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_eval_task_filters_validate_span_attribute_contract(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {
                    "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                    "date_range": [
                        "2026-01-01T00:00:00Z",
                        "2026-01-31T23:59:59Z",
                    ],
                    "observation_type": ["llm", "tool"],
                    "span_attributes_filters": [_span_attr_filter()],
                },
            }
        )

        assert serializer.is_valid(), serializer.errors
        filters = serializer.validated_data["filters"]
        assert filters["observation_type"] == ["llm", "tool"]

    def test_eval_task_filters_reject_frontend_field_id_drift(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {
                    "project_id": "1372e742-a10b-4d98-9ca4-31ef4d67115f",
                    "span_kind": ["llm"],
                },
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_eval_task_filters_reject_legacy_span_attribute_operator(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {
                    "span_attributes_filters": [
                        _span_attr_filter("not_in_between", ["a", "b"])
                    ],
                },
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_eval_task_filters_reject_malformed_date_range(self):
        serializer = EditEvalTaskSerializer(
            data={
                "edit_type": "edit_rerun",
                "filters": {"date_range": ["2026-01-01T00:00:00Z"]},
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors
