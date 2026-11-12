"""Copy immutable selected trials into the live dataset grid, without executing a run.

Every requested example is either appended once or returned as a failure. All
accepted rows commit together; storage/admission errors roll back the entire
operation. This is a copy action, not a split, ground-truth or version writer.
"""
import json
import math
from decimal import Decimal, InvalidOperation
from uuid import UUID

from accounts.models.organization import Organization
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Max, Q
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentRunItem
from model_hub.services.dataset_schema import (
    EXCLUDED_SOURCES,
    JSON_TYPES,
    rejecting_schema,
    validate_rows,
)
from model_hub.services.dataset_version_service import _strict_json
from model_hub.services.experiment_run_writes import RunWriteError, _planned
from model_hub.services.persisted_run_comparison import _bindings
from model_hub.services.results_catalog import (
    ResultsReadError,
    bounded_list,
    checked_runs,
)
from model_hub.services.results_grid import assessed_scores
from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace,
    scoped_dataset_queryset,
)
from rest_framework.exceptions import PermissionDenied, ValidationError
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level
from tfc.routers import primary_required

try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request
except ImportError:
    log_and_deduct_cost_for_resource_request = None

MAX_COLUMNS = 100
MAX_COPY_BYTES = 16 * 1024 * 1024


class RowImportFailure(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ResultsReadError("Invalid column identifier.", 400) from None


def _authorize(request):
    organization = request_organization(request)
    workspace = request_workspace(request)
    if (organization is None or workspace is None
            or workspace.organization_id != organization.id
            or workspace.deleted or not workspace.is_active):
        raise PermissionDenied("An active organization and workspace are required.")
    level = get_effective_workspace_level(request.user, workspace.id)
    if level is None or level < Level.MEMBER:
        raise PermissionDenied("Workspace write access required.")
    return organization, workspace


def _mapping(data, source, target, pins):
    mapping = data.get("column_map")
    if target is not None:
        if data["new_dataset_name"]:
            raise ResultsReadError("A new dataset name is only used for a new dataset.", 400)
        if mapping is None:
            raise ResultsReadError("Map the result columns to the destination columns.", 400)
    elif not data["new_dataset_name"]:
        raise ResultsReadError("Enter a name for the new dataset.", 400)
    if mapping is None:
        mapping = {"inputs": "inputs", "output": "output"}
        if data["expected_from"] != "empty":
            mapping["expected"] = "expected"
        mapping.update({f"score:{metric}": f"score:{metric}" for metric in data["score_columns"]})
    if len(mapping) > MAX_COLUMNS:
        raise ResultsReadError("Copy at most 100 columns at a time.", 400)
    source_columns = {}
    selected = {}
    skipped = []
    for key, destination in mapping.items():
        if key in ("inputs", "output", "expected"):
            normalized = key
        elif key.startswith("input:") and key[6:]:
            normalized = key
        elif key.startswith("score:"):
            normalized = f"score:{_uuid(key[6:])}"
            if normalized[6:] not in pins:
                raise ResultsReadError("A selected score does not belong to this run.", 400)
        else:
            normalized = _uuid(key)
            column = Column.no_workspace_objects.filter(
                dataset=source, pk=normalized, deleted=False).first()
            if column is None:
                raise ResultsReadError("A source column is unavailable on this dataset.", 400)
            source_columns[normalized] = column
        if normalized in selected or normalized in skipped:
            raise ResultsReadError("Map each source column only once.", 400)
        if destination is None:
            skipped.append(normalized)
        else:
            selected[normalized] = _uuid(destination) if target is not None else destination
    if not selected or len(set(selected.values())) != len(selected):
        raise ResultsReadError("Map at least one column, with a different destination for each.", 400)
    if any(f"score:{metric}" not in selected for metric in data["score_columns"]):
        raise ResultsReadError("Map every selected score column.", 400)
    if data["expected_from"] == "output" and "expected" not in selected:
        raise ResultsReadError("Map the expected column to copy output as expected.", 400)
    return selected, skipped, source_columns


def _native_snapshot_value(value, kind):
    """Decode saved cell text without changing integer precision or blank text."""
    if value is None:
        return None
    if kind == "integer":
        if type(value) is int:
            return value
        if type(value) is float and math.isfinite(value) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                number = Decimal(value)
            except InvalidOperation:
                raise ValidationError("An integer input contains an invalid number.") from None
            if number.is_finite():
                # Bound exponent expansion before rounding or converting to int.
                if number.adjusted() >= MAX_COPY_BYTES:
                    raise ValidationError("An integer input exceeds the copy byte limit.")
                if number == number.to_integral_value():
                    return int(number)
        raise ValidationError("An integer input requires a finite whole number.")
    if kind in JSON_TYPES and isinstance(value, str):
        try:
            return _strict_json(json.loads(value), path="input")
        except (ValueError, RecursionError):
            raise ValidationError("A JSON input contains invalid JSON.") from None
    if kind == "boolean":
        if type(value) is bool:
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValidationError("A boolean input requires true or false.")
    if kind == "float":
        if type(value) is int or (type(value) is float and math.isfinite(value)):
            return value
        if isinstance(value, str):
            try:
                number = float(value)
            except (ValueError, OverflowError):
                raise ValidationError("A numeric input contains an invalid number.") from None
            if math.isfinite(number):
                return number
        raise ValidationError("A numeric input requires a finite number.")
    return value


def _source_value(key, example, item, scores, data, source_columns):
    if key == "inputs":
        return example.input
    if key == "expected":
        if data["expected_from"] == "empty":
            return None
        if data["expected_from"] == "keep":
            return example.expected
        key = "output"
    if key == "output":
        if item.output is None:
            raise RowImportFailure("output_unavailable", "This result has no saved output.")
        return item.output
    if key.startswith("score:"):
        score = scores.get(key[6:], {})
        if score.get("status") != "assessed" or score.get("value") is None:
            raise RowImportFailure("score_unavailable", "A selected score is not available for this try.")
        return score["value"]
    value = example.input
    if key.startswith("input:"):
        field = key[6:]
    else:
        field = key
        value = value.get("values") if isinstance(value, dict) else None
    if not isinstance(value, dict) or field not in value:
        raise RowImportFailure("input_unavailable", "A selected input is not available in the saved result.")
    value = value[field]
    return _native_snapshot_value(value, source_columns[key].data_type) if key in source_columns else value


def _cell_value(value, kind):
    """Preserve JSON types, zero/false/empty text; reject lossy scalar conversions."""
    _strict_json(value, path="value")
    if value is None:
        return None
    if kind in JSON_TYPES:
        if kind == "array" and not isinstance(value, list):
            raise ValidationError("An array column requires an array value.")
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if kind in ("integer", "float", "boolean"):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                raise ValidationError("The value does not match the destination column type.") from None
        if kind == "boolean":
            valid = type(value) is bool
        else:
            valid = type(value) is int or (type(value) is float and math.isfinite(value))
            if valid and kind == "integer":
                valid = int(value) == value
        if not valid:
            raise ValidationError("The value does not match the destination column type.")
        if kind == "integer":
            value = int(value)
        return json.dumps(value, allow_nan=False)
    if isinstance(value, str):
        return value
    if kind not in ("text", "others"):
        raise ValidationError("The value does not match the destination column type.")
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _schema_cell_value(value, kind):
    """Decode our encoded cells without the legacy integer-to-float coercion."""
    if kind == "integer":
        return _native_snapshot_value(value, kind)
    if value is None or value == "":
        return None
    if kind in JSON_TYPES or kind in ("float", "boolean"):
        return json.loads(value)
    return value


def _load_examples(run, source, ids, trial):
    try:
        plan = _planned(run)
    except RunWriteError as exc:
        raise ResultsReadError("The run does not have a saved row selection.", 409) from exc
    examples = bounded_list(DatasetVersionItem.no_workspace_objects.filter(
        Q(legacy_row__isnull=True) | Q(legacy_row__deleted=False, legacy_row__dataset=source),
        dataset_version=run.dataset_version, example_id__in=ids, deleted=False,
    ), ("input", "expected", "metadata"), limit=1000)
    items = bounded_list(ExperimentRunItem.no_workspace_objects.filter(
        run=run, example_id__in=ids, trial_number=trial, deleted=False,
    ), ("output", "error", "score_refs", "metadata"), limit=1000)
    return plan, {str(e.example_id): e for e in examples}, {str(i.example_id): i for i in items}


def _prepare(data, run, source, selected, source_columns, columns, pins, schema):
    ids = [str(value) for value in data["example_ids"]]
    plan, examples, items = _load_examples(run, source, ids, data["trial"])
    accepted, failures = [], []
    size = 0
    for example_id in ids:
        try:
            example = examples.get(example_id)
            if example is None or (example_id, data["trial"]) not in plan:
                raise RowImportFailure("example_unavailable", "This row or try is not available in the run.")
            item = items.get(example_id)
            if item is None or item.status != "completed" or item.error is not None:
                raise RowImportFailure("result_unavailable", "This try has no completed result to copy.")
            scores = {}
            if any(key.startswith("score:") for key in selected):
                try:
                    scores = assessed_scores(item.score_refs, pins)
                except ResultsReadError as exc:
                    raise RowImportFailure("invalid_result", "The saved score does not match this run.") from exc
            cells = {}
            for key, destination in selected.items():
                value = _source_value(key, example, item, scores, data, source_columns)
                cells[destination] = _cell_value(value, columns[destination]["type"])
            if schema is not None:
                named = {spec["name"]: cells.get(key) for key, spec in columns.items()}
                validate_rows(
                    schema, [named], {spec["name"]: spec["type"] for spec in columns.values()},
                    value_decoder=_schema_cell_value,
                )
            size += sum(len(value.encode("utf-8")) for value in cells.values() if value is not None)
            if size > MAX_COPY_BYTES:
                raise ResultsReadError("Selected values exceed 16 MiB. Select fewer rows or columns.", 503)
            accepted.append((example_id, item, cells))
        except RowImportFailure as exc:
            failures.append({"example_id": example_id, "code": exc.code, "message": str(exc)})
        except (
            DjangoValidationError, ValidationError, ValueError, TypeError, OverflowError, RecursionError,
        ):
            failures.append({"example_id": example_id, "code": "invalid_value",
                             "message": "A value does not match the destination column type or dataset schema."})
    return accepted, failures


def _admit(organization, workspace, target, count):
    if log_and_deduct_cost_for_resource_request is None:
        return []
    calls = []
    if target is None:
        calls.append((APICallTypeChoices.DATASET_ADD.value, None))
    existing = Row.no_workspace_objects.filter(dataset=target).count() if target else 0
    calls.append((APICallTypeChoices.ROW_ADD.value, {"total_rows": existing + count}))
    entries = []
    for call_type, config in calls:
        kwargs = {"organization": organization, "workspace": workspace, "api_call_type": call_type}
        if config is not None:
            kwargs["config"] = config
        entry = log_and_deduct_cost_for_resource_request(**kwargs)
        if entry is None:
            raise ResultsReadError("The dataset limit could not be checked. No rows were added.", 503)
        if entry.status == APICallStatusChoices.RESOURCE_LIMIT.value:
            raise ResultsReadError("The dataset or row limit has been reached. No rows were added.", 429)
        entries.append(entry)
    return entries


def _create_target(data, organization, workspace, user, selected):
    target = Dataset.no_workspace_objects.create(
        name=data["new_dataset_name"], organization=organization, workspace=workspace,
        user=user, dataset_config={"eval_recommendations": ["Deterministic Evals"]},
    )
    actual, config = {}, {}
    for selector, name in selected.items():
        column = Column.no_workspace_objects.create(
            dataset=target, name=name, data_type="json", source="OTHERS")
        actual[name] = str(column.id)
        spec = {"is_visible": True, "is_frozen": None}
        role = {"inputs": "input", "output": "output", "expected": "expected"}.get(selector)
        if role:
            spec["role"] = role
        config[str(column.id)] = spec
    target.column_order = list(actual.values())
    target.column_config = config
    target.save(update_fields=["column_order", "column_config"])
    return target, actual


@primary_required
@transaction.atomic
def copy_results_to_dataset(request, dataset_id, data):
    organization, workspace = _authorize(request)
    # Serialize creation/name checks and quota admission before dataset locks.
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    is_new = str(dataset_id) == "new"
    ids = {str(data["source_dataset_id"])}
    if not is_new:
        ids.add(str(dataset_id))
    datasets = {str(ds.id): ds for ds in scoped_dataset_queryset(request)
                .filter(id__in=ids).order_by("id").select_for_update(of=("self",))}
    if set(datasets) != ids:
        raise ResultsReadError("Dataset not found.", 404)
    source = datasets[str(data["source_dataset_id"])]
    target = None if is_new else datasets[str(dataset_id)]
    run = checked_runs(source, [data["run_id"]])[0]
    pins = _bindings(run)
    selected, skipped, source_columns = _mapping(data, source, target, pins)
    schema = None
    if target is None:
        if Dataset.no_workspace_objects.filter(
                organization=organization, name=data["new_dataset_name"], deleted=False).exists():
            raise ResultsReadError("A dataset with this name already exists. Choose another name.", 400)
        columns = {name: {"name": name, "type": "json"} for name in selected.values()}
    else:
        writable_columns = bounded_list(Column.no_workspace_objects.filter(
            dataset=target, deleted=False).exclude(source__in=EXCLUDED_SOURCES)
            .select_for_update(), ("metadata",), limit=MAX_COLUMNS)
        allowed = {str(c.id) for c in writable_columns}
        if not set(selected.values()) <= allowed:
            raise ResultsReadError("A destination column is unavailable or cannot accept copied values.", 400)
        columns = {str(c.id): {"name": c.name, "type": c.data_type} for c in writable_columns}
        schema = rejecting_schema(target)
        if schema is not None and len({c.name for c in writable_columns}) != len(writable_columns):
            raise ResultsReadError("Dataset schema checks require unique column names.", 400)
    accepted, failures = _prepare(data, run, source, selected, source_columns, columns, pins, schema)
    result = {
        "dataset_id": str(target.id) if target else None,
        "dataset_name": target.name if target else None,
        "dataset_url": f"/dashboard/develop/{target.id}?tab=data" if target else None,
        "created": False, "outcome": "failed", "rows_requested": len(data["example_ids"]),
        "rows_added": 0, "rows_failed": len(failures), "trial": data["trial"],
        "column_map": dict(selected) if target else {}, "skipped_columns": skipped,
        "rows": [], "failures": failures,
    }
    if not accepted:
        return result
    entries = _admit(organization, workspace, target, len(accepted))
    if target is None:
        target, actual = _create_target(data, organization, workspace, request.user, selected)
    else:
        actual = {key: key for key in columns}
    last_order = Row.all_objects.filter(dataset=target).aggregate(value=Max("order"))["value"]
    next_order = 0 if last_order is None else last_order + 1
    rows, cells = [], []
    for index, (example_id, item, values) in enumerate(accepted):
        provenance = {
            "source_dataset_id": str(source.id), "dataset_version_id": str(run.dataset_version_id),
            "run_id": str(run.id), "example_id": example_id, "result_id": str(item.id),
            "trial": data["trial"], "note": data["version_description"],
            "expected_from": data["expected_from"],
            "needs_review": data["needs_review"] or data["expected_from"] == "output",
        }
        if data.get("expected_source"):
            provenance["expected_source"] = data["expected_source"]
        row = Row(dataset=target, order=next_order + index, metadata={"result_import": provenance})
        rows.append(row)
        for destination, column_id in actual.items():
            cells.append(Cell(dataset=target, row=row, column_id=column_id, value=values.get(destination)))
        result["rows"].append({"example_id": example_id, "result_id": str(item.id), "row_id": str(row.id)})
    Row.no_workspace_objects.bulk_create(rows, batch_size=500)
    Cell.no_workspace_objects.bulk_create(cells, batch_size=1000)
    for entry in entries:
        entry.status = APICallStatusChoices.SUCCESS.value
        entry.save()
    result.update(
        dataset_id=str(target.id), dataset_name=target.name,
        dataset_url=f"/dashboard/develop/{target.id}?tab=data", created=is_new,
        outcome="partial" if failures else "completed", rows_added=len(rows),
        column_map={selector: actual[destination] for selector, destination in selected.items()},
    )
    return result
