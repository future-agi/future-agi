# Removed ThreadPoolExecutor import to avoid gevent conflicts
import json
import os
import tempfile
import traceback
import uuid
from io import BytesIO

import pandas as pd
import structlog
from django.db import close_old_connections, transaction
from django.db.models import Q
from drf_yasg.utils import swagger_auto_schema
from rest_framework.generics import CreateAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from analytics.utils import (
    MixpanelEvents,
    MixpanelTypes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
    determine_data_type,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    CreateDatasetFromLocalFileRequestSerializer,
)
from model_hub.serializers.develop_dataset_contracts import (
    DatasetCreationProgressResponseSerializer,
    LocalFileDatasetCreateStartedResponseSerializer,
)
from model_hub.services.file_import_admission import (
    READER_KEY,
    read_previewed_json,
    upload_reader,
    worker_reader,
)
from model_hub.services.file_import_limits import (
    MAX_IMPORT_FILE_BYTES,
    check_import_shape,
    prepare_import_values,
)
from model_hub.services.file_media_dispatch import (
    MEDIA_FAILURE_MESSAGE,
    dispatch_import_media,
    refresh_media_dispatch,
)
from model_hub.services.row_activity_writes import file_import_activity
from model_hub.utils.file_reader import FileProcessor
from model_hub.views.utils.dataset_limit import dataset_add_refusal
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.temporal import temporal_activity
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import DatasetLimitCheckFailedErrorSerializer
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.storage import (
    upload_audio_to_s3_duration,
    upload_document_to_s3,
    upload_image_to_s3,
)
from tfc.utils.storage_client import (
    ensure_bucket,
    extract_object_key,
    get_storage_client,
)

try:
    from ee.usage.utils.usage_entries import (
        ROW_LIMIT_REACHED_MESSAGE,
        log_and_deduct_cost_for_resource_request,
    )
except ImportError:
    ROW_LIMIT_REACHED_MESSAGE = None
    log_and_deduct_cost_for_resource_request = None

logger = structlog.get_logger(__name__)


def _request_organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _request_workspace_filter(request, field_name="workspace"):
    workspace = getattr(request, "workspace", None)
    if not workspace:
        from tfc.utils.workspace_scope import empty_workspace_filter

        return empty_workspace_filter()

    if getattr(workspace, "is_default", False):
        return (
            Q(**{field_name: workspace})
            | Q(
                **{
                    f"{field_name}__is_default": True,
                    f"{field_name}__organization_id": workspace.organization_id,
                }
            )
            | Q(**{f"{field_name}__isnull": True})
        )

    return Q(**{field_name: workspace})


def _request_dataset_queryset(request):
    return Dataset.objects.filter(
        _request_workspace_filter(request),
        organization=_request_organization(request),
        deleted=False,
    )


def normalize_cell_value(value):
    """
    Normalize a cell value by converting empty strings, NaN, and None to None.
    Handles pandas Series/arrays and regular values safely.

    Args:
        value: The value to normalize (can be pandas Series, string, number, etc.)

    Returns:
        Normalized value (JSON for structured values, None for empty/null
        scalars, str(value) otherwise).
    """
    # JSON/JSONL readers yield native containers. Serialize before pd.isna:
    # lists produce an array of booleans there, and str(dict) is Python repr,
    # not JSON. Empty containers are meaningful values, not missing cells.
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    try:
        # Check for NaN/None values first (works for pandas Series and regular values)
        if pd.isna(value):
            return None
        # Check for empty strings (after stripping)
        if isinstance(value, str) and value.strip() == "":
            return None
        # Convert to string for non-empty values
        return str(value)
    except (ValueError, TypeError):
        # If pd.isna fails (e.g., for certain pandas Series), try alternative approach
        if value is None or (hasattr(value, "__len__") and len(value) == 0):
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        # Convert to string for non-empty values
        return str(value)


def upload_file_to_minio(file_obj, object_key, org_id=None):
    """Upload file to Minio and return the URL"""
    try:
        from tfc.utils.storage_client import get_object_url

        bucket_name = UPLOAD_BUCKET_NAME
        minio_client = get_storage_client()
        ensure_bucket(minio_client, bucket_name)

        # Reset file pointer to beginning
        file_obj.seek(0)
        file_content = file_obj.read()

        # Upload the file
        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=BytesIO(file_content),
            length=len(file_content),
            content_type="application/octet-stream",
        )

        if org_id:
            try:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                if (
                    emit is not None
                    and UsageEvent is not None
                    and BillingEventType is not None
                ):
                    emit(
                        UsageEvent(
                            org_id=str(org_id),
                            event_type=BillingEventType.OBSERVE_ADD,
                            amount=len(file_content),
                            properties={"source": "dataset_file"},
                        )
                    )
            except (ImportError, TypeError):
                pass

        url = get_object_url(bucket_name, object_key)
        return url
    except Exception as e:
        logger.error(f"Error uploading file to Minio: {str(e)}")
        raise


