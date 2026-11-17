"""Shared, scoped predicates for the dataset's stored evaluation results."""

import json
import operator
import re
from decimal import Decimal, InvalidOperation

from django.db.models import Count, F, Q
from rest_framework.exceptions import ValidationError

from model_hub.models.choices import CellStatus, DataTypeChoices, SourceChoices
from model_hub.services.bounded_dataset_read import BoundedDatasetReadDeadline

FAILED_FILTERS = frozenset(("system:row_failed", "system:row_failed_in"))
EVAL_SOURCES = frozenset(
    (
        SourceChoices.EVALUATION.value,
        SourceChoices.EXPERIMENT_EVALUATION.value,
        SourceChoices.OPTIMISATION_EVALUATION.value,
    )
)


def failure_columns(columns_map):
    """Only scored boolean columns have a stored pass/fail verdict.

    Numeric scores have no threshold in the classic column contract. Do not
    invent one or interpret execution errors/ordinary input booleans as fails.
    """
    return {
        key: column
        for key, column in columns_map.items()
        if column.source in EVAL_SOURCES
        and column.data_type == DataTypeChoices.BOOLEAN.value
    }


def nested_filter_path(item, columns_map):
    """Validate a bounded literal path against the authorized column inventory."""
    key = str(item.get("column_id", ""))
    if "::" not in key:
        return None
    column_id, path = key.split("::", 1)
    column = columns_map.get(column_id)
    if column is None:
        raise ValidationError({"column_id": "Unknown or inaccessible dataset column."})
    if column.data_type not in (
        DataTypeChoices.JSON.value,
        DataTypeChoices.ARRAY.value,
    ):
        raise ValidationError(
            {"column_id": "Nested dataset paths require a JSON or array column."}
        )
    key_pattern = r"[^.\[\]\x00-\x1f]+"
    # formatPath emits JSON-string brackets for non-identifier object keys.
    quoted_key = r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
    bracket_pattern = rf"\[(?:0|[1-9][0-9]*|\*|{quoted_key})\]"
    grammar = (
        rf"(?:{key_pattern}|{bracket_pattern})"
        rf"(?:\.{key_pattern}|{bracket_pattern})*"
    )
    if len(path) > 512 or not re.fullmatch(grammar, path):
        raise ValidationError({"filters": "Malformed nested dataset path."})
    tokens = []
    for key, bracket in re.findall(
        rf"({key_pattern})|\[({quoted_key}|[0-9]+|\*)\]", path
    ):
        if key:
            if "::" in key:
                raise ValidationError({"filters": "Malformed nested dataset path."})
            tokens.append(key)
        elif bracket.startswith('"'):
            tokens.append(json.loads(bracket))
        else:
            tokens.append(None if bracket == "*" else int(bracket))
    if len(tokens) > 32:
        raise ValidationError({"filters": "Malformed nested dataset path."})
    if item["filter_config"].get("filter_type") not in (
        "text",
        "number",
        "boolean",
        "array",
    ):
        raise ValidationError({"filters": "Unsupported nested dataset filter type."})
    return column_id, tokens


def _nested_values(raw, tokens):
    try:
        values = [json.loads(raw)]
    except (TypeError, ValueError, RecursionError):
        return []
    for token in tokens:
        children = []
        for value in values:
            if token is None and isinstance(value, list):
                children.extend(value)
            elif isinstance(token, int) and isinstance(value, list):
                if token < len(value):
                    children.append(value[token])
            elif isinstance(token, str) and isinstance(value, dict):
                children.append(value.get(token))
        values = children
    return values


