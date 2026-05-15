# Removed ThreadPoolExecutor import to avoid gevent conflicts
import json
import os
import tempfile
import traceback
import uuid
from io import BytesIO
from typing import Any

import pandas as pd
import structlog
from django.db import close_old_connections, transaction
from django.db.utils import OperationalError
from rest_framework.generics import CreateAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

logger = structlog.get_logger(__name__)
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
from model_hub.serializers.develop_dataset import UploadFileForm
from model_hub.utils.file_reader import FileProcessor
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.temporal import temporal_activity
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
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import ROW_LIMIT_REACHED_MESSAGE, log_and_deduct_cost_for_resource_request
except ImportError:
    from futureagi._ee_stubs import ROW_LIMIT_REACHED_MESSAGE, log_and_deduct_cost_for_resource_request


def normalize_cell_value(value):
    """
    Normalize a cell value by converting empty strings, NaN, and None to None.
    Handles pandas Series/arrays and regular values safely.

    Args:
        value: The value to normalize (can be pandas Series, string, number, etc.)

    Returns:
        Normalized value (None for empty/null values, str(value) otherwise)
    """
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

                emit(
                    UsageEvent(
                        org_id=str(org_id),
                        event_type=BillingEventType.OBSERVE_ADD,
                        amount=len(file_content),
                        properties={"source": "dataset_file"},
                    )
                )
            except ImportError:
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


def handle_columns_bulk(dataset_id, data, column_mapping, row_mapping):
    """Handle bulk creation of cells for all columns - gevent-safe version"""
    try:
        close_old_connections()

        cells_to_create = []
        error_columns = set()
        completed_columns = set()

        logger.info(
            f"Processing {len(column_mapping)} columns for dataset {dataset_id}"
        )

        # Process columns sequentially to avoid threading issues with gevent
        for column_name, column_id in column_mapping.items():
            try:
                column = Column.objects.get(id=column_id)
                values = data[column_name]
                has_error = False

                logger.info(
                    f"Processing column {column_name} with {len(values)} values"
                )

                # Process cells in batches to avoid memory issues
                batch_size = 500
                for i in range(0, len(values), batch_size):
                    batch_values = dict(list(values.items())[i : i + batch_size])

                    for index, value in batch_values.items():
                        try:
                            value_infos = {}
                            column_metadata = {}
                            status = CellStatus.PASS.value

                            # Handle image data type - temporarily store raw value to avoid threading issues
                            if column.data_type == DataTypeChoices.IMAGE.value:
                                # Store the raw value temporarily - will be processed later
                                cell_value = str(value)
                                status = CellStatus.RUNNING.value
                                value_infos["note"] = (
                                    "Image upload will be processed separately to avoid threading conflicts"
                                )
                                logger.info(
                                    f"Storing raw image value for later processing: {len(str(value))} chars"
                                )

                            elif column.data_type == DataTypeChoices.AUDIO.value:
                                # Store the raw value temporarily - will be processed later
                                cell_value = str(value)
                                status = CellStatus.RUNNING.value
                                value_infos["note"] = (
                                    "Audio upload will be processed separately to avoid threading conflicts"
                                )
                                logger.info(
                                    f"Storing raw audio value for later processing: {len(str(value))} chars"
                                )

                            elif column.data_type == DataTypeChoices.DOCUMENT.value:
                                # Store the raw value temporarily - will be processed later
                                cell_value = str(value)
                                status = CellStatus.RUNNING.value
                                value_infos["note"] = (
                                    "Document upload will be processed separately to avoid threading conflicts"
                                )
                                logger.info(
                                    f"Storing raw document value for later processing: {len(str(value))} chars"
                                )

                            elif column.data_type == DataTypeChoices.IMAGES.value:
                                # Store the raw comma-separated image URLs - will be processed later
                                cell_value = str(value)
                                status = CellStatus.RUNNING.value
                                value_infos["note"] = (
                                    "Multiple images upload will be processed separately to avoid threading conflicts"
                                )
                                logger.info(
                                    f"Storing raw images value for later processing: {len(str(value))} chars"
                                )
                            else:
                                # Handle pandas Series/arrays and regular values safely
                                cell_value = normalize_cell_value(value)

                            cells_to_create.append(
                                Cell(
                                    dataset_id=dataset_id,
                                    column=column,
                                    row_id=row_mapping[index],
                                    value=cell_value,
                                    value_infos=json.dumps(value_infos),
                                    column_metadata=column_metadata,
                                    status=status,
                                )
                            )

                        except Exception:
                            logger.exception(
                                f"Error processing cell: {traceback.format_exc()}"
                            )
                            has_error = True

                # Check if this is a media column that needs async processing
                is_media_column = column.data_type in [
                    DataTypeChoices.IMAGE.value,
                    DataTypeChoices.AUDIO.value,
                    DataTypeChoices.DOCUMENT.value,
                    DataTypeChoices.IMAGES.value,
                ]

                if has_error:
                    error_columns.add(column_id)
                elif not is_media_column:
                    # Only mark non-media columns as completed
                    # Media columns will be marked completed after process_media_batch finishes
                    completed_columns.add(column_id)

            except Exception:
                logger.exception(
                    f"Error processing column {column_name}: {traceback.format_exc()}"
                )
                error_columns.add(column_id)

        # Bulk create all cells in batches
        if cells_to_create:
            logger.info(f"Creating {len(cells_to_create)} cells in bulk")
            batch_size = 1000
            for i in range(0, len(cells_to_create), batch_size):
                batch = cells_to_create[i : i + batch_size]
                Cell.objects.bulk_create(batch, batch_size=batch_size)
                logger.info(
                    f"Created batch {i // batch_size + 1} of {(len(cells_to_create) + batch_size - 1) // batch_size}"
                )

        # Update column statuses
        if completed_columns:
            Column.objects.filter(id__in=list(completed_columns)).update(
                status=StatusType.COMPLETED.value
            )

        if error_columns:
            Column.objects.filter(id__in=list(error_columns)).update(
                status=StatusType.PARTIAL_EXTRACTED.value
            )

        # Trigger embeddings task - temporarily disabled to avoid threading conflicts
        # embedding_column_ids = list(error_columns) + list(completed_columns)
        # if embedding_column_ids:
        #     insert_embeddings_task.delay(dataset_id=dataset_id, column_ids=embedding_column_ids)
        logger.info("Skipping embeddings task to avoid threading conflicts with gevent")

        logger.info(
            f"Completed processing: {len(completed_columns)} successful, {len(error_columns)} with errors"
        )
        return len(completed_columns), len(error_columns)

    except Exception as e:
        traceback.print_exc()
        logger.exception(f"Error in bulk cell creation: {e}")
        return 0, len(column_mapping)
    finally:
        close_old_connections()