def download_file_from_minio(file_url, original_filename=None):
    """Download file from Minio URL to a temporary file"""
    try:
        bucket_name = UPLOAD_BUCKET_NAME
        minio_client = get_storage_client()

        # Extract object key from URL (supports S3, GCS, and local formats)
        object_key = extract_object_key(file_url, bucket_name)

        # Preserve file extension from original filename
        file_extension = ""
        if original_filename:
            file_extension = os.path.splitext(original_filename)[1]
        elif object_key:
            # Fallback to extracting from object key
            file_extension = os.path.splitext(object_key)[1]

        # Create temporary file with proper extension
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
        temp_file.close()  # Close the file handle so minio can write to it

        logger.info(f"Downloading file from Minio: {object_key}")
        logger.info(f"Temporary file path: {temp_file.name}")
        logger.info(f"File extension: {file_extension}")

        minio_client.fget_object(bucket_name, object_key, temp_file.name)

        return temp_file.name
    except Exception as e:
        logger.error(f"Error downloading file from Minio: {str(e)}")
        raise


MEDIA_COLUMN_TYPES = {
    DataTypeChoices.IMAGE.value,
    DataTypeChoices.IMAGES.value,
    DataTypeChoices.AUDIO.value,
    DataTypeChoices.DOCUMENT.value,
}
IMPORT_BATCH_SIZE = 1000


def handle_columns_bulk(dataset_id, data, column_mapping, row_mapping):
    """Write bounded batches in the caller's transaction; any failure propagates.

    Never close connections here: doing so inside atomic() closes its connection.
    Keep at most 1000 Cell instances, not a second copy of the entire import.
    """
    cells = []
    completed_columns = []
    for name, column_id in column_mapping.items():
        column = Column.objects.get(id=column_id)
        media = column.data_type in MEDIA_COLUMN_TYPES
        for index, value in data[name].items():
            cells.append(
                Cell(
                    dataset_id=dataset_id,
                    column=column,
                    row_id=row_mapping[index],
                    value=str(value) if media else normalize_cell_value(value),
                    value_infos=json.dumps(
                        {"note": "Media upload will be processed separately"}
                        if media
                        else {}
                    ),
                    column_metadata={},
                    status=CellStatus.RUNNING.value if media else CellStatus.PASS.value,
                )
            )
            if len(cells) == IMPORT_BATCH_SIZE:
                Cell.objects.bulk_create(cells, batch_size=IMPORT_BATCH_SIZE)
                cells.clear()
        if not media:
            completed_columns.append(column_id)
    if cells:
        Cell.objects.bulk_create(cells, batch_size=IMPORT_BATCH_SIZE)
    Column.objects.filter(id__in=completed_columns).update(
        status=StatusType.COMPLETED.value
    )
    return len(completed_columns), 0


def _resume_import_media_dispatch(dataset):
    if (dataset.dataset_config or {}).get("file_media_dispatch_status") == "pending":
        transaction.on_commit(
            lambda: dispatch_import_media(
                dataset.id, dataset.organization_id, process_media_uploads.delay
            )
        )