def _nested_decimal(value):
    """Parse decimal operands without rounding integers or accepting booleans."""
    if type(value) not in (str, int, float):
        return None
    text = str(value).strip()
    if not re.fullmatch(
        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text
    ):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _nested_matches(values, config):
    # Missing keys, JSON null, blank values and absent cells are null. Negative
    # value comparisons require a present, correctly typed leaf.
    values = [value for value in values if value is not None and value != ""]
    kind, op = config["filter_type"], config["filter_op"]
    if op in ("is_null", "is_not_null"):
        return bool(values)
    negative = op in ("not_equals", "not_in", "not_contains", "not_between")
    op = op.removeprefix("not_") if negative else op
    expected = config.get("filter_value")
    if kind == "number":
        values = [
            number
            for value in values
            if type(value) in (int, float)
            and (number := _nested_decimal(value)) is not None
        ]
        if op == "between":
            low, high = map(_nested_decimal, expected)
            matched = (
                low is not None
                and high is not None
                and any(low <= value <= high for value in values)
            )
        else:
            compare = {
                "equals": operator.eq,
                "greater_than": operator.gt,
                "greater_than_or_equal": operator.ge,
                "less_than": operator.lt,
                "less_than_or_equal": operator.le,
            }[op]
            number = _nested_decimal(expected)
            matched = number is not None and any(
                compare(value, number) for value in values
            )
    elif kind == "boolean":
        values = [value for value in values if type(value) is bool]
        matched = any(value == (str(expected).lower() == "true") for value in values)
    else:
        if kind == "array":
            values = [
                leaf
                for value in values
                for leaf in (value if isinstance(value, list) else [value])
                if leaf is not None
            ]
        needles = expected if isinstance(expected, list) else [expected]
        array_membership = kind == "array" and op == "contains"
        compare = {
            "equals": operator.eq,
            "in": operator.eq,
            "contains": lambda value, needle: needle in value,
            "starts_with": str.startswith,
            "ends_with": str.endswith,
        }[op]

        def matches(value, needle):
            # Numeric equality includes array membership and rehydrated text
            # actions; it must not depend on JS/Python number rendering.
            equality = array_membership or op in ("equals", "in")
            if equality and type(value) in (int, float):
                number, operand = _nested_decimal(value), _nested_decimal(needle)
                return number is not None and operand is not None and number == operand
            if array_membership:
                # String elements are exact, without coercion or case folding.
                return type(value) is type(needle) and value == needle
            text = (
                value
                if isinstance(value, str)
                else json.dumps(value, separators=(",", ":"))
            )
            return compare(text.casefold(), str(needle).casefold())

        matched = any(matches(value, needle) for value in values for needle in needles)
    return bool(values) and (not matched if negative else matched)


def apply_nested_filter(cells, rows, item, parsed):
    """Scan only the authorized base column; invalid JSON has no nested values."""
    column_id, tokens = parsed
    deadline = BoundedDatasetReadDeadline.start()
    deadline.before_query()
    candidates = cells.filter(
        column_id=column_id,
        row_id__in=rows.values("id"),
        deleted=False,
        row__deleted=False,
        column__deleted=False,
    ).values_list("row_id", "value")
    matching = []
    for row_id, raw in candidates.iterator(chunk_size=100):
        deadline.checkpoint()
        if _nested_matches(_nested_values(raw, tokens), item["filter_config"]):
            matching.append(row_id)
    deadline.checkpoint()
    # Invert the non-null set to include rows with no cell at all.
    if item["filter_config"]["filter_op"] == "is_null":
        return rows.exclude(id__in=matching)
    return rows.filter(id__in=matching)


def validate_read_columns(filters, sort, columns_map):
    from model_hub.services.dataset_splits import validate_split_filter

    available = set(columns_map)
    eval_ids = set(failure_columns(columns_map))
    for item in filters:
        key = str(item.get("column_id", ""))
        config = item.get("filter_config") or {}
        if nested_filter_path(item, columns_map) is not None:
            continue
        if key == "system:splits":
            validate_split_filter(config)
        elif key in FAILED_FILTERS:
            value = config.get("filter_value")
            if key == "system:row_failed":
                valid = (
                    config.get("filter_type") == "boolean"
                    and config.get("filter_op") == "equals"
                    and value is True
                )
            else:
                valid = (
                    config.get("filter_type") == "categorical"
                    and config.get("filter_op") == "in"
                    and isinstance(value, list)
                    and bool(value)
                    and all(isinstance(v, str) and v in eval_ids for v in value)
                )
            if not valid:
                raise ValidationError(
                    {"filters": "Invalid or inaccessible failed-evaluation filter."}
                )
        elif key not in available:
            raise ValidationError(
                {"column_id": "Unknown or inaccessible dataset column."}
            )
    if any(
        str(item.get("column_id", "")) not in available | {"system:order"}
        for item in sort
    ):
        raise ValidationError(
            {"column_id": "Unknown or inaccessible dataset sort column."}
        )


def failed_cells(cells, columns_map, selected=None):
    ids = failure_columns(columns_map)
    if selected is not None:
        ids = {key: column for key, column in ids.items() if key in selected}
    # all callers supply cells bound to the authorized dataset and visible rows.
    return cells.filter(
        column_id__in=ids,
        deleted=False,
        row__deleted=False,
        column__deleted=False,
        status=CellStatus.PASS.value,
    ).filter(Q(value__iexact="false") | Q(value__iexact="Failed"))


