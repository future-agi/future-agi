import json
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from rest_framework import serializers

from model_hub.models.choices import AnnotationTypeChoices, DataTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType


@dataclass
class FieldConfig:
    id: str
    name: str
    is_visible: bool
    group_by: str | None = None
    output_type: str | None = None
    reverse_output: bool | None = None
    annotation_label_type: AnnotationTypeChoices | None = None
    choices: list[str] | None = (None,)
    settings: dict | None = None
    choices_map: dict | None = None
    eval_template_id: str | None = None
    annotators: dict | None = None
    # When set, this column renders a sub-field (e.g. "reason") of a parent
    # eval column identified by parent_eval_id. Lets the frontend pull the
    # value from eval_outputs without parsing the id.
    source_field: str | None = None
    parent_eval_id: str | None = None


def get_sort_query(sort_by, sort_order="desc"):
    """
    Returns sort query based on sort_by parameter and sort order
    Args:
        sort_by (str): Field to sort by
        sort_order (str): Sort order ('asc' or 'desc'), defaults to 'desc'
    Returns:
        str: Sort query string with appropriate prefix
    """
    prefix = "" if sort_order == "asc" else "-"

    match sort_by:
        case "created_at":
            return f"{prefix}created_at"
        case "updated_at":
            return f"{prefix}updated_at"
        case "name":
            return f"{prefix}name"
        case _:
            return f"{prefix}created_at"  # Default sort by created_at


def get_default_trace_config():
    """Default columns for trace list — ordered by usefulness.

    Priority logic:
    1. Identity — what is this trace?
    2. Status — did it work?
    3. Performance — how long, how much?
    4. Content — what went in/out?
    5. Context — who, when, tags
    """
    config = [
        FieldConfig(id="trace_name", name="Trace Name", is_visible=True, group_by=None),
        FieldConfig(id="input", name="Input", is_visible=True, group_by=None),
        FieldConfig(id="output", name="Output", is_visible=True, group_by=None),
        FieldConfig(id="start_time", name="Timestamp", is_visible=True, group_by=None),
        FieldConfig(id="status", name="Status", is_visible=True, group_by=None),
        FieldConfig(id="latency", name="Latency", is_visible=True, group_by=None),
        FieldConfig(id="total_tokens", name="Tokens", is_visible=True, group_by=None),
        FieldConfig(id="cost", name="Total Cost", is_visible=True, group_by=None),
        FieldConfig(id="model", name="Model", is_visible=True, group_by=None),
        FieldConfig(id="tags", name="Tags", is_visible=True, group_by=None),
        FieldConfig(id="user_id", name="User Id", is_visible=True, group_by=None),
        # Hidden by default — available via Display > View columns
        FieldConfig(id="trace_id", name="Trace Id", is_visible=False, group_by=None),
        FieldConfig(
            id="prompt_tokens", name="Prompt Tokens", is_visible=False, group_by=None
        ),
        FieldConfig(
            id="completion_tokens",
            name="Completion Tokens",
            is_visible=False,
            group_by=None,
        ),
        FieldConfig(id="provider", name="Provider", is_visible=False, group_by=None),
        FieldConfig(
            id="session_id", name="Session Id", is_visible=False, group_by=None
        ),
    ]

    parsed_config = list(map(asdict, config))
    return parsed_config