@temporal_activity(time_limit=3600 * 2, queue="tasks_l")
def process_dataset_from_file(dataset_id, file_url, original_filename):
    """
    Celery task to process dataset creation from uploaded file in background
    """
    try:
        close_old_connections()

        logger.info(f"Starting background processing for dataset {dataset_id}")

        # Update dataset metadata to show processing started
        dataset = Dataset.objects.get(id=dataset_id)
        dataset_config = dataset.dataset_config or {}
        dataset_config.update(
            {
                "file_processing_status": "processing",
                "file_processing_started_at": pd.Timestamp.now().isoformat(),
                "original_filename": original_filename,
                "file_url": file_url,
            }
        )
        dataset.dataset_config = dataset_config
        dataset.save()

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

            with open(temp_file_path, "rb") as temp_file:
                file_with_name = FileWithName(temp_file, original_filename)
                data, error = FileProcessor.process_file(file_obj=file_with_name)

            if error:
                logger.error(f"FileProcessor error: {error}")
                raise Exception(f"File processing error: {error}")

            data.columns = data.columns.str.strip()

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
            for column_name in data.columns:
                column_id = uuid.uuid4()
                data_type = determine_data_type(data[column_name])
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

            # Bulk create columns and rows
            with transaction.atomic():
                Column.objects.bulk_create(columns_to_create)
                Row.objects.bulk_create(rows_to_create)

                # Update dataset configuration
                dataset.column_order = column_order
                dataset.column_config = column_config
                dataset.save()

            # Update progress
            dataset_config.update(
                {
                    "file_processing_status": "creating_cells",
                    "total_rows": len(data),
                    "total_columns": len(data.columns),
                }
            )
            dataset.dataset_config = dataset_config
            dataset.save()

            # Process cells in bulk
            completed_cols, error_cols = handle_columns_bulk(
                str(dataset.id), data.to_dict(), column_mapping, row_mapping
            )

            # Start media upload processing in a separate task to avoid threading conflicts
            process_media_uploads.delay(str(dataset.id))

            logger.info(
                "Skipping recommendations to avoid threading conflicts with gevent"
            )

            # Update final status
            dataset_config.update(
                {
                    "file_processing_status": "completed",
                    "file_processing_completed_at": pd.Timestamp.now().isoformat(),
                    "completed_columns": (
                        int(completed_cols) if completed_cols is not None else 0
                    ),
                    "error_columns": int(error_cols) if error_cols is not None else 0,
                    "dataset_source_local": True,
                }
            )
            dataset.dataset_config = dataset_config
            dataset.save()

            logger.info(f"Successfully completed processing for dataset {dataset_id}")

        finally:
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    except Exception as e:
        logger.exception(f"Error processing dataset {dataset_id}: {str(e)}")

        # Update dataset with error status
        try:
            dataset = Dataset.objects.get(id=dataset_id)
            dataset_config = dataset.dataset_config or {}
            dataset_config.update(
                {
                    "file_processing_status": "failed",
                    "file_processing_error": str(e),
                    "file_processing_failed_at": pd.Timestamp.now().isoformat(),
                }
            )
            dataset.dataset_config = dataset_config
            dataset.save()
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

    def post(self, request, *args, **kwargs):
        try:
            form = UploadFileForm(request.POST, request.FILES)
            file = form.files.get("file")
            new_dataset_name = form.data.get("new_dataset_name")
            model_type = form.data.get("model_type")
            source = form.data.get("source", DatasetSourceChoices.BUILD.value)

            # Enforce file size limit (aligned with UI: max 10 MB)
            if file:
                from model_hub.validators.dataset_validators import validate_file_size

                try:
                    validate_file_size(file)
                except Exception as validation_err:
                    return self._gm.bad_request(str(validation_err.detail[0]))

            # Check usage limits
            call_log_row_entry = log_and_deduct_cost_for_resource_request(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                api_call_type=APICallTypeChoices.DATASET_ADD.value,
                sdk_source=True if source == DatasetSourceChoices.SDK.value else False,
                workspace=request.workspace,
            )
            if (
                call_log_row_entry is None
                or call_log_row_entry.status
                == APICallStatusChoices.RESOURCE_LIMIT.value
                and source != DatasetSourceChoices.SDK.value
            ):
                return self._gm.too_many_requests(
                    get_error_message("DATASET_CREATE_LIMIT_REACHED")
                )
            call_log_row_entry.status = APICallStatusChoices.SUCCESS.value
            call_log_row_entry.save()

            # Validate inputs
            from model_hub.validators.dataset_validators import (
                validate_dataset_name_unique,
            )

            try:
                validate_dataset_name_unique(
                    new_dataset_name,
                    getattr(request, "organization", None) or request.user.organization,
                )
            except Exception as validation_err:
                return self._gm.bad_request(str(validation_err.detail[0]))

            if not file:
                return self._gm.bad_request(get_error_message("NO_FILE_UPLOADED"))

            # Quick file validation and row count check
            data, error = FileProcessor.process_file(file_obj=file)
            if error:
                return self._gm.bad_request(error)

            rows_in_dataset = data.shape[0]
            call_log_row = log_and_deduct_cost_for_resource_request(
                getattr(request, "organization", None) or request.user.organization,
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
            _org = getattr(request, "organization", None) or request.user.organization
            file_key = f"datasets/{_org.id}/{uuid.uuid4()}/{file.name}"
            file_url = upload_file_to_minio(file, file_key, org_id=str(_org.id))
            logger.info(f"File uploaded to Minio: {file_url}")
            logger.info(f"File key: {file_key}")

            # Create dataset with initial metadata
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            new_dataset_name = new_dataset_name if new_dataset_name else file.name

            dataset = Dataset.objects.create(
                name=new_dataset_name,
                organization=organization,
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

    def get(self, request, dataset_id, *args, **kwargs):
        try:
            # Get dataset and validate ownership
            dataset = Dataset.objects.filter(
                id=dataset_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            ).first()

            if not dataset:
                return self._gm.not_found(get_error_message("DATASET_NOT_FOUND"))

            dataset_config = dataset.dataset_config or {}

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
