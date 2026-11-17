"""Server-selected row scopes and durable generation-input fingerprints.

No snapshot means the output predates input tracking, so 'changed' includes it.
Snapshots contain only digests, never copies of prompt content or credentials.
"""

import hashlib
import inspect
import json
import re
from functools import wraps

from model_hub.models.choices import CellStatus, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.generated_input_snapshot import GeneratedInputSnapshot

# Prompt settings the runner never reads. Derived variables are extracted from
# generated outputs after rows are selected; the others are model-picker display
# values. Hashing them would mark unchanged rows as changed.
NON_EXECUTION_PROMPT_SETTINGS = frozenset(
    {"derived_variables", "logoUrl", "isAvailable"}
)


def generation_config(column):
    if column.source == SourceChoices.RUN_PROMPT.value:
        from model_hub.models.run_prompt import RunPrompter

        prompt = RunPrompter.objects.get(
            id=column.source_id, dataset_id=column.dataset_id
        )
        fields = (
            "model",
            "messages",
            "temperature",
            "frequency_penalty",
            "presence_penalty",
            "max_tokens",
            "top_p",
            "response_format",
            "tool_choice",
            "output_format",
            "run_prompt_config",
        )
        config = {field: getattr(prompt, field) for field in fields}
        config["run_prompt_config"] = {
            key: value
            for key, value in (prompt.run_prompt_config or {}).items()
            if key not in NON_EXECUTION_PROMPT_SETTINGS
        }
        config["tools"] = sorted(
            str(pk) for pk in prompt.tools.values_list("pk", flat=True)
        )
        return config
    return {
        key: value
        for key, value in (column.metadata or {}).items()
        if key != "concurrency"
    }


def input_fingerprints(column, row_ids=None):
    """Hash inputs before execution; never infer changes from bulk-write timestamps."""
    rows = Row.objects.filter(dataset_id=column.dataset_id, deleted=False)
    if row_ids is not None:
        rows = rows.filter(id__in=row_ids)
    values = {str(pk): [] for pk in rows.values_list("id", flat=True)}
    config = generation_config(column)
    columns = Column.objects.filter(
        dataset_id=column.dataset_id, deleted=False
    ).exclude(id=column.id)
    if config.get("column_id"):
        columns = columns.filter(id=config["column_id"])
    else:
        # UUID references also occur outside templates (conditional branch inputs).
        # Named inputs may use Jinja expressions, mustache sections or f-strings.
        text = json.dumps(config, sort_keys=True, default=str)
        templates = " ".join(re.findall(r"\{[^}]+\}", text))
        dependencies = [
            str(pk)
            for pk, name in columns.values_list("id", "name")
            if str(pk) in text
            or str(pk).replace("-", "_") in text
            or re.search(
                r"(?<![\w])" + re.escape(name.split(".")[0]) + r"(?![\w])", templates
            )
        ]
        columns = columns.filter(id__in=dependencies)
    inputs = (
        Cell.objects.filter(
            dataset_id=column.dataset_id,
            row_id__in=values,
            row__deleted=False,
            column__in=columns,
            deleted=False,
        )
        .order_by("column_id", "id")
        .values_list("row_id", "column_id", "value")
    )
    for row_id, column_id, value in inputs:
        values[str(row_id)].append([str(column_id), value])
    return {
        row_id: "sha256:"
        + hashlib.sha256(
            json.dumps(
                [config, inputs], sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()
        for row_id, inputs in values.items()
    }


def selected_rows(column, scope):
    rows = Row.objects.filter(dataset_id=column.dataset_id, deleted=False).order_by(
        "order", "id"
    )
    if scope == "failed":
        return list(
            map(
                str,
                rows.filter(
                    id__in=Cell.objects.filter(
                        dataset_id=column.dataset_id,
                        column=column,
                        deleted=False,
                        status__in=[CellStatus.ERROR.value, "fail", "failed"],
                    ).values("row_id")
                ).values_list("id", flat=True),
            )
        )
    if scope == "changed":
        current = input_fingerprints(column)
        previous = {
            str(row): value
            for row, value in GeneratedInputSnapshot.objects.filter(
                column=column,
                row__dataset_id=column.dataset_id,
                row__deleted=False,
            ).values_list("row_id", "fingerprint")
        }
        return [
            str(pk)
            for pk in rows.values_list("id", flat=True)
            if previous.get(str(pk)) != current[str(pk)]
        ]
    return list(map(str, rows.values_list("id", flat=True)))


def save_fingerprints(column, fingerprints):
    # An interrupted worker must not acknowledge inputs it never produced output for.
    completed = set(
        map(
            str,
            Cell.objects.filter(
                column=column,
                dataset_id=column.dataset_id,
                row__dataset_id=column.dataset_id,
                row__deleted=False,
                row_id__in=fingerprints,
                deleted=False,
            )
            .exclude(status=CellStatus.RUNNING.value)
            .values_list("row_id", flat=True),
        )
    )
    GeneratedInputSnapshot.objects.bulk_create(
        [
            GeneratedInputSnapshot(
                column=column, row_id=row_id, fingerprint=fingerprint
            )
            for row_id, fingerprint in fingerprints.items()
            if row_id in completed
        ],
        update_conflicts=True,
        unique_fields=["column", "row"],
        update_fields=["fingerprint"],
    )


def track_generation_inputs(function):
    """Wrap the actual worker, including initial creation and direct task retries."""
    signature = inspect.signature(function)

    @wraps(function)
    def tracked(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        if function.__name__ == "conditional_column_async":
            from model_hub.views.utils.disabled_code import conditional_config_uses_code

            if conditional_config_uses_code(arguments.arguments["config"]):
                return function(*args, **kwargs)
        column = Column.objects.get(
            id=arguments.arguments["new_column_id"],
            dataset_id=arguments.arguments["dataset_id"],
            deleted=False,
        )
        fingerprints = input_fingerprints(column, arguments.arguments.get("row_ids"))
        result = function(*args, **kwargs)
        # Missing inputs and worker-level errors must not leave selected outputs
        # permanently running. Never touch a row outside this invocation's scope.
        outputs = Cell.objects.filter(
            column=column,
            dataset_id=column.dataset_id,
            row_id__in=fingerprints,
            row__deleted=False,
            deleted=False,
        )
        reason = json.dumps(
            {"reason": "Generation did not produce an output for this row"}
        )
        outputs.filter(status=CellStatus.RUNNING.value).update(
            value=None,
            status=CellStatus.ERROR.value,
            value_infos=reason,
        )
        existing = set(map(str, outputs.values_list("row_id", flat=True)))
        missing = Row.objects.filter(
            id__in=set(fingerprints) - existing,
            dataset_id=column.dataset_id,
            deleted=False,
        ).values_list("id", flat=True)
        Cell.objects.bulk_create(
            [
                Cell(
                    column=column,
                    dataset_id=column.dataset_id,
                    row_id=row_id,
                    status=CellStatus.ERROR.value,
                    value_infos=reason,
                )
                for row_id in missing
            ]
        )
        save_fingerprints(column, fingerprints)
        from model_hub.models.choices import StatusType

        Column.objects.filter(id=column.id, dataset_id=column.dataset_id).update(
            status=StatusType.COMPLETED.value
        )
        return result

    return tracked