def get_default_span_config():
    config = [
        FieldConfig(id="span_name", name="Span Name", is_visible=True, group_by=None),
        FieldConfig(id="status", name="Status", is_visible=True, group_by=None),
        FieldConfig(id="input", name="Input", is_visible=True, group_by=None),
        FieldConfig(id="output", name="Output", is_visible=True, group_by=None),
        FieldConfig(id="latency_ms", name="Duration", is_visible=True, group_by=None),
        FieldConfig(id="total_tokens", name="Tokens", is_visible=True, group_by=None),
        FieldConfig(id="cost", name="Total Cost", is_visible=True, group_by=None),
        FieldConfig(id="model", name="Model", is_visible=True, group_by=None),
        FieldConfig(id="start_time", name="Timestamp", is_visible=True, group_by=None),
        # Hidden by default
        FieldConfig(id="span_id", name="Span Id", is_visible=False, group_by=None),
        FieldConfig(id="trace_id", name="Trace Id", is_visible=False, group_by=None),
        FieldConfig(
            id="prompt_tokens", name="Prompt Tokens", is_visible=False, group_by=None
        ),
        FieldConfig(
            id="completion_tokens",
            name="Completion Tokens",
            is_visible=False,
            group_by=None,
        ),
        FieldConfig(id="provider", name="Provider", is_visible=False, group_by=None),
    ]

    parsed_config = list(map(asdict, config))
    return parsed_config


def get_default_project_version_config():
    config = [
        FieldConfig(id="run_name", name="Run Name", is_visible=True, group_by=None),
        FieldConfig(
            id="avg_cost", name="Avg. Cost", is_visible=True, group_by="System Metrics"
        ),
        FieldConfig(
            id="avg_latency",
            name="Avg. Latency",
            is_visible=True,
            group_by="System Metrics",
        ),
        FieldConfig(id="rank", name="Rank", is_visible=False, group_by=None),
    ]

    parsed_config = list(map(asdict, config))
    return parsed_config


def get_default_project_session_config():
    config = [
        FieldConfig(id="session_id", name="Session Id", is_visible=True, group_by=None),
        FieldConfig(
            id="first_message", name="First Message", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="last_message", name="Last Message", is_visible=True, group_by=None
        ),
        FieldConfig(id="duration", name="Duration", is_visible=True, group_by=None),
        FieldConfig(id="total_cost", name="Total Cost", is_visible=True, group_by=None),
        FieldConfig(
            id="total_traces_count", name="Total Traces", is_visible=True, group_by=None
        ),
        FieldConfig(id="start_time", name="Start Time", is_visible=True, group_by=None),
        FieldConfig(id="end_time", name="End Time", is_visible=True, group_by=None),
        FieldConfig(id="user_id", name="User Id", is_visible=True, group_by=None),
        FieldConfig(
            id="user_id_type", name="User Id Type", is_visible=False, group_by=None
        ),
        FieldConfig(
            id="user_id_hash", name="User Id Hash", is_visible=False, group_by=None
        ),
        FieldConfig(
            id="total_tokens", name="Total Tokens", is_visible=False, group_by=None
        ),
    ]

    parsed_config = list(map(asdict, config))
    return parsed_config


def get_default_eval_task_config(is_project_name_visible=True):
    config = [
        FieldConfig(id="name", name="Task Name", is_visible=True, group_by=None),
        FieldConfig(
            id="filters_applied", name="Filters Applied", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="created_at", name="Date Created", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="evals_applied", name="Evals Applied", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="sampling_rate", name="Sampling Rate", is_visible=True, group_by=None
        ),
        FieldConfig(id="last_run", name="Last Run", is_visible=True, group_by=None),
        FieldConfig(id="status", name="Status", is_visible=True, group_by=None),
    ]

    if is_project_name_visible:
        config.insert(
            1,
            FieldConfig(
                id="project_name", name="Project Name", is_visible=True, group_by=None
            ),
        )

    parsed_config = list(map(asdict, config))
    return parsed_config


def is_json(value: str) -> bool:
    try:
        json.loads(value)
        return True
    except json.JSONDecodeError:
        return False


def is_datetime(value: str) -> bool:
    try:
        pd.to_datetime(value)
        return True
    except (ValueError, TypeError):
        return False


def is_image(value: str) -> bool:
    return value.startswith(("data:image", "iVBORw0KGgo"))


