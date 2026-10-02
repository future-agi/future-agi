from dataclasses import asdict

from tracer.utils.helper import FieldConfig


def _attach_prompt_property_identity(config: list[dict], *, source: str) -> list[dict]:
    """Name prompt-table fields in the shared logical property registry.

    ``id`` remains the native prompt-metrics adapter column.  ``property_id``
    is the stable definition identity retained by pickers and saved filters.
    Prompt resources themselves are values of these definitions, not new
    property definitions per prompt/version/label instance.
    """

    for field in config:
        field["property_id"] = f"system_attribute:{source}:{field['id']}"
        field["property_kind"] = "system_attribute"
        field["property_source"] = source
    return config


def get_default_prompt_metrics_config():
    config = [
        # FieldConfig(id="node_type", name="Node Type", is_visible=True, group_by=None),
        FieldConfig(
            id="prompt_template_version",
            name="Versions",
            is_visible=True,
            group_by=None,
        ),
        FieldConfig(
            id="prompt_label_name", name="Label Name", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="avg_input_tokens",
            name="Median Input Tokens",
            is_visible=True,
            group_by=None,
        ),
        FieldConfig(
            id="avg_output_tokens",
            name="Median Output Tokens",
            is_visible=True,
            group_by=None,
        ),
        FieldConfig(
            id="unique_traces", name="No. of traces", is_visible=True, group_by=None
        ),
        FieldConfig(id="avg_cost", name="Median Cost", is_visible=True, group_by=None),
        FieldConfig(
            id="avg_latency", name="Median Latency", is_visible=True, group_by=None
        ),
        FieldConfig(id="first_used", name="First Used", is_visible=True, group_by=None),
        FieldConfig(id="last_used", name="Last Used", is_visible=True, group_by=None),
    ]

    parsed_config = list(map(asdict, config))
    return _attach_prompt_property_identity(parsed_config, source="prompts")


def get_default_span_prompt_metrics_config():
    config = [
        # FieldConfig(id="node_type", name="Node Type", is_visible=True, group_by=None),
        FieldConfig(
            id="prompt_template_version",
            name="Versions",
            is_visible=True,
            group_by=None,
        ),
        FieldConfig(
            id="prompt_label_name", name="Label Name", is_visible=True, group_by=None
        ),
        FieldConfig(id="name", name="Span name", is_visible=True, group_by=None),
        FieldConfig(id="trace_id", name="Trace Id", is_visible=True, group_by=None),
        FieldConfig(id="span_id", name="Span Id", is_visible=True, group_by=None),
        FieldConfig(id="session_id", name="Session Id", is_visible=True, group_by=None),
        FieldConfig(id="input", name="Input", is_visible=True, group_by=None),
        FieldConfig(id="output", name="Output", is_visible=True, group_by=None),
    ]

    parsed_config = list(map(asdict, config))
    return _attach_prompt_property_identity(parsed_config, source="spans")