@temporal_activity(time_limit=3600 * 2, queue="tasks_l")
@file_import_activity
def process_dataset_from_file(dataset_id, file_url, original_filename):
    """
    Celery task to process dataset creation from uploaded file in background
    """
    try:
        close_old_connections()

        logger.info(f"Starting background processing for dataset {dataset_id}")

        # Keep tenant filtering, but lock only Dataset: the default-workspace
        # manager joins nullable workspace rows, which PostgreSQL cannot lock.
        # Update dataset metadata to show processing started.
        with transaction.atomic():
            dataset = Dataset.objects.select_for_update(of=("self",)).get(id=dataset_id)
            dataset_config = dataset.dataset_config or {}
            if dataset_config.get("file_processing_status") == "completed":
                _resume_import_media_dispatch(dataset)
                return  # Redelivery must not recreate the published table.
            # F32: only an upload the route admitted while ON uses the
            # previewed reader (services/file_import_admission.py).
            reader, refusal = worker_reader(dataset_config)
            if refusal:
                # Admitted, but the reader is off here: store no table.
                dataset_config.update(
                    {
                        "file_processing_status": "failed",
                        "file_processing_error": refusal,
                        "file_processing_failed_at": pd.Timestamp.now().isoformat(),
                    }
                )
                dataset.dataset_config = dataset_config
                dataset.save(update_fields=["dataset_config"])
                return
            dataset_config.update(
                {
                    "file_processing_status": "processing",
                    "file_processing_started_at": pd.Timestamp.now().isoformat(),
                    "original_filename": original_filename,
                    "file_url": file_url,
                }
            )
            dataset.dataset_config = dataset_config
            dataset.save(update_fields=["dataset_config"])

        # Download file from Minio to temporary location
        temp_file_path = download_file_from_minio(file_url, original_filename)

        try:
            # Process the file
            logger.info(f"Processing file: {temp_file_path}")
            logger.info(f"Original filename: {original_filename}")

            # Create a file-like object with the original filename for FileProcessor
            class FileWithName:
                def __init__(self, file_obj, name):
                    self.file_obj = file_obj
                    self.name = name

                def __getattr__(self, name):
                    return getattr(self.file_obj, name)

            if os.path.getsize(temp_file_path) > MAX_IMPORT_FILE_BYTES:
                raise ValueError(
                    "File import exceeds 25 MiB. Split the file into smaller imports."
                )
            with open(temp_file_path, "rb") as temp_file:
                file_with_name = FileWithName(temp_file, original_filename)
                if reader is None:
                    data, error = FileProcessor.process_file(file_obj=file_with_name)
                    data_types = None
                else:
                    data, data_types, error = read_previewed_json(
                        file_with_name, original_filename
                    )

            if error:
                logger.error(f"FileProcessor error: {error}")
                raise Exception(f"File processing error: {error}")

            data.columns = data.columns.str.strip()
            check_import_shape(data)

            # Strip whitespace and convert empty strings to None (pandas will convert to NaN)
            # This ensures empty cells are properly handled as null values instead of empty strings
            def process_value(x):
                if isinstance(x, str):
                    stripped = x.strip()
                    return None if stripped == "" else stripped
                return x

            data = data.map(process_value)
            data.reset_index(drop=True, inplace=True)

            # Create columns and rows in bulk
            columns_to_create = []
            rows_to_create = []
            column_order = []
            column_config = {}
            column_mapping = {}
            row_mapping = {}

            # Prepare columns
            for position, column_name in enumerate(data.columns):
                column_id = uuid.uuid4()
                # The previewed reader returns each column's type; FileProcessor
                # frames are inferred, as before.
                data_type = (
                    determine_data_type(data[column_name])
                    if data_types is None
                    else data_types[position]
                )
                status = StatusType.RUNNING.value
                if data_type in [
                    DataTypeChoices.IMAGE.value,
                    DataTypeChoices.AUDIO.value,
                ]:
                    # Mark as running instead of uploading to avoid threading conflicts
                    status = StatusType.RUNNING.value

                columns_to_create.append(
                    Column(
                        id=column_id,
                        name=column_name,
                        data_type=data_type,
                        source=SourceChoices.OTHERS.value,
                        status=status,
                        dataset=dataset,
                    )
                )

                column_order.append(str(column_id))
                column_mapping[column_name] = str(column_id)
                column_config[str(column_id)] = {"is_visible": True, "is_frozen": None}

                # Prepare rows

            for i, index in enumerate(data.index):
                row_id = uuid.uuid4()
                rows_to_create.append(Row(id=row_id, dataset=dataset, order=i))
                row_mapping[index] = str(row_id)

            values = prepare_import_values(
                data, columns_to_create, MEDIA_COLUMN_TYPES, normalize_cell_value
            )
            # One publication point: no committed Row can precede its Cells.
            # Bounds are enforced before this transaction (file_import_limits).
            with transaction.atomic():
                dataset = Dataset.objects.select_for_update(of=("self",)).get(
                    id=dataset_id
                )
                dataset_config = dataset.dataset_config or {}
                if dataset_config.get("file_processing_status") == "completed":
                    _resume_import_media_dispatch(dataset)
                    return
                Column.objects.bulk_create(
                    columns_to_create, batch_size=IMPORT_BATCH_SIZE
                )
                Row.objects.bulk_create(rows_to_create, batch_size=IMPORT_BATCH_SIZE)
                completed_cols, error_cols = handle_columns_bulk(
                    str(dataset.id), values, column_mapping, row_mapping
                )
                dataset.column_order = column_order
                dataset.column_config = column_config
                dataset_config.update(
                    {
                        "file_processing_status": "completed",
                        "file_processing_completed_at": pd.Timestamp.now().isoformat(),
                        "total_rows": len(data),
                        "total_columns": len(data.columns),
                        "completed_columns": int(completed_cols),
                        "error_columns": int(error_cols),
                        "dataset_source_local": True,
                    }
                )
                dataset_config.pop("file_processing_error", None)
                dataset_config.pop("file_processing_failed_at", None)
                if (
                    any(c.data_type in MEDIA_COLUMN_TYPES for c in columns_to_create)
                    and row_mapping
                ):
                    # Commit the handoff intent with the complete table. A failed
                    # on_commit callback propagates, but cannot undo publication.
                    dataset_config["file_media_dispatch_status"] = "pending"
                dataset.dataset_config = dataset_config
                dataset.save(
                    update_fields=["column_order", "column_config", "dataset_config"]
                )
                _resume_import_media_dispatch(dataset)

            logger.info(f"Successfully completed processing for dataset {dataset_id}")

        finally:
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    except Exception as e:
        # Dispatch exceptions may contain private URLs. Record only their class;
        # publication stays completed, with the bounded handoff state intact.
        logger.error("dataset_processing_failed", error_class=type(e).__name__)

        # Update dataset with error status
        try:
            with transaction.atomic():
                dataset = Dataset.objects.select_for_update(of=("self",)).get(
                    id=dataset_id
                )
                dataset_config = dataset.dataset_config or {}
                # A competing retry may already have published successfully.
                if dataset_config.get("file_processing_status") != "completed":
                    dataset_config.update(
                        {
                            "file_processing_status": "failed",
                            "file_processing_error": str(e),
                            "file_processing_failed_at": pd.Timestamp.now().isoformat(),
                        }
                    )
                    dataset.dataset_config = dataset_config
                    dataset.save(update_fields=["dataset_config"])
        except Exception as save_error:
            logger.exception(f"Error updating dataset status: {str(save_error)}")

        # Re-raise for Celery retry mechanism
        raise
    finally:
        close_old_connections()