def determine_value_type(value):
    # Determine data type based on value
    if isinstance(value, bool):
        return DataTypeChoices.BOOLEAN.value
    elif isinstance(value, int):
        return DataTypeChoices.INTEGER.value
    elif isinstance(value, float):
        return DataTypeChoices.FLOAT.value
    elif isinstance(value, list | tuple):
        return DataTypeChoices.ARRAY.value
    elif isinstance(value, dict):
        return DataTypeChoices.JSON.value
    elif isinstance(value, datetime):
        return DataTypeChoices.DATETIME.value
    elif isinstance(value, str):
        if is_json(value):
            return DataTypeChoices.JSON.value
        elif is_datetime(value):
            return DataTypeChoices.DATETIME.value
        elif is_image(value):
            return DataTypeChoices.IMAGE.value
        return DataTypeChoices.TEXT.value
    else:
        return DataTypeChoices.OTHERS.value


def update_column_config_based_on_eval_config(
    column_config: list[FieldConfig],
    custom_eval_configs: list[CustomEvalConfig],
    skip_choices: bool | None = False,
    is_simulator: bool = False,
):
    if not column_config:
        column_config = []

    for item in custom_eval_configs:
        eval_template_config = item.eval_template.config or {}
        output_type = eval_template_config.get("output", "score")
        choices = item.eval_template.choices if item.eval_template.choices else None
        choices_map = item.eval_template.config.get("choices_map", {})

        # For simulator projects, don't add "Avg." prefix
        name_prefix = "" if is_simulator else "Avg. "

        eval_template_id = str(item.eval_template.id)

        if choices and output_type == EvalOutputType.CHOICES.value and not skip_choices:
            for choice in choices:
                present_config = FieldConfig(
                    id=str(item.id) + "**" + choice,
                    name=f"{name_prefix}{choice} ({item.name})",
                    group_by="Evaluation Metrics",
                    is_visible=True,
                    output_type=output_type,
                    reverse_output=item.eval_template.config.get(
                        "reverse_output", False
                    ),
                    choices_map=choices_map,
                    eval_template_id=eval_template_id,
                )
                present_config = asdict(present_config)
                if not any(
                    config["id"] == present_config["id"] for config in column_config
                ):
                    column_config.append(present_config)
        else:
            present_config = FieldConfig(
                id=str(item.id),
                name=f"{name_prefix}{item.name}",
                group_by="Evaluation Metrics",
                is_visible=True,
                output_type=output_type,
                reverse_output=item.eval_template.config.get("reverse_output", False),
                choices_map=choices_map,
                choices=choices,
                eval_template_id=eval_template_id,
            )
            present_config = asdict(present_config)
            if not any(
                config["id"] == present_config["id"] for config in column_config
            ):
                column_config.append(present_config)

        # One reason column per eval (regardless of output_type). Hidden by
        # default — visible via the column picker. See TH-4136.
        # No "Avg." prefix: reason is the latest explanation, not an average.
        reason_id = f"{item.id}__reason"
        reason_config = FieldConfig(
            id=reason_id,
            name=f"{item.name} - Reason",
            group_by="Evaluation Metrics",
            is_visible=False,
            eval_template_id=eval_template_id,
            source_field="reason",
            parent_eval_id=str(item.id),
        )
        reason_config = asdict(reason_config)
        if not any(config["id"] == reason_config["id"] for config in column_config):
            column_config.append(reason_config)

    return column_config


def validate_filters_helper(value):
    if not value:
        return []

    REQUIRED_FILTER_KEYS = ["column_id", "filter_config"]
    VALID_CONFIG_KEYS = ["filter_type", "filter_op", "filter_value"]

    for filter_item in value:
        if not isinstance(filter_item, dict):
            raise serializers.ValidationError("Each filter must be a dictionary.")

        missing_keys = [key for key in REQUIRED_FILTER_KEYS if key not in filter_item]
        if missing_keys:
            raise serializers.ValidationError(
                f"Missing required filter keys: {', '.join(missing_keys)}"
            )

        filter_config = filter_item.get("filter_config")
        if not isinstance(filter_config, dict):
            raise serializers.ValidationError("Filter config must be a dictionary.")

        missing_keys = [key for key in VALID_CONFIG_KEYS if key not in filter_config]
        if missing_keys:
            raise serializers.ValidationError(
                f"Missing required filter config keys: {', '.join(missing_keys)}"
            )

    return value


