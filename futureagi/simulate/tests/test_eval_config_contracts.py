import uuid

from simulate.serializers.requests.run_test_evals import (
    EvalConfigDefinitionSerializer,
    EvalConfigUpdateRequestSerializer,
)


def _filter():
    return {
        "column_id": "duration",
        "filter_config": {
            "filter_type": "number",
            "filter_op": "greater_than_or_equal",
            "filter_value": 10,
        },
    }


def _payload():
    return {
        "template_id": str(uuid.uuid4()),
        "name": "quality_check",
        "mapping": {"hypothesis": "output"},
        "config": {
            "params": {"k": 3},
            "run_config": {"pass_threshold": 0.8},
            "temperature": 0.1,
            "strict": True,
        },
        "filters": [_filter()],
        "error_localizer": False,
        "model": "turing_large",
        "eval_group": str(uuid.uuid4()),
    }


def test_eval_config_definition_accepts_canonical_payload():
    serializer = EvalConfigDefinitionSerializer(data=_payload())

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"] == [_filter()]
    assert serializer.validated_data["config"]["temperature"] == 0.1
    assert serializer.validated_data["mapping"]["hypothesis"] == "output"


def test_eval_config_definition_rejects_unknown_root_fields():
    payload = _payload()
    payload["templateName"] = "legacy"

    serializer = EvalConfigDefinitionSerializer(data=payload)

    assert not serializer.is_valid()
    assert "templateName" in serializer.errors


def test_eval_config_definition_rejects_filter_object_shape():
    payload = _payload()
    payload["filters"] = {"duration": {"op": "greater_than_or_equal", "value": 10}}

    serializer = EvalConfigDefinitionSerializer(data=payload)

    assert not serializer.is_valid()
    assert "filters" in serializer.errors


def test_eval_config_definition_rejects_non_object_config():
    payload = _payload()
    payload["config"] = ["legacy"]

    serializer = EvalConfigDefinitionSerializer(data=payload)

    assert not serializer.is_valid()
    assert "config" in serializer.errors


def test_eval_config_definition_rejects_camel_case_filter_contract():
    payload = _payload()
    bad_filter = _filter()
    bad_filter["filterConfig"] = bad_filter.pop("filter_config")
    payload["filters"] = [bad_filter]

    serializer = EvalConfigDefinitionSerializer(data=payload)

    assert not serializer.is_valid()
    assert "filters" in serializer.errors


def test_eval_config_update_accepts_template_id_and_filters():
    serializer = EvalConfigUpdateRequestSerializer(
        data={
            "template_id": str(uuid.uuid4()),
            "filters": [_filter()],
            "config": {"temperature": 0.2},
            "mapping": {"hypothesis": "output"},
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert "template_id" in serializer.validated_data
    assert serializer.validated_data["filters"] == [_filter()]


def test_eval_config_update_rejects_unknown_root_fields():
    serializer = EvalConfigUpdateRequestSerializer(
        data={
            "config": {"temperature": 0.2},
            "mapping": {"hypothesis": "output"},
            "testExecutionId": str(uuid.uuid4()),
        }
    )

    assert not serializer.is_valid()
    assert "testExecutionId" in serializer.errors
