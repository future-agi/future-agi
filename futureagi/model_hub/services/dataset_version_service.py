"""Validated, append-only creation of dataset versions.

This is the single application-owned write path for new version snapshots. The
legacy Dataset/Row/Cell tables remain writable independently; this service never
mutates an existing version or its items.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TypeAlias

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models.user import User
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset, Row
from tfc.routers import force_primary

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
_MAX_ITEMS = 10_000


@dataclass(frozen=True)
class DatasetVersionItemInput:
    input: JsonValue
    expected: JsonValue | None = None
    metadata: JsonObject = field(default_factory=dict)
    example_id: uuid.UUID | None = None
    external_id: str | None = None
    source_trace_id: str | None = None
    source_span_id: str | None = None
    legacy_row_id: uuid.UUID | None = None


def _strict_json(value: object, *, path: str, depth: int = 0) -> JsonValue:
    """Validate JSON without coercing non-JSON objects to strings."""

    if depth > 100:
        raise ValidationError({path: "JSON nesting must not exceed 100 levels."})
    if type(value) is str:
        _validate_text(value, path=path)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValidationError({path: "Non-finite numbers are not valid JSON."})
        # JSONB expands exponents and returns integral numeric values as ints.
        # Normalize before hashing to preserve hashes after a database read.
        return int(Decimal(str(value))) if value.is_integer() else value
    if type(value) is list:
        return [
            _strict_json(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        if not all(type(key) is str for key in value):
            raise ValidationError({path: "JSON object keys must be strings."})
        for key in value:
            _validate_text(key, path=path)
        return {
            key: _strict_json(item, path=f"{path}.{key}", depth=depth + 1)
            for key, item in value.items()
        }
    raise ValidationError({path: "Value is not strict JSON."})


def _validate_text(value: str, *, path: str) -> None:
    if "\x00" in value:
        raise ValidationError({path: "Strings cannot contain null characters."})
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValidationError({path: "Strings must contain valid Unicode."}) from exc


def canonical_content_hash(
    *, input: JsonValue, expected: JsonValue | None, metadata: JsonObject
) -> str:
    """Return the exact SHA-256 hash for one validated item payload."""

    payload = {
        "input": _strict_json(input, path="input"),
        "expected": _strict_json(expected, path="expected"),
        "metadata": _strict_json(metadata, path="metadata"),
    }
    if not isinstance(payload["metadata"], dict):
        raise ValidationError({"metadata": "Metadata must be a JSON object."})
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValidationError({"input": "Values must be strict JSON."}) from exc
    return hashlib.sha256(encoded).hexdigest()


def _version_content_hash(
    *, dataset_id: uuid.UUID, item_records: list[dict[str, str | None]]
) -> str:
    payload = {
        "dataset_id": str(dataset_id),
        "items": sorted(item_records, key=lambda item: item["example_id"]),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


@transaction.atomic
@force_primary()
def create_dataset_version(
    *,
    dataset: Dataset,
    items: list[DatasetVersionItemInput] | tuple[DatasetVersionItemInput, ...],
    created_by: User | None,
    parent_version: DatasetVersion | None = None,
    description: str = "",
    metadata: JsonObject | None = None,
    source_revision: str = "",
) -> DatasetVersion:
    """Create one complete version and its items atomically.

    ``dataset`` is expected to come from an already membership-scoped query;
    this function rechecks active state and all related identities before any
    insert. A fresh UUID is always generated, so this service has no update or
    idempotency-key mode.
    """

    if not dataset.pk:
        raise _error("dataset", "Dataset must be persisted.")
    if not isinstance(items, (list, tuple)) or len(items) > _MAX_ITEMS:
        raise _error(
            "items", f"Items must be a bounded list of at most {_MAX_ITEMS} entries."
        )
    if not all(isinstance(item, DatasetVersionItemInput) for item in items):
        raise _error("items", "Every item must be a DatasetVersionItemInput.")
    authoritative_dataset = (
        Dataset.no_workspace_objects.select_for_update(of=("self",))
        .select_related("organization", "workspace")
        .filter(id=dataset.pk, deleted=False)
        .first()
    )
    if authoritative_dataset is None:
        raise _error("dataset", "Dataset is missing, deleted, or inaccessible.")
    if (
        authoritative_dataset.organization_id != dataset.organization_id
        or authoritative_dataset.workspace_id != dataset.workspace_id
    ):
        raise _error("dataset", "Dataset tenant changed; authorize the dataset again.")
    dataset = authoritative_dataset

    if created_by is not None:
        if not created_by.pk or not created_by.can_access_organization(
            dataset.organization
        ):
            raise _error("created_by", "Creator has no active organization membership.")
        if dataset.workspace_id and not created_by.can_write_to_workspace(
            dataset.workspace
        ):
            raise _error("created_by", "Creator cannot write to the dataset workspace.")
    if not isinstance(description, str) or not isinstance(source_revision, str):
        raise _error("version", "Description and source revision must be strings.")
    _validate_text(description, path="description")
    _validate_text(source_revision, path="source_revision")
    normalized_metadata = _strict_json(
        {} if metadata is None else metadata,
        path="metadata",
    )
    if not isinstance(normalized_metadata, dict):
        raise _error("metadata", "Version metadata must be a JSON object.")

    parent = None
    if parent_version is not None:
        if not parent_version.pk:
            raise _error("parent_version", "Parent version must be persisted.")
        parent = (
            DatasetVersion.no_workspace_objects.select_related("dataset")
            .filter(id=parent_version.pk, deleted=False)
            .first()
        )
        if parent is None:
            raise _error("parent_version", "Parent version is missing or deleted.")
        if parent.dataset_id != dataset.id:
            raise _error(
                "parent_version", "Parent version must belong to this dataset."
            )
        if (
            parent.organization_id != dataset.organization_id
            or parent.workspace_id != dataset.workspace_id
        ):
            raise _error(
                "parent_version", "Parent version tenant does not match dataset."
            )

    parent_external_ids: dict[str, uuid.UUID] = {}
    requested_external_ids = {
        item.external_id
        for item in items
        if isinstance(item.external_id, str) and item.external_id
    }
    if parent is not None:
        parent_external_ids = dict(
            DatasetVersionItem.no_workspace_objects.filter(
                dataset_version=parent,
                deleted=False,
                external_id__in=requested_external_ids,
            )
            .exclude(external_id="")
            .values_list("external_id", "example_id")
        )

    if any(
        item.legacy_row_id is not None and not isinstance(item.legacy_row_id, uuid.UUID)
        for item in items
    ):
        raise _error("items", "Legacy row IDs must be UUIDs.")
    legacy_ids = {item.legacy_row_id for item in items if item.legacy_row_id}
    legacy_rows = {
        row.id: row
        for row in Row.no_workspace_objects.filter(
            id__in=legacy_ids, dataset=dataset, deleted=False
        )
    }
    if len(legacy_rows) != len(legacy_ids):
        raise _error("items", "Every legacy row must belong to this dataset.")

    seen_examples: set[uuid.UUID] = set()
    seen_external_ids: set[str] = set()
    prepared_items: list[DatasetVersionItem] = []
    hash_records: list[dict[str, str | None]] = []
    for index, item in enumerate(items):
        if item.example_id is not None and not isinstance(item.example_id, uuid.UUID):
            raise _error(f"items[{index}].example_id", "Example ID must be a UUID.")
        if item.external_id is not None and not isinstance(item.external_id, str):
            raise _error(f"items[{index}].external_id", "External ID must be a string.")
        for name, value, maximum in (
            ("external_id", item.external_id, 500),
            ("source_trace_id", item.source_trace_id, 255),
            ("source_span_id", item.source_span_id, 255),
        ):
            if value is not None and (
                not isinstance(value, str) or len(value) > maximum
            ):
                raise _error(
                    f"items[{index}].{name}",
                    f"Value must be a string of at most {maximum} characters.",
                )
            if value is not None:
                _validate_text(value, path=f"items[{index}].{name}")
        external_id = item.external_id or None
        if external_id and external_id in seen_external_ids:
            raise _error(f"items[{index}].external_id", "Duplicate external ID.")
        if external_id:
            seen_external_ids.add(external_id)

        example_id = item.example_id
        inherited_id = parent_external_ids.get(external_id) if external_id else None
        if (
            example_id is not None
            and inherited_id is not None
            and example_id != inherited_id
        ):
            raise _error(
                f"items[{index}].example_id",
                "Explicit example ID conflicts with parent external ID.",
            )
        if example_id is None:
            example_id = inherited_id or (
                DatasetVersionItem.example_id_for_legacy_row(item.legacy_row_id)
                if item.legacy_row_id
                else uuid.uuid4()
            )
        if example_id in seen_examples:
            raise _error(f"items[{index}].example_id", "Duplicate example ID.")
        seen_examples.add(example_id)

        normalized_input = _strict_json(item.input, path=f"items[{index}].input")
        normalized_expected = _strict_json(
            item.expected, path=f"items[{index}].expected"
        )
        normalized_item_metadata = _strict_json(
            item.metadata, path=f"items[{index}].metadata"
        )
        if not isinstance(normalized_item_metadata, dict):
            raise _error(f"items[{index}].metadata", "Item metadata must be an object.")
        item_hash = canonical_content_hash(
            input=normalized_input,
            expected=normalized_expected,
            metadata=normalized_item_metadata,
        )
        hash_records.append(
            {
                "example_id": str(example_id),
                "external_id": external_id,
                "content_hash": item_hash,
            }
        )
        prepared_items.append(
            DatasetVersionItem(
                example_id=example_id,
                external_id=external_id,
                input=normalized_input,
                expected=normalized_expected,
                metadata=normalized_item_metadata,
                source_trace_id=item.source_trace_id or "",
                source_span_id=item.source_span_id or "",
                content_hash=item_hash,
                legacy_row_id=item.legacy_row_id,
            )
        )

    version = DatasetVersion(
        dataset=dataset,
        parent_version=parent,
        created_by=created_by,
        organization_id=dataset.organization_id,
        workspace_id=dataset.workspace_id,
        content_hash=_version_content_hash(
            dataset_id=dataset.id,
            item_records=hash_records,
        ),
        description=description,
        metadata=normalized_metadata,
        source_revision=source_revision,
    )
    version.save(force_insert=True)
    for prepared_item in prepared_items:
        prepared_item.dataset_version = version
    DatasetVersionItem.objects.bulk_create(prepared_items, batch_size=500)
    return version