def validate_sort_params_helper(value):
    """Validate that each sort parameter has the required keys."""
    REQUIRED_SORT_KEYS = ["column_id", "direction"]
    VALID_DIRECTIONS = ["asc", "desc"]

    if not value:
        return []

    for sort_item in value:
        if not isinstance(sort_item, dict):
            raise serializers.ValidationError(
                "Each sort parameter must be a dictionary."
            )

        missing_keys = [key for key in REQUIRED_SORT_KEYS if key not in sort_item]
        if missing_keys:
            raise serializers.ValidationError(
                f"Missing required sort keys: {', '.join(missing_keys)}"
            )

        if "direction" in sort_item and sort_item["direction"] not in VALID_DIRECTIONS:
            raise serializers.ValidationError(
                f"Sort direction must be one of {VALID_DIRECTIONS}, got {sort_item['direction']}"
            )

    return value


def get_annotation_labels_for_project(project_id, organization=None):
    """Find annotation labels that have at least one Score in a project.

    Labels may not have a direct ``project`` FK set (e.g. org-wide centralized
    labels), so we look for labels referenced by Score records whose trace or
    observation_span belongs to the project.

    Pre-deprecation this method also union'd in ``TraceAnnotation``-referenced
    labels. Score is the unified store now (the dual-write mirrors every
    TraceAnnotation write to Score, so any label in TraceAnnotation is also
    reachable via Score). Reading both was redundant; reading Score alone
    is the path forward toward fully retiring TraceAnnotation.
    """
    from django.db.models import Q

    from model_hub.models.score import Score

    # Labels with scores for this project
    score_label_ids = (
        Score.objects.filter(
            Q(trace__project_id=project_id)
            | Q(observation_span__project_id=project_id),
            deleted=False,
        )
        .values("label_id")
        .distinct()
    )

    return AnnotationsLabels.objects.filter(
        Q(project_id=project_id) | Q(id__in=score_label_ids),
        deleted=False,
    ).distinct()


def update_span_column_config_based_on_annotations(
    column_config: list[FieldConfig], annotation_labels: list[AnnotationsLabels]
):
    from model_hub.models.score import Score

    if not column_config:
        column_config = []

    # Batch-fetch distinct annotators for all labels in one query
    label_ids = [label.id for label in annotation_labels]
    annotator_rows = (
        Score.objects.filter(label_id__in=label_ids, deleted=False)
        .values("label_id", "annotator_id", "annotator__name", "annotator__email")
        .distinct()
    )

    # Build a map: label_id → {user_id: {userId, userName}}
    label_annotators_map: dict[str, dict] = {}
    for row in annotator_rows:
        lid = str(row["label_id"])
        uid = str(row["annotator_id"])
        if lid not in label_annotators_map:
            label_annotators_map[lid] = {}
        label_annotators_map[lid][uid] = {
            "user_id": uid,
            "user_name": row["annotator__name"] or row["annotator__email"] or "Unknown",
        }

    for label in annotation_labels:
        choices = []
        if label.type == AnnotationTypeChoices.CATEGORICAL.value:
            choices = [option["label"] for option in label.settings["options"]]

        label_type = label.type
        output_type = float

        if label_type == AnnotationTypeChoices.CATEGORICAL.value:
            output_type = "list"
        elif label_type == AnnotationTypeChoices.TEXT.value:
            output_type = "text"
        elif label_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
            output_type = "boolean"
        else:
            output_type = "float"

        present_config = FieldConfig(
            id=str(label.id),
            name=f"{label.name}",
            group_by="Annotation Metrics",
            is_visible=True,
            output_type=output_type,
            reverse_output=False,
            annotation_label_type=label.type,
            choices=choices if len(choices) > 0 else None,
            settings=label.settings,
            annotators=label_annotators_map.get(str(label.id)),
        )
        present_config = asdict(present_config)
        if not any(config["id"] == present_config["id"] for config in column_config):
            column_config.append(present_config)

    return column_config


