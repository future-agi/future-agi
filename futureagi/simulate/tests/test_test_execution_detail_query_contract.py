import json

from simulate.serializers.test_execution import ExecutionDetailQuerySerializer


def _filter():
    return {
        "column_id": "duration",
        "filter_config": {
            "filter_type": "number",
            "filter_op": "greater_than_or_equal",
            "filter_value": 10,
        },
    }


def test_test_execution_detail_query_accepts_canonical_filters():
    serializer = ExecutionDetailQuerySerializer(
        data={
            "page": "2",
            "limit": "50",
            "search": "failed",
            "filters": json.dumps([_filter()]),
            "row_groups": json.dumps(["scenario"]),
            "group_keys": json.dumps(["checkout"]),
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"] == [_filter()]
    assert serializer.validated_data["row_groups"] == ["scenario"]
    assert serializer.validated_data["group_keys"] == ["checkout"]
    assert serializer.validated_data["page"] == 2
    assert serializer.validated_data["limit"] == 50


def test_test_execution_detail_query_rejects_bad_filter_json():
    serializer = ExecutionDetailQuerySerializer(
        data={"filters": "[not-valid-json"}
    )

    assert not serializer.is_valid()
    assert "filters" in serializer.errors


def test_test_execution_detail_query_rejects_camel_case_filter_contract():
    bad_filter = _filter()
    bad_filter["filterConfig"] = bad_filter.pop("filter_config")
    serializer = ExecutionDetailQuerySerializer(
        data={"filters": json.dumps([bad_filter])}
    )

    assert not serializer.is_valid()
    assert "filters" in serializer.errors


def test_test_execution_detail_query_rejects_non_array_grouping_params():
    serializer = ExecutionDetailQuerySerializer(
        data={"row_groups": json.dumps({"field": "scenario"})}
    )

    assert not serializer.is_valid()
    assert "row_groups" in serializer.errors


def test_test_execution_detail_query_rejects_unknown_params():
    serializer = ExecutionDetailQuerySerializer(data={"pageNumber": "1"})

    assert not serializer.is_valid()
    assert "pageNumber" in serializer.errors