@temporal_activity(time_limit=1800, queue="tasks_l", max_retries=3)
def process_media_uploads(dataset_id):
    """
    Coordinator task to handle image and audio uploads after dataset creation.
    This task splits work into batches and fires separate Celery tasks for each batch.
    """
    try:
        close_old_connections()

        logger.info(f"Starting media upload processing for dataset {dataset_id}")

        # Get all cell IDs that need media processing
        cells_query = Cell.objects.filter(
            dataset_id=dataset_id,
            status=CellStatus.RUNNING.value,
            column__data_type__in=[
                DataTypeChoices.IMAGE.value,
                DataTypeChoices.IMAGES.value,
                DataTypeChoices.AUDIO.value,
                DataTypeChoices.DOCUMENT.value,
            ],
        ).order_by("id")

        total_cells = cells_query.count()

        if total_cells == 0:
            logger.info(f"No media cells to process for dataset {dataset_id}")
            # Mark columns as completed if no cells to process
            Column.objects.filter(
                dataset_id=dataset_id,
                data_type__in=[
                    DataTypeChoices.IMAGE.value,
                    DataTypeChoices.IMAGES.value,
                    DataTypeChoices.AUDIO.value,
                    DataTypeChoices.DOCUMENT.value,
                ],
            ).update(status=StatusType.COMPLETED.value)
            return

        logger.info(f"Processing {total_cells} media cells in parallel batches")

        # Split cells into batches and fire separate tasks for each batch
        batch_size = 40
        batch_tasks = []

        for i in range(0, total_cells, batch_size):
            # Get cell IDs for this batch
            batch_cell_ids = list(
                cells_query.values_list("id", flat=True)[i : i + batch_size]
            )

            if batch_cell_ids:
                # Fire batch processing task
                task = process_media_batch.delay(dataset_id, batch_cell_ids)
                batch_tasks.append(task)
                logger.info(
                    f"Fired batch task {i // batch_size + 1} with {len(batch_cell_ids)} cells"
                )

        logger.info(
            f"Fired {len(batch_tasks)} parallel batch tasks for dataset {dataset_id}"
        )

    except Exception as e:
        logger.exception(
            f"Error in media upload coordinator for dataset {dataset_id}: {str(e)}"
        )
        raise
    finally:
        close_old_connections()


