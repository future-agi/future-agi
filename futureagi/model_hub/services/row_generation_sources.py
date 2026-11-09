"""Bounded, authorized examples and indexed passages for append generation."""

import json

from django.db.models import Sum
from django.db.models.functions import Length
from rest_framework.exceptions import NotFound, ValidationError

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Cell, Row
from model_hub.utils.workspace_scope import scoped_knowledge_base_queryset

MAX_SOURCE_BYTES = 50_000
MAX_PASSAGES = 20


def source_config(data):
    result = {}
    if "reference_row_ids" in data:
        ids = [str(value) for value in data["reference_row_ids"]]
        if not ids or len(ids) > 50 or len(set(ids)) != len(ids):
            raise ValidationError("Select between 1 and 50 distinct saved rows.")
        result["reference_row_ids"] = ids
    if data.get("kb_id"):
        kinds = {column["name"]: column["data_type"] for column in data["columns"]}
        if any(kinds.get(name) != "text" for name in ("context", "citation")):
            raise ValidationError(
                "Knowledge-base generation needs text columns named context and citation."
            )
        result["kb_id"] = str(data["kb_id"])
    return result


def selected_rows(dataset, columns, ids):
    found = {
        str(value)
        for value in Row.objects.filter(
            dataset=dataset, id__in=ids, deleted=False
        ).values_list("id", flat=True)
    }
    if found != set(ids):
        raise NotFound("Reference rows not found")
    cells = Cell.objects.filter(
        dataset=dataset, row_id__in=ids, column__in=columns, deleted=False
    )
    # Check in storage before materializing text; the final UTF-8 envelope is checked below.
    if (cells.aggregate(size=Sum(Length("value")))["size"] or 0) > MAX_SOURCE_BYTES:
        raise ValidationError(
            "References are too large. Select fewer or shorter examples."
        )
    values = {
        (str(row), str(column)): value
        for row, column, value in cells.values_list("row_id", "column_id", "value")
    }
    result = []
    for row in ids:
        record = {}
        for column in columns:
            value = values.get((row, str(column.id)))
            if value is not None and column.data_type in (
                "integer",
                "float",
                "boolean",
                "json",
                "array",
            ):
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    raise ValidationError(
                        "A selected row has an invalid typed value."
                    ) from None
            record[column.name] = value
        result.append({"row_id": row, "values": record})
    return result


def indexed_passages(kb, file_ids):
    from agentic_eval.core.database.ch_vector import ClickHouseVectorDB

    db = ClickHouseVectorDB()
    try:
        records = db.execute_read(
            "SELECT metadata.key, metadata.value FROM syn FINAL "
            "WHERE eval_id = %(kb)s AND deleted = 0 "
            "AND metadata.value[indexOf(metadata.key, 'organization_id')] = %(org)s "
            "AND metadata.value[indexOf(metadata.key, 'file_id')] IN %(files)s "
            "ORDER BY id LIMIT 20",
            {
                "kb": str(kb.id),
                "org": str(kb.organization_id),
                "files": tuple(file_ids),
            },
            settings={"max_result_bytes": MAX_SOURCE_BYTES * 2},
            max_result_rows=MAX_PASSAGES,
        )
    except Exception as error:
        raise ValidationError(
            "Knowledge-base passages are unavailable. Check indexing and try again."
        ) from error
    finally:
        db.close()
    result = []
    for keys, values in records:
        record = dict(zip(keys, values, strict=True))
        if (
            record.get("organization_id") != str(kb.organization_id)
            or record.get("file_id") not in file_ids
            or not record.get("chunk_text")
            or not record.get("chunk_id")
        ):
            raise ValidationError("Knowledge-base passage metadata is invalid.")
        result.append(
            {
                "text": record["chunk_text"],
                "citation": f"kb:{kb.id}/file:{record['file_id']}/chunk:{record['chunk_id']}",
            }
        )
    return result


def generation_sources(request, dataset, columns, config):
    result = {}
    if "reference_row_ids" in config:
        result["reference_rows"] = selected_rows(
            dataset, columns, config["reference_row_ids"]
        )
    if config.get("kb_id"):
        kb = scoped_knowledge_base_queryset(request).filter(id=config["kb_id"]).first()
        if kb is None or kb.organization_id != dataset.organization_id:
            raise NotFound("Knowledge base not found")
        if kb.status != StatusType.COMPLETED.value:
            raise ValidationError("Wait for the knowledge base to finish indexing.")
        file_ids = [
            str(value)
            for value in kb.files.filter(
                deleted=False, status=StatusType.COMPLETED.value
            ).values_list("id", flat=True)
        ]
        if not file_ids:
            raise ValidationError("The knowledge base has no indexed files.")
        result["passages"] = indexed_passages(kb, file_ids)
        if not result["passages"]:
            raise ValidationError("The knowledge base has no indexed passages.")
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_SOURCE_BYTES:
        raise ValidationError(
            "References are too large. Select fewer or shorter examples."
        )
    return result


def validate_citations(rows, sources):
    passages = sources.get("passages")
    if not passages:
        return
    allowed = {(passage["citation"], passage["text"]) for passage in passages}
    if any((row.get("citation"), row.get("context")) not in allowed for row in rows):
        from model_hub.services.row_generation import GenerationOutputError

        raise GenerationOutputError(
            "The model returned an ungrounded citation or context. No rows were added."
        )
