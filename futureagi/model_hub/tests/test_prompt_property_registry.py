from types import SimpleNamespace

from model_hub.serializers.contracts import PromptMetricsResultSerializer
from model_hub.utils.helpers import (
    get_default_prompt_metrics_config,
    get_default_span_prompt_metrics_config,
)
from tracer.utils.helper import update_column_config_based_on_eval_config


def test_prompt_metric_fields_expose_stable_registry_definitions():
    prompt_fields = get_default_prompt_metrics_config()
    span_fields = get_default_span_prompt_metrics_config()

    assert prompt_fields
    assert all(
        field["property_id"] == f"system_attribute:prompts:{field['id']}"
        for field in prompt_fields
    )
    assert all(field["property_kind"] == "system_attribute" for field in prompt_fields)
    assert all(field["property_source"] == "prompts" for field in prompt_fields)
    assert all(
        field["property_id"] == f"system_attribute:spans:{field['id']}"
        for field in span_fields
    )

    serializer = PromptMetricsResultSerializer(
        data={
            "table": [],
            "config": prompt_fields,
            "metadata": {"total_rows": 0},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["config"][0]["property_id"].startswith(
        "system_attribute:prompts:"
    )


def test_prompt_eval_columns_use_the_eval_config_definition_not_display_column_id():
    eval_config = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        name="Quality",
        eval_template=SimpleNamespace(
            id="22222222-2222-4222-8222-222222222222",
            config={"output": "choices", "choices_map": {}},
            choices=["good", "bad"],
        ),
    )

    fields = update_column_config_based_on_eval_config([], [eval_config])

    assert {field["id"] for field in fields} == {
        f"{eval_config.id}**good",
        f"{eval_config.id}**bad",
    }
    assert {field["property_id"] for field in fields} == {
        f"eval_config:{eval_config.id}"
    }
    assert {field["property_kind"] for field in fields} == {"eval_config"}
    assert {field["property_source"] for field in fields} == {"traces"}