@temporal_activity(time_limit=3600, queue="tasks_l", max_retries=3)
def process_media_batch(dataset_id, cell_ids):
    """
    Process a single batch of media cells.
    This task handles parallel S3 uploads and atomic database updates.
    At the end, checks if all media columns are complete and updates their status.
    """
    try:
        # Ensure clean database connections
        from concurrent.futures import ThreadPoolExecutor, as_completed

        close_old_connections()

        logger.info(
            f"Processing batch with {len(cell_ids)} cells for dataset {dataset_id}"
        )

        org_id = (
            Dataset.objects.filter(id=dataset_id)
            .values_list("organization_id", flat=True)
            .first()
        )

        # Fetch cells for this batch with column relationship
        batch = list(Cell.objects.filter(id__in=cell_ids).select_related("column"))

        if not batch:
            logger.warning(
                f"No cells found for batch in dataset {dataset_id}, cell_ids: {cell_ids[:10]}..."
            )

            return

        # Prepare serialized cell data to avoid DB access in threads
        serialized_cells = []
        cell_map = {}
        for cell in batch:
            serialized_cells.append(
                {
                    "cell_id": cell.id,
                    "cell_value": cell.value,
                    "value_infos": cell.value_infos,
                    "data_type": cell.column.data_type,
                }
            )

            cell_map[str(cell.id)] = cell

        # Step 1: Upload all media files in parallel
        upload_results = {}

        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_cell_data = {}
            for cell_data in serialized_cells:
                future = executor.submit(
                    upload_cell_media,
                    cell_data,
                    dataset_id,
                    org_id=str(org_id) if org_id else None,
                )
                future_to_cell_data[future] = cell_data

            # Process results as they complete
            for future in as_completed(future_to_cell_data):
                cell_data = future_to_cell_data[future]
                try:
                    result = future.result()
                    upload_results[str(result["cell_id"])] = result
                except Exception as e:
                    logger.error(
                        f"Unexpected error in parallel upload for cell {cell_data['cell_id']}: {str(e)}"
                    )
                    upload_results[str(cell_data["cell_id"])] = {
                        "cell_id": cell_data["cell_id"],
                        "success": False,
                        "status": CellStatus.ERROR.value,
                        "value_infos": {"reason": str(e)},
                    }

        # Step 2: Update all cells in the batch using bulk operations (single DB transaction)
        cells_to_update = []

        for cell_id in cell_ids:
            # Convert UUID to string for map lookup (cell_map uses string keys)
            cell_id_str = str(cell_id)
            cell = cell_map.get(cell_id_str)
            if not cell:
                logger.warning(f"Cell {cell_id_str} not found or locked, skipping")
                continue

            result = upload_results.get(cell_id_str)
            if result:
                status_value = result.get("status")
                if status_value is None:
                    logger.warning(
                        f"Status missing in result for cell {cell_id_str}, defaulting to ERROR"
                    )
                    cell.status = CellStatus.ERROR.value
                else:
                    cell.status = status_value

                if result.get("value") is not None:
                    cell.value = result["value"]
                if result.get("column_metadata") is not None:
                    cell.column_metadata = result["column_metadata"]
                if result.get("value_infos") is not None:
                    cell.value_infos = json.dumps(result["value_infos"])
                elif "value_infos" in result:
                    cell.value_infos = json.dumps({})

                cells_to_update.append(cell)
            else:
                logger.warning(
                    f"Missing upload result for cell {cell_id_str} in batch. Marking as ERROR."
                )
                cell.status = CellStatus.ERROR.value
                cell.value_infos = json.dumps(
                    {
                        "reason": "Upload result missing - upload may have failed silently"
                    }
                )
                cells_to_update.append(cell)

        # Bulk update all cells in a single database operation within the same transaction
        if cells_to_update:
            with transaction.atomic():
                Cell.objects.bulk_update(
                    cells_to_update,
                    ["value", "status", "value_infos", "column_metadata"],
                    batch_size=50,
                )
                logger.info(
                    f"Batch: Updated {len(cells_to_update)} cells for dataset {dataset_id}"
                )

        with transaction.atomic():
            # Check if any RUNNING cells exist for these columns
            running_cells_count = Cell.objects.filter(
                dataset_id=dataset_id,
                status=CellStatus.RUNNING.value,
                column__data_type__in=[
                    DataTypeChoices.IMAGE.value,
                    DataTypeChoices.IMAGES.value,
                    DataTypeChoices.AUDIO.value,
                    DataTypeChoices.DOCUMENT.value,
                ],
            ).count()

            if running_cells_count == 0:
                Column.objects.filter(
                    dataset_id=dataset_id,
                    data_type__in=[
                        DataTypeChoices.IMAGE.value,
                        DataTypeChoices.IMAGES.value,
                        DataTypeChoices.AUDIO.value,
                        DataTypeChoices.DOCUMENT.value,
                    ],
                ).update(status=StatusType.COMPLETED.value)

                logger.info(
                    f"Updated media columns status to COMPLETED for dataset {dataset_id}"
                )

        logger.info(f"Completed batch processing for dataset {dataset_id}")

    except Exception as e:
        logger.exception(
            f"Error in batch processing for dataset {dataset_id}: {str(e)}"
        )
        raise
    finally:
        close_old_connections()


