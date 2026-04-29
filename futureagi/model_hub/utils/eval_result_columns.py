from model_hub.models.develop_dataset import DataTypeChoices


def infer_eval_result_column_data_type(eval_template) -> str:
    """Return the column data type for an eval result.

    This is the shared source of truth used by add-time creation, runtime
    runners, and display helpers so output-type support cannot drift between
    code paths.
    """

    if getattr(eval_template, "template_type", "single") == "composite":
        return (
            DataTypeChoices.FLOAT.value
            if getattr(eval_template, "aggregation_enabled", False)
            else DataTypeChoices.TEXT.value
        )

    output_type = ((getattr(eval_template, "config", None) or {}).get("output") or "")

    return {
        "reason": DataTypeChoices.TEXT.value,
        "score": DataTypeChoices.FLOAT.value,
        "numeric": DataTypeChoices.FLOAT.value,
        "choices": DataTypeChoices.ARRAY.value,
        "Pass/Fail": DataTypeChoices.BOOLEAN.value,
        "datetime": DataTypeChoices.DATETIME.value,
    }.get(output_type, DataTypeChoices.BOOLEAN.value)
