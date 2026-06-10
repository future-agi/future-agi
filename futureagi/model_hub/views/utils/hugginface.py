import gc
import json
import os
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import structlog
from django.db import close_old_connections

logger = structlog.get_logger(__name__)
from model_hub.models.api_key import ApiKey
from model_hub.models.choices import CellStatus, DataTypeChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.utils.utils import load_hf_dataset_with_retries
from tfc.settings.settings import (
    HUGGINGFACE_API_TOKEN,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.storage import upload_audio_to_s3_duration, upload_image_to_s3

_HUGGINGFACE_DATASET_INFO_TIMEOUT_SECONDS = 30


def get_huggingface_dataset_info(dataset_path, organization_id):
    if not organization_id:
        raise Exception("Organization not found")

    auth_token = ApiKey.objects.filter(
        organization_id=organization_id, provider="huggingface"
    ).first()
    auth_token = (
        auth_token._actual_key
        if auth_token and auth_token._actual_key
        else HUGGINGFACE_API_TOKEN
    )
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

        else:
            cell_value = str(value)
            cell_status = CellStatus.PASS.value

        # Create cell
        # Note: rows dict keys may be strings after JSON serialization (via Temporal)
        row_id = rows.get(index) or rows.get(str(index))
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
):
    try:
        hug_dataset = load_hf_dataset_with_retries(
            huggingface_dataset_name,
            huggingface_dataset_config,
            huggingface_dataset_split,
            organization_id,
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

            gc.collect()

    except Exception as e:
        for i in range(num_rows):
            cell_value_infos = {}
            cell_value_infos["reason"] = get_error_message(
                "FAILED_TO_GET_DATA_FROM_HUGGINGFACE"
            )
            cell_status = CellStatus.ERROR.value
            columns = Column.objects.filter(id__in=column_order)
            for column in columns:
                Cell.objects.create(
                    dataset_id=dataset_id,
                    column=column,
                    row_id=rows[i],
                    value="",
                    value_infos=json.dumps(cell_value_infos),
                    column_metadata={},
                    status=cell_status,
                )
        logger.exception(
            f"huggingface: Error in processing huggingface dataset: {str(e)}"
        )

    finally:
        Column.objects.filter(id__in=column_order).update(
            status=StatusType.COMPLETED.value
        )
        gc.collect()
        # insert_embeddings_task.delay(dataset_id=dataset_id, column_ids=column_order)