def upload_cell_media(cell_data, dataset_id, org_id=None):
    """
    Thread-safe function to upload media files to S3.
    Does not access Django ORM to avoid thread-safety issues.

    Args:
        cell_data: Dict containing cell_id, cell_value, value_infos, data_type
        dataset_id: UUID of the dataset

    Returns:
        Dict containing upload results
    """
    cell_id = cell_data["cell_id"]
    cell_value = cell_data["cell_value"]
    data_type = cell_data["data_type"]

    value_infos = (
        json.loads(cell_data["value_infos"]) if cell_data["value_infos"] else {}
    )
    result = {
        "cell_id": cell_id,
        "success": False,
        "value": None,
        "status": None,
        "value_infos": value_infos,
        "column_metadata": None,
    }

    try:
        # Check for empty/null values before processing
        normalized_value = normalize_cell_value(cell_value)
        if normalized_value is None:
            logger.warning(f"Empty cell value for cell {cell_id} (type: {data_type})")
            result["status"] = CellStatus.ERROR.value
            result["success"] = False
            result["value_infos"] = {"reason": "Empty or null cell value"}
            return result

        # Use normalized value for processing
        cell_value = normalized_value

        if data_type == DataTypeChoices.IMAGE.value:
            # Process image upload
            image_key = f"images/{dataset_id}/{uuid.uuid4()}"
            image_url = upload_image_to_s3(
                cell_value, os.getenv("S3_FOR_DATA"), image_key, org_id=org_id
            )
            result["value"] = image_url
            result["status"] = CellStatus.PASS.value
            result["success"] = True

        elif data_type == DataTypeChoices.AUDIO.value:
            # Process audio upload
            audio_key = f"audio/{dataset_id}/{uuid.uuid4()}"
            audio_url, duration = upload_audio_to_s3_duration(
                cell_value, os.getenv("S3_FOR_DATA"), audio_key, org_id=org_id
            )
            result["value"] = audio_url
            result["status"] = CellStatus.PASS.value
            result["success"] = True
            if duration:
                result["column_metadata"] = {"audio_duration_seconds": duration}

        elif data_type == DataTypeChoices.DOCUMENT.value:
            doc_key = f"documents/{dataset_id}/{uuid.uuid4()}"
            doc_url = upload_document_to_s3(
                cell_value,
                bucket_name=os.getenv("S3_FOR_DATA"),
                object_key=doc_key,
                org_id=org_id,
            )

            if not isinstance(value_infos, dict):
                value_infos = {}
            value_infos["document_url"] = doc_url
            value_infos["document_name"] = cell_value[:400]
            result["value"] = doc_url
            result["status"] = CellStatus.PASS.value
            result["success"] = True

        elif data_type == DataTypeChoices.IMAGES.value:
            # Process multiple images - supports JSON array or comma-separated
            from model_hub.utils.image_utils import parse_image_urls

            image_urls_str = cell_value.strip()
            logger.debug("processing_images_type", cell_id=cell_id)
            logger.debug("raw_cell_value", value=image_urls_str[:500])

            raw_urls = parse_image_urls(image_urls_str)

            logger.debug("parsed_urls", count=len(raw_urls), urls=raw_urls)

            uploaded_urls = []
            upload_errors = []

            for i, raw_url in enumerate(raw_urls):
                try:
                    image_key = f"images/{dataset_id}/{uuid.uuid4()}"
                    image_url = upload_image_to_s3(
                        raw_url, os.getenv("S3_FOR_DATA"), image_key, org_id=org_id
                    )
                    uploaded_urls.append(image_url)
                except Exception as e:
                    logger.error(
                        "image_upload_failed",
                        cell_id=cell_id,
                        image_index=i,
                        error=str(e),
                    )
                    upload_errors.append(f"Image {i}: {str(e)}")

            if uploaded_urls:
                # Store as JSON array
                result["value"] = json.dumps(uploaded_urls)
                result["status"] = CellStatus.PASS.value
                result["success"] = True
                if upload_errors:
                    # Partial success - some images failed
                    value_infos["partial_upload_errors"] = upload_errors
            else:
                # All uploads failed
                result["status"] = CellStatus.ERROR.value
                result["success"] = False
                value_infos["reason"] = "No images could be uploaded"
                if upload_errors:
                    value_infos["errors"] = upload_errors

        # Clear the processing note
        if "note" in value_infos:
            del value_infos["note"]
        result["value_infos"] = value_infos

    except Exception as e:
        logger.error(
            f"Error uploading media for cell {cell_id} (type: {data_type}): {str(e)}"
        )
        result["status"] = CellStatus.ERROR.value
        result["success"] = False
        if not isinstance(value_infos, dict):
            value_infos = {}
        value_infos["reason"] = str(e)
        result["value_infos"] = value_infos

    return result


