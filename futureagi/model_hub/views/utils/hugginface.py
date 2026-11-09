import base64
import gc
import json
import mimetypes
import os
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pandas as pd
import requests
import structlog
from django.db import close_old_connections

from model_hub.models.api_key import ApiKey
from model_hub.models.choices import CellStatus, DataTypeChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.utils.utils import load_hf_dataset_with_retries
from tfc.settings.settings import (
    HUGGINGFACE_API_TOKEN,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.storage import (
    upload_audio_to_s3_duration,
    upload_document_to_s3,
    upload_image_to_s3,
)

logger = structlog.get_logger(__name__)

_HUGGINGFACE_DATASET_INFO_TIMEOUT_SECONDS = 30


def _guess_document_content_type(path):
    content_type, _ = mimetypes.guess_type(str(path or ""))
    return content_type or "application/pdf"


def _document_bytes_to_data_uri(raw_bytes, path=None):
    return (
        f"data:{_guess_document_content_type(path)};base64,"
        f"{base64.b64encode(bytes(raw_bytes)).decode('ascii')}"
    )


def _document_name_from_path(path, fallback="document"):
    if not path:
        return fallback
    name = os.path.basename(str(path))
    return name or fallback


def _is_remote_document_path(path):
    return urlparse(str(path)).scheme in ("http", "https")


def _pdfplumber_document_to_bytes(value):
    stream = getattr(value, "stream", None)
    if stream and hasattr(stream, "read") and hasattr(stream, "seek"):
        position = None
        try:
            position = stream.tell()
        except Exception:
            position = None

        try:
            stream.seek(0)
            raw_bytes = stream.read()
        finally:
            if position is not None:
                stream.seek(position)

        if isinstance(raw_bytes, str):
            return raw_bytes.encode()
        return bytes(raw_bytes)

    from tfc.utils.lazy_extras import load_extra

    pdf_to_bytes = load_extra("datasets.features.pdf", "ml").pdf_to_bytes

    return pdf_to_bytes(value)


def _coerce_huggingface_document_for_upload(value, fallback_name):
    if isinstance(value, dict):
        path = value.get("path")
        raw_bytes = value.get("bytes")
        document_name = _document_name_from_path(path, fallback=fallback_name)
        if raw_bytes is not None:
            return _document_bytes_to_data_uri(raw_bytes, path), document_name
        if path:
            if _is_remote_document_path(path):
                return str(path), document_name
            raise ValueError(
                "HuggingFace document payload did not include document bytes"
            )
        return value, document_name

    if isinstance(value, (bytes, bytearray, memoryview)):
        return _document_bytes_to_data_uri(value), fallback_name

    try:
        if value.__class__.__module__.startswith("pdfplumber"):
            return (
                _document_bytes_to_data_uri(_pdfplumber_document_to_bytes(value)),
                fallback_name,
            )
    except Exception as e:
        logger.exception("huggingface: Error converting decoded PDF document")
        raise ValueError("Unable to convert HuggingFace decoded PDF document") from e

    document_name = _document_name_from_path(value, fallback=fallback_name)
    return value, document_name


def get_huggingface_dataset_info(dataset_path, organization_id):
    if not organization_id:
        raise Exception("Organization not found")

    auth_token = ApiKey.objects.filter(
        organization_id=organization_id, provider="huggingface"
    ).first()
    # The org's own token when it has one: one this deployment cannot decrypt
    # raises (KeyUnreadable) rather than being swapped for the platform token.
    auth_token = (
        auth_token.actual_key if auth_token and auth_token.key else None
    ) or HUGGINGFACE_API_TOKEN
    headers = {"Authorization": f"Bearer {auth_token}"}
    API_URL = f"https://datasets-server.huggingface.co/info?dataset={dataset_path}"
    response = requests.get(
        API_URL,
        headers=headers,
        timeout=_HUGGINGFACE_DATASET_INFO_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise Exception(f"{response.status_code}")
    response = response.json()

    config_keys = list(response.get("dataset_info", {}).keys())
    config_to_splits = {}
    for key in config_keys:
        config_to_splits[key] = list(
            response.get("dataset_info", {}).get(key, {}).get("splits", {}).keys()
        )

    dataset_info = {
        "splits": config_to_splits,
    }

    return dataset_info


def _row_id_at(rows, index):
    """The row id for item `index`. `rows` crosses Temporal as JSON, so its
    keys arrive as strings; a direct call keeps the view's integer keys."""
    return rows.get(index) or rows.get(str(index))


def _write_error_cells(dataset_id, column_order, rows, num_rows, reason):
    """An ERROR cell with `reason` for every (row, column) that has no cell
    yet. Cells written before a mid-stream failure stay as they are: Cell
    has no unique (row, column) constraint, so a second write would
    duplicate them."""
    row_ids = [
        row_id for row_id in (_row_id_at(rows, i) for i in range(num_rows)) if row_id
    ]
    if not row_ids:
        return
    columns = list(Column.objects.filter(id__in=column_order))
    written = {
        (str(row_id), str(column_id))
        for row_id, column_id in Cell.no_workspace_objects.filter(
            dataset_id=dataset_id,
            row_id__in=row_ids,
            column_id__in=[column.id for column in columns],
        ).values_list("row_id", "column_id")
    }
    value_infos = json.dumps({"reason": reason})
    for row_id in row_ids:
        for column in columns:
            if (str(row_id), str(column.id)) in written:
                continue
            Cell.objects.create(
                dataset_id=dataset_id,
                column=column,
                row_id=row_id,
                value="",
                value_infos=value_infos,
                column_metadata={},
                status=CellStatus.ERROR.value,
            )


# Function to create cells for dataset imported from huggingface
def process_huggingface_columns(data_dict, dataset_id, column_id, rows, index):
    try:
        close_old_connections()
        data = pd.DataFrame(data_dict)
        column = Column.objects.get(id=column_id)
        dataset = Dataset.objects.get(id=dataset_id)
        if str(column.name) in data:
            value = data[str(column.name)][0]
        else:
            value = ""
        cell_value_infos = {}
        column_metadata = {}
        # Handle image and audio data types outside the inner loop
        if column.data_type == DataTypeChoices.IMAGE.value:
            # Handle image data type for all values in this column
            try:
                image_key = f"images/{dataset.id}/{uuid.uuid4()}"
                image_url = upload_image_to_s3(
                    value,
                    os.getenv("S3_FOR_DATA"),
                    image_key,
                    org_id=str(dataset.organization_id),
                )
                cell_value = image_url
                cell_status = CellStatus.PASS.value
            except ValueError as e:
                logger.exception(f"huggingface: Error uploading image: {str(e)}")
                cell_value = ""
                cell_value_infos["reason"] = str(e)
                cell_status = CellStatus.ERROR.value
            except Exception as e:
                logger.exception(f"huggingface: Error uploading image: {str(e)}")
                cell_value = ""
                cell_value_infos["reason"] = str(e)
                cell_status = CellStatus.ERROR.value

        elif column.data_type == DataTypeChoices.AUDIO.value:
            # Handle audio data type for all values in this column
            try:
                audio_key = f"audio/{dataset.id}/{uuid.uuid4()}"
                audio_url, duration = upload_audio_to_s3_duration(
                    value,
                    os.getenv("S3_FOR_DATA"),
                    audio_key,
                    org_id=str(dataset.organization_id),
                )
                cell_value = audio_url
                cell_status = CellStatus.PASS.value
                if duration:
                    column_metadata = {"audio_duration_seconds": duration}
            except Exception as e:
                traceback.print_exc()
                logger.exception(f"huggingface: Error uploading audio: {str(e)}")
                cell_value = ""
                cell_value_infos["reason"] = str(e)
                cell_status = CellStatus.ERROR.value

        elif column.data_type == DataTypeChoices.DOCUMENT.value:
            try:
                document_key = f"documents/{dataset.id}/{uuid.uuid4()}"
                document_input, document_name = _coerce_huggingface_document_for_upload(
                    value, fallback_name=f"{column.name}.pdf"
                )
                document_url = upload_document_to_s3(
                    document_input,
                    bucket_name=os.getenv("S3_FOR_DATA"),
                    object_key=document_key,
                    org_id=str(dataset.organization_id),
                )
                cell_value = document_url
                cell_value_infos["document_url"] = document_url
                cell_value_infos["document_name"] = document_name[:400]
                cell_status = CellStatus.PASS.value
            except Exception as e:
                logger.exception(f"huggingface: Error uploading document: {str(e)}")
                cell_value = ""
                cell_value_infos["reason"] = str(e)
                cell_status = CellStatus.ERROR.value

        else:
            cell_value = str(value)
            cell_status = CellStatus.PASS.value

        # Create cell
        # Note: rows dict keys may be strings after JSON serialization (via Temporal)
        row_id = _row_id_at(rows, index)
        Cell.objects.create(
            id=uuid.uuid4(),
            dataset=dataset,
            column=column,
            row_id=row_id,
            value=cell_value,
            value_infos=json.dumps(cell_value_infos),
            column_metadata=column_metadata,
            status=cell_status,
        )
    except Exception as e:
        traceback.print_exc()
        logger.exception(f"huggingface: Exception in Adding cell {e}")
    finally:
        close_old_connections()


# Function to load huggingface dataset and add dataset rows parallely
def process_huggingface_dataset(
    dataset_id,
    huggingface_dataset_name,
    huggingface_dataset_config,
    huggingface_dataset_split,
    organization_id,
    num_rows,
    column_order,
    rows,
    revision=None,
):
    try:
        hug_dataset = load_hf_dataset_with_retries(
            huggingface_dataset_name,
            huggingface_dataset_config,
            huggingface_dataset_split,
            organization_id,
            **({"revision": revision} if revision else {}),
        )
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for i, item in enumerate(hug_dataset):
                try:
                    if i >= num_rows:
                        gc.collect()
                        break
                    data = pd.DataFrame([item])
                    data.columns = data.columns.str.strip()
                    data = data.map(lambda x: x.strip() if isinstance(x, str) else x)
                    data_dict = data.to_dict(orient="records")
                    columns = Column.objects.filter(id__in=column_order)
                    for column in columns:
                        future = executor.submit(
                            process_huggingface_columns,
                            data_dict,
                            dataset_id,
                            str(column.id),
                            rows,
                            i,
                        )
                        futures.append(future)
                except Exception as e:
                    logger.exception(
                        f"huggingface: Error in adding cell to huggingface dataset: {str(e)}"
                    )

            for future in futures:
                future.result()

            _write_error_cells(
                dataset_id,
                column_order,
                rows,
                num_rows,
                "The source did not supply a value for this imported cell",
            )
            gc.collect()

    except Exception as e:
        # A build without the `ml` extra (no `datasets`) can never load:
        # say so in every cell, and do not let Temporal retry it.
        unavailable = isinstance(e, ImportError)
        reason = get_error_message(
            "HUGGINGFACE_IMPORT_UNAVAILABLE"
            if unavailable
            else "FAILED_TO_GET_DATA_FROM_HUGGINGFACE"
        )
        logger.exception(
            f"huggingface: Error in processing huggingface dataset: {str(e)}"
        )
        _write_error_cells(dataset_id, column_order, rows, num_rows, reason)
        if unavailable:
            from temporalio.exceptions import ApplicationError

            raise ApplicationError(
                reason, type="HuggingFaceImportUnavailable", non_retryable=True
            ) from e

    finally:
        Column.objects.filter(id__in=column_order).update(
            status=StatusType.COMPLETED.value
        )
        gc.collect()
        # insert_embeddings_task.delay(dataset_id=dataset_id, column_ids=column_order)