def rerun_source_run(snapshot, variant_id, run_ids=()):
    """The run whose own checks pick a variant's failed rows, or None.

    Of the live runs bound to the variant in this snapshot (only those the
    request named, when it named some), the newest, as the Results catalog
    orders runs. A named run that is gone refuses, never falls back.
    """
    from model_hub.models.native_experiment_runs import NativeRunBinding

    bindings = NativeRunBinding.objects.select_related("run").filter(
        snapshot=snapshot, variant_id=variant_id, run__deleted=False
    )
    if run_ids:
        bindings = bindings.filter(run_id__in=run_ids)
    binding = bindings.order_by(
        F("run__ordinal").desc(nulls_last=True), "-run__created_at", "-run_id"
    ).first()
    if binding is None and run_ids:
        raise ValueError(
            "The run you chose is no longer available, so its failed rows are "
            "unknown. Nothing was rerun."
        )
    return binding.run if binding else None


def rerun_failed_rows_for(
    snapshot, variant_id, column_ids, rows, examples, run_ids=(), cell_runs=None
):
    """Failed rows only, for one variant of the snapshot.

    A row chosen through a Results cell is judged by the run that cell names
    (`cell_runs` maps (column id, row id) to run ids); every other row by the
    run the request named, else the variant's newest run.
    """
    default = rerun_source_run(snapshot, variant_id, run_ids)
    groups = {}
    for row in map(str, rows):
        named = set()
        for column_id in column_ids:
            named |= (cell_runs or {}).get((str(column_id), row), set())
        for run_id in named or {None}:
            groups.setdefault(run_id, set()).add(row)
    failed = set()
    for run_id, group in groups.items():
        run = rerun_source_run(snapshot, variant_id, [run_id]) if run_id else default
        failed |= rerun_failed_rows(
            snapshot.snapshot_dataset_id, column_ids, group, run, examples
        )
    return failed


def rerun_failed_rows(dataset_id, output_column_ids, row_ids, run=None, examples=None):
    """The rows "Failed rows only" reruns, as the Rerun menu counts them.

    A row failed when it errored, or when it failed a check. A row the selected
    `run` recorded (`examples` maps snapshot row ids to example ids) is judged by
    that run alone: a try of its that errored, hit infrastructure trouble or was
    cancelled, or failed one of its own pinned checks, never another run's stored
    cells. Any other row reads the stored cells: an ERROR output, or a failed
    pass/fail evaluator column of one of these outputs (its source_id is
    "<variant>-<output>-sourceid-<metric>").
    """
    from model_hub.models.develop_dataset import Cell, Column
    from model_hub.services.results_catalog import ResultsReadError
    from model_hub.services.results_matrix import failing_examples

    cells = Cell.no_workspace_objects.filter(dataset_id=dataset_id, row_id__in=row_ids)
    failed = set()
    stored = {str(row_id) for row_id in row_ids}
    if run is not None:
        row_of = {examples[row]: row for row in stored if row in (examples or {})}
        try:
            recorded, failing, errored = failing_examples(run, list(row_of))
        except ResultsReadError as error:
            raise ValueError(
                "The run's results can't be read, so its failed rows are unknown. "
                "Nothing was rerun."
            ) from error
        failed |= {row_of[example] for example in failing | errored}
        stored -= {row_of[example] for example in recorded}
    failed |= set(
        cells.filter(
            row_id__in=stored,
            column_id__in=output_column_ids,
            deleted=False,
            status=CellStatus.ERROR.value,
        ).values_list("row_id", flat=True)
    )
    outputs = Q()
    for column_id in output_column_ids:
        outputs |= Q(source_id__contains=f"-{column_id}-sourceid-")
    if outputs and stored:
        checks = {
            str(column.pk): column
            for column in Column.no_workspace_objects.filter(
                outputs, dataset_id=dataset_id, deleted=False
            )
        }
        cells = cells.filter(row_id__in=stored)
        failed |= set(failed_cells(cells, checks).values_list("row_id", flat=True))
    return {str(row_id) for row_id in failed}


def apply_failed_filter(cells, rows, item, columns_map):
    selected = (
        item["filter_config"]["filter_value"]
        if item["column_id"] == "system:row_failed_in"
        else None
    )
    matching = failed_cells(cells, columns_map, selected)
    return rows.filter(id__in=matching.values("row_id"))


def failure_counts(cells, columns_map):
    matching = failed_cells(cells, columns_map)
    counts = {
        str(row["column_id"]): row["count"]
        for row in matching.order_by()
        .values("column_id")
        .annotate(count=Count("row_id", distinct=True))
    }
    return {
        "failed_rows": matching.order_by().values("row_id").distinct().count(),
        "failed_by_eval": [
            {"column_id": key, "name": column.name, "count": counts.get(key, 0)}
            for key, column in sorted(
                failure_columns(columns_map).items(),
                key=lambda item: (item[1].name, item[0]),
            )
        ],
    }