class CreateDatasetFromLocalFileView(CreateAPIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    @validated_request(
        request_serializer=CreateDatasetFromLocalFileRequestSerializer,
        responses={
            200: LocalFileDatasetCreateStartedResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
            503: DatasetLimitCheckFailedErrorSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        try:
            validated_data = request.validated_data
            file = validated_data["file"]
            new_dataset_name = validated_data.get("new_dataset_name")
            model_type = validated_data.get("model_type")
            source = validated_data.get("source") or DatasetSourceChoices.BUILD.value

            if not file:
                return self._gm.bad_request(get_error_message("NO_FILE_UPLOADED"))

            # Enforce file size limit (aligned with UI: max 25 MB)
            if file:
                from model_hub.validators.dataset_validators import validate_file_size

                try:
                    validate_file_size(file)
                except Exception as validation_err:
                    return self._gm.bad_request(str(validation_err.detail[0]))

            # Quick file validation and row count check. F32: a JSON/JSONL
            # dialog upload admitted while ON uses the previewed reader
            # (services/file_import_admission.py); every other upload uses
            # FileProcessor, as before.
            reader = upload_reader(request, file.name, source)
            if reader is None:
                data, error = FileProcessor.process_file(file_obj=file)
            else:
                data, _, error = read_previewed_json(file, file.name)
            if error:
                return self._gm.bad_request(error)

            organization = _request_organization(request)
            new_dataset_name = new_dataset_name if new_dataset_name else file.name

            from model_hub.validators.dataset_validators import (
                validate_dataset_name_unique,
            )

            try:
                validate_dataset_name_unique(new_dataset_name, organization)
            except Exception as validation_err:
                return self._gm.bad_request(str(validation_err.detail[0]))

            rows_in_dataset = data.shape[0]

            # Check usage limits after validation so failed requests do not consume quota.
            if log_and_deduct_cost_for_resource_request is not None:
                call_log_row_entry = log_and_deduct_cost_for_resource_request(
                    organization=organization,
                    api_call_type=APICallTypeChoices.DATASET_ADD.value,
                    sdk_source=True
                    if source == DatasetSourceChoices.SDK.value
                    else False,
                    workspace=request.workspace,
                )
                refusal = dataset_add_refusal(
                    call_log_row_entry,
                    sdk_source=source == DatasetSourceChoices.SDK.value,
                )
                if refusal is not None:
                    return refusal
                call_log_row_entry.status = APICallStatusChoices.SUCCESS.value
                call_log_row_entry.save()

            if log_and_deduct_cost_for_resource_request is not None:
                call_log_row = log_and_deduct_cost_for_resource_request(
                    organization,
                    api_call_type=APICallTypeChoices.ROW_ADD.value,
                    config={"total_rows": rows_in_dataset},
                    workspace=request.workspace,
                )
                if (
                    call_log_row is None
                    or call_log_row.status == APICallStatusChoices.RESOURCE_LIMIT.value
                ):
                    return self._gm.too_many_requests(ROW_LIMIT_REACHED_MESSAGE)
                call_log_row.status = APICallStatusChoices.SUCCESS.value
                call_log_row.save()

            # Upload file to Minio immediately
            _org = organization
            file_key = f"datasets/{_org.id}/{uuid.uuid4()}/{file.name}"
            file_url = upload_file_to_minio(file, file_key, org_id=str(_org.id))
            logger.info(f"File uploaded to Minio: {file_url}")
            logger.info(f"File key: {file_key}")

            # Create dataset with initial metadata
            dataset = Dataset.objects.create(
                name=new_dataset_name,
                organization=organization,
                workspace=getattr(request, "workspace", None),
                model_type=model_type,
                source=source,
                dataset_config={
                    "dataset_source_local": True,
                    "file_processing_status": "queued",
                    "file_processing_queued_at": pd.Timestamp.now().isoformat(),
                    "original_filename": file.name,
                    "file_url": file_url,
                    "estimated_rows": rows_in_dataset,
                    "estimated_columns": data.shape[1],
                    # The worker's admission record; absent for every other upload.
                    **({READER_KEY: reader} if reader is not None else {}),
                },
                user=request.user,
            )

            # Start background processing
            process_dataset_from_file.delay(str(dataset.id), file_url, file.name)

            if request.headers.get("X-Api-Key") is not None:
                properties = get_mixpanel_properties(
                    user=request.user,
                    dataset=dataset,
                    type=MixpanelTypes.LOCAL_FILE.value,
                    name=new_dataset_name,
                    row_count=int(data.shape[0]),
                    col_count=int(data.shape[1]),
                )
                track_mixpanel_event(
                    MixpanelEvents.SDK_DATASET_CREATE.value, properties
                )

            return self._gm.success_response(
                {
                    "message": "Dataset creation started successfully. Processing in background.",
                    "dataset_id": str(dataset.id),
                    "dataset_name": dataset.name,
                    "dataset_model_type": model_type,
                    "processing_status": "queued",
                    "estimated_rows": rows_in_dataset,
                    "estimated_columns": data.shape[1],
                }
            )

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in creating dataset from local: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_DATASET_FROM_LOCAL")
            )


class DatasetCreationProgressView(APIView):
    """
    API endpoint to check the progress of dataset creation from file upload
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)

    @swagger_auto_schema(
        responses={
            200: DatasetCreationProgressResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, dataset_id, *args, **kwargs):
        try:
            # Get dataset and validate ownership
            dataset = _request_dataset_queryset(request).filter(id=dataset_id).first()

            if not dataset:
                return self._gm.not_found(get_error_message("DATASET_NOT_FOUND"))

            dataset_config = refresh_media_dispatch(dataset)

            # Check if this is a file-based dataset
            if not dataset_config.get("dataset_source_local"):
                return self._gm.bad_request(
                    "This endpoint is only for file-based datasets"
                )

            # Extract processing status information
            status = dataset_config.get("file_processing_status", "unknown")

            # Determine if processing is still ongoing
            is_processing = status in ["queued", "processing", "creating_cells"]
            is_completed = status == "completed"
            is_failed = status == "failed"

            progress_info = {
                "dataset_id": str(dataset.id),
                "dataset_name": dataset.name,
                "processing_status": status,
                "is_processing": is_processing,
                "is_completed": is_completed,
                "is_failed": is_failed,
                "media_dispatch_status": dataset_config.get(
                    "file_media_dispatch_status"
                ),
                "media_dispatch_attempts": dataset_config.get(
                    "file_media_dispatch_attempts", 0
                ),
                "media_dispatch_last_error_class": dataset_config.get(
                    "file_media_dispatch_last_error_class"
                ),
                "media_processing_message": (
                    MEDIA_FAILURE_MESSAGE
                    if dataset_config.get("file_media_dispatch_status") == "failed"
                    else None
                ),
                "original_filename": dataset_config.get("original_filename"),
                "estimated_rows": dataset_config.get("estimated_rows"),
                "estimated_columns": dataset_config.get("estimated_columns"),
                "queued_at": dataset_config.get("file_processing_queued_at"),
                "started_at": dataset_config.get("file_processing_started_at"),
                "completed_at": dataset_config.get("file_processing_completed_at"),
                "failed_at": dataset_config.get("file_processing_failed_at"),
                "error_message": dataset_config.get("file_processing_error"),
            }

            return self._gm.success_response(progress_info)

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error getting dataset creation progress: {str(e)}")
            return self._gm.internal_server_error_response(
                "Failed to get dataset creation progress"
            )