def update_run_column_config_based_on_annotations(
    column_config: list[FieldConfig], annotation_labels: list[AnnotationsLabels]
):
    if not column_config:
        column_config = []

    for label in annotation_labels:
        choices = []
        if label.type == AnnotationTypeChoices.CATEGORICAL.value:
            choices = [option["label"] for option in label.settings["options"]]

        if choices and len(choices) > 0:
            for choice in choices:
                present_config = FieldConfig(
                    id=str(label.id) + "**" + choice,
                    name=f"Avg. {choice} ({label.name})",
                    group_by="Annotation Metrics",
                    is_visible=True,
                    output_type="float",
                    reverse_output=False,
                    choices=choices,
                    settings=label.settings,
                )
                present_config = asdict(present_config)
                if not any(
                    config["id"] == present_config["id"] for config in column_config
                ):
                    column_config.append(present_config)
        else:
            present_config = FieldConfig(
                id=str(label.id),
                name=f"Avg. {label.name}",
                group_by="Annotation Metrics",
                is_visible=True,
                output_type="float",
                reverse_output=False,
                settings=label.settings,
            )
            present_config = asdict(present_config)
            if not any(
                config["id"] == present_config["id"] for config in column_config
            ):
                column_config.append(present_config)

    return column_config


def generate_timestamps(interval, start_date, end_date):
    timestamps = []
    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end_date:
        timestamps.append({"timestamp": current, "value": 0})
        if interval == "hour":
            current += timedelta(hours=1)
        elif interval == "day":
            current += timedelta(days=1)
        elif interval == "week":
            current += timedelta(weeks=1)
        elif interval == "month":
            current += timedelta(days=30)
        else:
            break  # Invalid interval, just stop
    return timestamps


def format_datetime_to_iso(val):
    """Convert a single datetime value to an ISO 8601 UTC string with 'Z' suffix."""
    if not val:
        return None


def flatten_dict(
    d: MutableMapping[str, Any],
    prefix: str = "",
    sep: str = ".",
) -> dict[str, Any]:
    """
    Flattens a nested dictionary into a single-level dictionary.

    Args:
        d (MutableMapping[str, Any]): The dictionary to flatten.
        prefix (str): The prefix for the keys in the flattened dictionary.
        sep (str): The separator to use between parent and child keys.

    Returns:
        dict[str, Any]: The flattened dictionary.
    """
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{prefix}{sep}{k}" if prefix else k
        if isinstance(v, MutableMapping):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)
    # Use strftime to produce a consistent UTC format, avoiding double-offset
    # when val is already timezone-aware (e.g. "2024-01-01T00:00:00+00:00Z").
    return val.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def format_datetime_fields_to_iso(rows, fields):
    """Convert datetime fields to ISO 8601 strings with 'Z' suffix in-place."""
    for item in rows:
        for field in fields:
            item[field] = format_datetime_to_iso(item.get(field))


# Helper function to extract date from datetime value
def extract_date(value):
    if isinstance(value, datetime):
        return value.date()
    elif isinstance(value, date):
        return value
    elif isinstance(value, str):
        # Try to parse as datetime string
        try:
            # Try ISO format first
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.date()
        except (ValueError, AttributeError):
            try:
                # Try common datetime formats
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                return dt.date()
            except ValueError:
                try:
                    dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
                    return dt.date()
                except ValueError:
                    # If all parsing fails, try date format
                    return datetime.strptime(value, "%Y-%m-%d").date()
    return None
