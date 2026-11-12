"""Bounded, local-only row-schema validation before immediate dataset writes."""

import json
import math
from types import SimpleNamespace

from django.db.models import Q
from django.http import Http404
from jsonschema import Draft202012Validator, SchemaError
from model_hub.models.choices import SourceChoices, determine_data_type
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.services.dataset_splits import scoped_dataset
from model_hub.utils.workspace_scope import legacy_dataset_filter
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
)

SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "minItems",
    "additionalProperties",
    "title",
    "description",
    "$schema",
}
JSON_TYPES = {"json", "array", "conversation", "checks", "trace", "images"}
EXCLUDED_SOURCES = [
    SourceChoices.EXPERIMENT.value,
    SourceChoices.EXPERIMENT_EVALUATION.value,
    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
    SourceChoices.EVALUATION.value,
    SourceChoices.EVALUATION_TAGS.value,
    SourceChoices.EVALUATION_REASON.value,
    SourceChoices.ANNOTATION_LABEL.value,
]


def validate_definition(schema):
    """Reject unsupported keywords rather than silently ignoring constraints."""
    try:
        encoded = json.dumps(schema, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValidationError("Schema must contain finite JSON values.") from None
    if len(encoded.encode()) > 65536:
        raise ValidationError("Schema exceeds 64 KiB.")
    pending = [(schema, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 512 or depth > 12:
            raise ValidationError("Schema exceeds the nesting or field limit.")
        if not isinstance(item, dict) or set(item) - SCHEMA_KEYS:
            raise ValidationError("Unsupported schema keyword or non-object schema.")
        if item.get("additionalProperties", True) not in (True, False):
            raise ValidationError("additionalProperties must be a boolean.")
        properties = item.get("properties", {})
        if not isinstance(properties, dict):
            raise ValidationError("Schema properties must be an object.")
        pending.extend((value, depth + 1) for value in properties.values())
        if "items" in item:
            pending.append((item["items"], depth + 1))
    if schema.get("type") != "object":
        raise ValidationError("A dataset schema must have type object.")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise ValidationError("Invalid JSON Schema definition.") from None
    return schema


def user_columns(dataset):
    return list(
        Column.no_workspace_objects.filter(
            dataset=dataset,
            deleted=False,
        ).exclude(source__in=EXCLUDED_SOURCES)
    )


def rejecting_schema(dataset):
    config = dataset.dataset_config
    schema = config.get("schema") if isinstance(config, dict) else None
    if not isinstance(schema, dict) or schema.get("enforce") != "reject":
        return None
    return validate_definition(schema.get("declared"))


def _rejecting_scope(request, resolved):
    """Stay inert unless the dataset the writer resolves declares rejection.

    Declarations are stored only through the scoped write lookup, so the same
    lookup applies once one exists and never narrows other datasets.
    """
    if resolved is None or rejecting_schema(resolved) is None:
        return None, None
    dataset = scoped_dataset(request, resolved.id, write=True)
    return dataset, rejecting_schema(dataset)


def _reject_json_constant(value):
    raise ValueError("Non-finite JSON number.")


def is_empty_cell_value(value):
    """Only null and blank strings clear a cell; zero and false are values."""
    return value is None or (isinstance(value, str) and not value.strip())


def normalize_added_cell_value(value, data_type):
    """Encode native JSON containers for TextField storage, leaving strings intact."""
    if data_type in ("json", "array") and isinstance(value, (dict, list)):
        try:
            return json.dumps(value, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise ValidationError(
                "JSON and array cells must contain finite JSON values."
            ) from None
    return value


def typed_value(value, data_type):
    """Interpret stored text using the column type, never the schema's desired type."""
    if value is None or value == "":
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if data_type in JSON_TYPES and isinstance(value, str):
        try:
            return json.loads(value, parse_constant=_reject_json_constant)
        except (ValueError, TypeError, RecursionError):
            raise ValidationError("A JSON column contains invalid JSON.") from None
    if data_type in ("integer", "float"):
        try:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError
            return number
        except (ValueError, TypeError, OverflowError):
            raise ValidationError(
                "A numeric column contains an invalid number."
            ) from None
    if data_type == "boolean":
        if value is True or str(value).lower() == "true":
            return True
        if value is False or str(value).lower() == "false":
            return False
    return value


def validate_rows(schema, rows, types, *, value_decoder=typed_value):
    """Validate rows using the caller's decoder for stored cell values."""
    validator = Draft202012Validator(schema)
    for index, row in enumerate(rows):
        if index >= 50000:
            raise ValidationError(
                "Schema validation supports at most 50,000 rows per request."
            )
        values = {
            name: value_decoder(value, types.get(name)) for name, value in row.items()
        }
        missing = [
            name
            for name in schema.get("required", [])
            if values.get(name) in (None, "")
        ]
        if missing:
            raise ValidationError(
                {
                    "schema": f"Row {index + 1}: required fields are empty: {', '.join(missing)}."
                }
            )
        error = next(validator.iter_errors(values), None)
        if error is not None:
            location = ".".join(str(part) for part in error.absolute_path) or "row"
            raise ValidationError(
                {"schema": f"Row {index + 1}, {location}: {error.message}"}
            )


def _refusal(error):
    if isinstance(error, Http404):
        return Response({"status": False, "message": "Dataset not found."}, status=404)
    return Response(
        {"status": False, "message": error.detail}, status=error.status_code
    )


def added_rows_refusal(request, dataset_id, rows):
    """Called before billing, row or cell creation; returns a normal API refusal."""
    try:
        # The same lookup as AddDataRowsView; an unresolved id stays its refusal.
        resolved = Dataset.objects.filter(id=dataset_id).first()
        dataset, schema = _rejecting_scope(request, resolved)
        if schema is None:
            return None
        columns = user_columns(dataset)
        types = {column.name: column.data_type for column in columns}
        if len(types) != len(columns):
            raise ValidationError("Schema enforcement requires unique column names.")
        proposed = []
        for row in rows:
            cells = row.get("cells", [])
            names = [cell["column_name"] for cell in cells]
            if len(set(names)) != len(names) or set(names) - set(types):
                raise ValidationError(
                    "Added rows contain duplicate or unknown columns."
                )
            values = dict.fromkeys(types)
            values.update(
                {cell["column_name"]: cell.get("value", "") for cell in cells}
            )
            # Validate the same normalized text the batch writer persists.
            proposed.append(
                {
                    key: (
                        None
                        if value is None
                        else str(normalize_added_cell_value(value, types[key]))
                    )
                    for key, value in values.items()
                }
            )
        validate_rows(schema, proposed, types)
    except (APIException, Http404) as error:
        return _refusal(error)
    return None


def imported_rows_refusal(request, dataset, data):
    """Validate every parsed import row before even new columns are created."""
    try:
        dataset, schema = _rejecting_scope(request, dataset)
        if schema is None:
            return None
        columns = user_columns(dataset)
        types = {column.name: column.data_type for column in columns}
        if len(types) != len(columns) or data.columns.duplicated().any():
            raise ValidationError("Schema enforcement requires unique column names.")
        for name in data.columns:
            if name not in types:
                types[name] = determine_data_type(data[name])

        def proposed():
            for row in data.to_dict(orient="records"):
                # The import writer stringifies absent non-media cells as "None".
                # Validate those stored bytes, not an imaginary JSON null.
                yield {name: str(row.get(name)) for name in types}

        validate_rows(schema, proposed(), types)
    except (APIException, Http404) as error:
        return _refusal(error)
    return None


def validate_cell_edit(attrs):
    """Validate in the serializer without modifying the immediate cell writer.

    Its decorator does not supply request context. Use the middleware's explicit
    scope, then authorize the row's dataset. The writer still checks URL identity.
    """
    organization = get_current_organization()
    workspace = get_current_workspace()
    if not organization or not workspace:
        return attrs  # The existing view still enforces its own request scope.
    # The same dataset rule as the schema save: a row of a legacy dataset with
    # no workspace is checked too, never skipped.
    row = (
        Row.no_workspace_objects.filter(
            legacy_dataset_filter(workspace, "dataset__workspace"),
            id=attrs["row_id"],
            deleted=False,
            dataset__organization=organization,
            dataset__deleted=False,
        )
        .select_related("dataset")
        .first()
    )
    if row is None:
        return attrs
    schema = rejecting_schema(row.dataset)
    if schema is None:
        return attrs
    request = SimpleNamespace(
        user=get_current_user(), organization=organization, workspace=workspace
    )
    scoped_dataset(request, row.dataset_id, write=True)
    columns = user_columns(row.dataset)
    by_id = {column.id: column for column in columns}
    column = by_id.get(attrs["column_id"])
    if column is None:
        return attrs  # Generated and evaluation columns use the existing refusal.
    types = {item.name: item.data_type for item in columns}
    if len(types) != len(columns):
        raise ValidationError("Schema enforcement requires unique column names.")
    values = dict.fromkeys(types)
    cells = Cell.no_workspace_objects.filter(
        Q(dataset=row.dataset) | Q(dataset__isnull=True),
        row=row,
        column_id__in=by_id,
        deleted=False,
    )
    for cell in cells:
        values[by_id[cell.column_id].name] = cell.value
    value = attrs.get("new_value", "")
    # Match the writer's empty handling and integer conversion exactly.
    if is_empty_cell_value(value):
        value = None
    elif column.data_type == "integer":
        try:
            value = str(int(float(value)))
        except (TypeError, ValueError, OverflowError):
            raise ValidationError("Invalid integer value.") from None
    elif column.data_type not in JSON_TYPES:
        value = str(value)
    values[column.name] = value
    validate_rows(schema, [values], types)
    return attrs
