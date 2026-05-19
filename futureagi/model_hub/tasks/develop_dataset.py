import asyncio
import json
import os
import random
import shutil
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import structlog
from django.conf import settings

from accounts.models.organization import Organization

# Activity-aware stub: invocations raise a Temporal non-retryable
# ApplicationError (these tasks run inside Temporal evaluation activities).
from tfc.ee_stub import _ee_activity_stub as _ee_stub

try:
    from ee.agenthub.eval_recommendation.eval_recommendation import (
        EvalRecommender,
    )
    from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
        SyntheticDataAgent,
    )
except ImportError:
    EvalRecommender = _ee_stub("EvalRecommender")
    SyntheticDataAgent = _ee_stub("SyntheticDataAgent")
from tfc.telemetry import wrap_for_thread

logger = structlog.get_logger(__name__)
from model_hub.models.choices import (
    CellStatus,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import (
    Cell,
    Column,
    Dataset,
    Files,
    KnowledgeBaseFile,
    Row,
)
from model_hub.serializers.develop_dataset import ColumnSerializer
from model_hub.utils.kb_helpers import is_kb_deleted_or_cancelled
from model_hub.utils.kb_indexer import KB_TABLE_NAME, KBIndexer
from model_hub.utils.synthetic_task_manager import SyntheticTaskManager
from tfc.temporal import temporal_activity
from tfc.utils.storage import (
    delete_compare_folder,
)
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from tfc.oss_stubs.usage import APICallStatusChoices
    from tfc.oss_stubs.usage import APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import count_text_tokens, log_and_deduct_cost_for_api_request
except ImportError:
    from tfc.oss_stubs.usage import count_text_tokens
    from tfc.oss_stubs.usage import log_and_deduct_cost_for_api_request


def run_generate_new_rows_test(
    dataset_id: str,
    num_rows_to_generate: int,
    sample_size: int = 10,
    dataset_description="",
    dataset_objective="",
):
    """
    A unified preprocessor function to prepare data and then call the
     `generate_new_rows` task for testing purposes.
    """
    logger.info(f"--- Starting Test for Dataset ID: {dataset_id} ---")
    # 1. Fetch Dataset and Columns
    try:
        dataset = Dataset.objects.get(id=dataset_id)
        total_columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
            source__in=[
                SourceChoices.EXPERIMENT.value,
                SourceChoices.EXPERIMENT_EVALUATION.value,
                SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
            ]
        )
        logger.info(
            f"Found dataset: '{dataset.name}' with {len(total_columns)} columns."
        )
    except Dataset.DoesNotExist:
        logger.info(f"ERROR: Dataset with ID {dataset_id} not found.")
        return

    # 2. Create new empty Row objects to be populated
    existing_row_count = len(Row.objects.filter(dataset=dataset, deleted=False))
    new_rows_to_create = []

    for i in range(num_rows_to_generate):
        new_rows_to_create.append(Row(dataset=dataset, order=existing_row_count + i))

    Row.objects.bulk_create(new_rows_to_create)
    new_rows_id = [str(row.id) for row in new_rows_to_create]
    logger.info(f"Successfully created {len(new_rows_id)} new empty rows.")

    # 3. Prepare `validated_data` dictionary
    validated_data = {
        "dataset": {
            "description": dataset_description,
            "objective": dataset_objective,
            "patterns": "",
        },
        "num_rows": num_rows_to_generate,
    }
    gen_columns = ColumnSerializer(data=total_columns, many=True)
    gen_columns.is_valid()
    # 4. Sanitize column definitions for the agent
    gen_columns_serialized = []
    for col in gen_columns.data:
        description = col.get("description")
        if not description:
            description = f"Generate a plausible value for the column named '{col.get('name')}' which is part of a dataset described as: {dataset_description}"

        clean_col = {
            "name": col.get("name", ""),
            "data_type": col.get("data_type", ""),
            "description": description,
        }
        gen_columns_serialized.append(clean_col)
    logger.info("Sanitized column definitions for the agent.")

    # 5. Call the actual task
    logger.info(
        f"\nCalling `generate_new_rows` task to populate {num_rows_to_generate} rows..."
    )
    try:
        generate_new_rows(
            dataset_id,
            validated_data,
            gen_columns_serialized,
            new_rows_id,
            sample_size_reference_data=sample_size,
        )
        logger.info("\n--- Task Finished Successfully ---")
        logger.info("Check the database for the newly populated rows.")
    except Exception as e:
        logger.error("\n--- ERROR during task execution ---")
        logger.error(f"An error occurred: {e}")


@temporal_activity(time_limit=3600, queue="tasks_xl")
def generate_new_rows(
    dataset_id, validated_data, gen_columns, new_rows_id, sample_size_reference_data=5
):
    agent = SyntheticDataAgent()
    dataset = Dataset.objects.get(id=dataset_id)
    total_columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
        source__in=[
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        ]
    )
    all_rows = Row.objects.filter(dataset=dataset, deleted=False).exclude(
        id__in=new_rows_id
    )
    # if all_rows.count() > sample_size_reference_data:
    #     sampled_row_ids = random.sample(
    #         list(all_rows.values_list("id", flat=True)), sample_size_reference_data
    #     )
    # else:
    sampled_row_ids = list(all_rows.values_list("id", flat=True))

    if len(gen_columns) > 0:
        payload = {
            "requirements": {
                "Dataset Name": dataset.name,
                "Dataset Description": validated_data["dataset"]["description"],
                "Objective": validated_data["dataset"]["objective"],
                "patterns": validated_data["dataset"]["patterns"],
            },
            "constraints": [
                {
                    "field": col.get("name"),
                    "type": col.get("data_type"),
                    "content": col.get("description", ""),
                    **(
                        {"min length": col["min_length"]}
                        if col.get("min_length") is not None
                        else {}
                    ),
                    **(
                        {"max length": col["max_length"]}
                        if col.get("max_length") is not None
                        else {}
                    ),
                    **(
                        {"values": col["values"]}
                        if col.get("values") is not None
                        else {}
                    ),
                }
                for col in gen_columns
            ],
            "schema": {col["name"]: {"type": col["data_type"]} for col in gen_columns},
            "batch_size": validated_data["num_rows"],
            "reference_data": {
                col.name: [
                    cell.value
                    for cell in Cell.objects.filter(
                        column__name=col.name,
                        dataset=dataset,
                        row__id__in=sampled_row_ids,  # foreign key mapping to get row from sampled row ids
                    )
                ]
                for col in total_columns
            },
        }

        logger.info(f"payload: {payload}")

        synthetic_df = agent.generate_and_validate(payload)

        for col in total_columns:
            for i in range(validated_data["num_rows"]):
                try:
                    value = synthetic_df.iloc[i][col.name]
                except KeyError:
                    value = ""

                # using get_or_create to avoid having to create cells every time we create rows
                cell, _ = Cell.objects.get_or_create(
                    dataset=dataset,
                    column=col,
                    row_id=new_rows_id[i],
                )
                if isinstance(value, np.generic):
                    value = value.item()

                cell.value = value
                cell.status = CellStatus.PASS.value
                cell.save()

            col.status = StatusType.COMPLETED.value
            col.save()

    else:
        logger.info("ADDING EMPTY VALUES FOR NEW ROWS")
        for col in total_columns:
            for i in range(validated_data["num_rows"]):
                # using get_or_create to avoid having to create cells every time we create rows
                cell, _ = Cell.objects.get_or_create(
                    dataset=dataset,
                    column=col,
                    row_id=new_rows_id[i],
                )

                cell.value = ""
                cell.status = CellStatus.PASS.value
                cell.save()

            col.status = StatusType.COMPLETED.value
            col.save()


@temporal_activity(time_limit=3600, queue="tasks_xl")
def generate_new_columns(
    dataset_id,
    row_ids,
    validated_data,
    new_columns_required_info,
    new_column_db_model_ids,
    gen_columns,
    max_order,
):
    agent = SyntheticDataAgent()
    dataset = Dataset.objects.get(id=dataset_id)
    rows = Row.objects.filter(id__in=row_ids)
    new_column_db_models = list(Column.objects.filter(id__in=new_column_db_model_ids))
    total_columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
        source__in=[
            SourceChoices.EXPERIMENT.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        ]
    )
    payload = {
        "requirements": {
            "Dataset Name": dataset.name,
            "Dataset Description": validated_data["dataset"]["description"],
            "Objective": validated_data["dataset"]["objective"],
            "patterns": validated_data["dataset"]["patterns"],
        },
        "constraints": [
            {
                "field": col.get("name"),
                "type": col.get("data_type"),
                "content": col.get("description", ""),
                **(
                    {"min length": col["min_length"]}
                    if col.get("min_length") is not None
                    else {}
                ),
                **(
                    {"max length": col["max_length"]}
                    if col.get("max_length") is not None
                    else {}
                ),
                **({"values": col["values"]} if col.get("values") is not None else {}),
            }
            for col in new_columns_required_info
        ],
        "schema": {
            col["name"]: {"type": col["data_type"]} for col in new_columns_required_info
        },
        "reference_data": {
            col.name: [
                cell.value
                for cell in Cell.objects.filter(
                    column__name=col.name,
                    dataset=dataset,
                    row__id__in=row_ids,  # foreign key mapping to get row from sampled row ids
                )
            ]
            for col in total_columns
            if col not in new_column_db_models
        },
        "batch_size": max_order,
    }

    # logger.info(f"payload: {payload}")

    synthetic_columns = asyncio.run(agent.generate_column_data(payload))

    for row_index, row in enumerate(rows):
        for col in new_column_db_models:
            try:
                value = synthetic_columns.iloc[row_index][col.name]
                # weird bug with model generating arrays for booleans, temp fix
                if isinstance(value, np.generic):
                    value = value.item()
            except KeyError:
                value = ""

            cell = Cell.objects.get(dataset=dataset, column=col, row=row)
            cell.value = value
            cell.status = CellStatus.PASS.value
            cell.save()


@temporal_activity(time_limit=3600 * 12, queue="tasks_xl")
def create_synthetic_dataset(
    validated_data,
    dataset_id,
    organization_id,
    creating_synthetic_dataset,
    request_uuid=None,
):
    try:
        organization = Organization.objects.get(id=organization_id)

        # If no request_uuid provided, try to get it from the task manager
        task_manager = SyntheticTaskManager()
        if not request_uuid:
            request_uuid = task_manager.start_task(str(dataset_id))

        try:
            from ee.usage.models.usage import APICallTypeChoices
        except ImportError:
            from tfc.oss_stubs.usage import APICallTypeChoices
        try:
            from ee.usage.schemas.event_types import BillingEventType
        except ImportError:
            BillingEventType = None
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        _usage_check = check_usage(
            str(organization.id), BillingEventType.SYNTHETIC_DATA_GENERATION
        )
        if not _usage_check.allowed:
            raise ValueError(_usage_check.reason or "Usage limit exceeded")

        agent = SyntheticDataAgent(dataset_id=dataset_id, request_uuid=request_uuid)

        dataset = Dataset.objects.get(id=dataset_id)
        payload = {
            "requirements": {
                "Dataset Name": validated_data["dataset"]["name"],
                "Dataset Description": validated_data["dataset"]["description"],
                "Objective": validated_data["dataset"]["objective"],
                "patterns": validated_data["dataset"]["patterns"],
            },
            "constraints": [
                {
                    **{
                        "field": col["name"],
                        "type": col["data_type"],
                        "content": col["description"],
                        **(
                            {"min length": col["min_length"]}
                            if "min_length" in col
                            else {}
                        ),
                        **(
                            {"max length": col["max_length"]}
                            if "max_length" in col
                            else {}
                        ),
                    },
                    **col,
                }
                for col in validated_data["columns"]
            ],
            "schema": {
                col["name"]: {"type": col["data_type"]}
                for col in validated_data["columns"]
            },
            "batch_size": validated_data["num_rows"],
        }
        kb_id = validated_data.get("kb_id", None)
        if kb_id:
            indexer = KBIndexer()
            doc_ids = indexer.get_subset_kb_id(
                validated_data["dataset"]["description"], kb_id
            )
            doc_ids = [str(u) for u in doc_ids]
            if doc_ids:
                payload.update(
                    {
                        "knowledge_base": {
                            "table_name": KB_TABLE_NAME,
                            "kb_id": kb_id,
                            "doc_ids": doc_ids,
                        }
                    }
                )

        logger.info(f"payload: {payload}")

        synthetic_df = agent.generate_and_validate(payload)
        if not agent.should_continue():
            return
        tik_total_tokens = 0
        for col in synthetic_df.columns:
            for value in synthetic_df[col]:
                tik_total_tokens += count_text_tokens(str(value))

        rows = list(Row.objects.filter(dataset=dataset))

        if len(rows) < synthetic_df.shape[0]:
            logger.error(
                f"Not enough rows in the database. Need {synthetic_df.shape[0]} rows, but only have {len(rows)}."
            )
            # remove extra rows from synthetic_df if there are not enough rows in the database
            synthetic_df = synthetic_df.iloc[: len(rows)]
            # raise ValueError(
            #     f"Not enough rows in database. Expected {synthetic_df.shape[0]} but found {len(rows)}"
            # )
        synthetic_df = synthetic_df.astype(str)
        for column_name in synthetic_df.columns:
            try:
                column = Column.objects.get(
                    name=column_name,
                    dataset=dataset,
                )

                for df_index in range(min(synthetic_df.shape[0], len(rows))):
                    try:
                        value = synthetic_df.iloc[df_index][column_name]

                        cell = Cell.objects.get(
                            dataset=dataset, column=column, row=rows[df_index]
                        )

                        cell.value = value
                        cell.status = CellStatus.PASS.value
                        cell.save()
                    except Exception as row_error:
                        logger.error(
                            f"Error processing row {df_index} for column {column_name}: {str(row_error)}"
                        )
                        continue

                column.status = StatusType.COMPLETED.value
                column.save()
            except Column.DoesNotExist:
                logger.error(f"Column {column_name} does not exist in the database")
                continue
            except Exception as col_error:
                logger.error(f"Error processing column {column_name}: {str(col_error)}")
                continue
        agent.update_current_creation(100.0)

        recommendations = EvalRecommender(dataset_id=dataset_id).recommend_evals()
        recommend_evals = recommendations.get("recommended_evals", [])
        (
            recommend_evals.append("Deterministic Evals")
            if "Deterministic Evals" not in recommend_evals
            else recommend_evals
        )
        dataset.dataset_config.update({"eval_recommendations": recommend_evals})
        dataset.save()

        if creating_synthetic_dataset:
            api_call_type = APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value
            is_futureagi_eval = False
            api_call_config = {
                "reference_id": "",
                "is_futureagi_eval": is_futureagi_eval,
                "input_tokens": int(tik_total_tokens),
            }

            api_call_log_row = log_and_deduct_cost_for_api_request(
                organization,
                api_call_type,
                config=api_call_config,
                source="synthetic_dataset",
                workspace=dataset.workspace,
            )

            if not api_call_log_row:
                raise ValueError(
                    "API call not allowed : Error validating the api call."
                )

            if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
                raise ValueError("API call not allowed : ", api_call_log_row.status)

            # Dual-write: emit usage event for new billing system (cost-based)
            try:
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.config import BillingConfig
                except ImportError:
                    BillingConfig = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                actual_cost = getattr(agent, "cost", {}).get("total_cost", 0)
                if not actual_cost and hasattr(agent, "llm"):
                    actual_cost = getattr(agent.llm, "cost", {}).get("total_cost", 0)
                credits = BillingConfig.get().calculate_ai_credits(actual_cost)

                emit(
                    UsageEvent(
                        org_id=str(organization.id),
                        event_type=api_call_type,
                        amount=credits,
                        properties={
                            "source": "synthetic_dataset",
                            "source_id": str(dataset.id),
                            "raw_cost_usd": str(actual_cost),
                        },
                    )
                )
            except Exception:
                pass  # Metering failure must not break the action

    except Exception as e:
        logger.exception(f"Error in create_synthetic_dataset: {str(e)}")
        try:
            dataset = Dataset.objects.get(id=dataset_id)
            dataset.status = StatusType.FAILED.value
            dataset.save()
        except Exception as inner_e:
            logger.error(f"Error updating dataset status: {str(inner_e)}")
        raise e


def remove_from_kb(deleted_files, kb_file_id, org_id):
    try:
        futures = []
        result = []
        kb_file = KnowledgeBaseFile.all_objects.get(id=kb_file_id)
        with ThreadPoolExecutor(max_workers=5) as executor:
            for file in deleted_files:
                indexer = KBIndexer()
                # Wrap function with OTel context propagation for thread safety
                wrapped_remove_chunks = wrap_for_thread(indexer.remove_chunks_from_kb)
                futures.append(
                    executor.submit(wrapped_remove_chunks, file, kb_file.id, org_id)
                )
            for future in as_completed(futures):
                try:
                    future_result = future.result()
                    if future_result:
                        if future_result.get("error", None):
                            result.append(
                                {
                                    "file_id": future_result["file_id"],
                                    "status": StatusType.FAILED.value,
                                    "error": future_result["error"],
                                }
                            )
                        else:
                            result.append(
                                {
                                    "file_id": future_result["file_id"],
                                    "status": StatusType.COMPLETED.value,
                                }
                            )
                except Exception as e:
                    logger.error(f"Error in future result: {str(e)}")
                    file_id = None
                    for i, f in enumerate(futures):
                        if f == future:
                            file_id = (
                                deleted_files[i] if i < len(deleted_files) else None
                            )
                            break

                    result.append(
                        {
                            "file_id": file_id,
                            "status": StatusType.FAILED.value,
                            "error": str(e),
                        }
                    )

            all_successful = all(
                item.get("status") == StatusType.COMPLETED.value for item in result
            )
            any_failed = any(
                item.get("status") == StatusType.FAILED.value for item in result
            )

            if all_successful:
                kb_file.status = StatusType.COMPLETED.value
            elif any_failed and len(result) > 0:
                kb_file.status = StatusType.PARTIAL_COMPLETED.value
            elif len(result) == 0:
                kb_file.status = StatusType.FAILED.value

            if result:
                errors = [item.get("error") for item in result if item.get("error")]
                kb_file.last_error = errors[-1] if errors else kb_file.last_error

            kb_file.save()

        return result

    except Exception as e:
        logger.exception(f"Error in remove_from_kb: {str(e)}")

        try:
            kb_file = KnowledgeBaseFile.all_objects.get(id=kb_file_id)
            kb_file.status = StatusType.FAILED.value
            kb_file.last_error = str(e)
            kb_file.save()
        except Exception as inner_e:
            logger.error(f"Error updating KB status: {inner_e}")

        raise e


@temporal_activity(time_limit=3600, queue="tasks_l")
def ingest_files_to_s3(files, kb_id, org):
    if not files:
        return None

    # Check if KB was deleted or cancelled before starting
    if is_kb_deleted_or_cancelled(kb_id):
        logger.info(f"KB {kb_id} was deleted/cancelled, skipping file ingestion")
        return None

    try:
        ingest_futures = []
        docs = []
        error_files = []
        latest_error = None
        kb_file = KnowledgeBaseFile.objects.get(id=kb_id)
        kb_file.status = StatusType.PROCESSING.value
        kb_file.save()

        with ThreadPoolExecutor(max_workers=5) as executor:
            for file_data, file_path in files.items():
                # Check if KB was deleted or cancelled before submitting each file
                if is_kb_deleted_or_cancelled(kb_id):
                    logger.info(
                        f"KB {kb_id} was deleted/cancelled during ingestion, stopping"
                    )
                    executor.shutdown(wait=False, cancel_futures=True)
                    return None

                indexer = KBIndexer()
                # Wrap function with OTel context propagation for thread safety
                wrapped_process_s3_file = wrap_for_thread(indexer.process_s3_file)
                ingest_futures.append(
                    executor.submit(
                        wrapped_process_s3_file, file_path, file_data, kb_id, org
                    )
                )

            for future in as_completed(ingest_futures):
                # Check if KB was deleted or cancelled while processing
                if is_kb_deleted_or_cancelled(kb_id):
                    logger.info(
                        f"KB {kb_id} was deleted/cancelled during ingestion, stopping"
                    )
                    executor.shutdown(wait=False, cancel_futures=True)
                    return None

                try:
                    result = future.result()
                    if result and "file_id" in result:
                        if result.get("error"):
                            error_files.append(result["file_id"])
                            latest_error = result["error"]
                        else:
                            docs.append(result["file_id"])
                except Exception as e:
                    logger.error(f"Error processing file: {str(e)}")
                    latest_error = str(e)

        # Final check before updating status
        if is_kb_deleted_or_cancelled(kb_id):
            logger.info(f"KB {kb_id} was deleted/cancelled, skipping status update")
            return None

        if docs:
            Files.objects.filter(id__in=docs).update(status=StatusType.COMPLETED.value)
        if error_files:
            Files.objects.filter(id__in=error_files).update(
                status=StatusType.FAILED.value
            )

        kb_file.refresh_from_db()
        file_statuses = list(kb_file.files.values_list("status", flat=True))
        kb_file.last_error = latest_error[:10000] if latest_error else None

        if any(status == StatusType.PROCESSING.value for status in file_statuses):
            kb_file.status = StatusType.PROCESSING.value
        elif all(status == StatusType.FAILED.value for status in file_statuses):
            kb_file.status = StatusType.FAILED.value
        elif any(status == StatusType.FAILED.value for status in file_statuses):
            kb_file.status = StatusType.PARTIAL_COMPLETED.value
        elif all(status == StatusType.COMPLETED.value for status in file_statuses):
            kb_file.status = StatusType.COMPLETED.value
        else:
            kb_file.status = StatusType.COMPLETED.value

        kb_file.save()

        return docs

    except Exception as e:
        logger.exception(f"Error processing files: {e}")

        try:
            kb_file = KnowledgeBaseFile.objects.get(id=kb_id)
            kb_file.status = StatusType.FAILED.value
            kb_file.last_error = str(e)
            kb_file.save()
        except Exception as inner_e:
            logger.error(f"Error updating KB status: {inner_e}")

        return None


@temporal_activity(time_limit=3600, queue="tasks_l")
def remove_kb_files(files, org, kb_id):
    if not kb_id:
        return None
    try:
        result = None
        kb_files_to_update = []

        if files:
            try:
                deleted_files = Files.objects.filter(id__in=files)
                kb_file = KnowledgeBaseFile.all_objects.get(
                    id=kb_id, organization_id=org
                )
                kb_file.status = StatusType.PROCESSING.value
                kb_file.save()
                kb_files_to_update.append(kb_file)

                result = remove_from_kb(
                    deleted_files.values_list("id", flat=True), str(kb_id), org
                )
            except Exception as e:
                logger.error(f"Error removing files from KB {kb_id}: {str(e)}")
                for kb_file in kb_files_to_update:
                    kb_file.status = StatusType.FAILED.value
                    kb_file.last_error = str(e)
                    kb_file.save()

                Files.objects.filter(id__in=files).update(
                    status=StatusType.FAILED.value
                )
                raise e
        else:
            kbs = KnowledgeBaseFile.all_objects.filter(
                id__in=kb_id, organization_id=org
            ).prefetch_related("files")

            for kb_file in kbs:
                try:
                    deleted_files = kb_file.files.filter(deleted=False)
                    deleted_files.update(status=StatusType.DELETING.value)

                    kb_file.status = StatusType.PROCESSING.value
                    kb_file.save()
                    kb_files_to_update.append(kb_file)

                    result = remove_from_kb(
                        deleted_files.values_list("id", flat=True), str(kb_file.id), org
                    )
                except Exception as e:
                    logger.error(f"Error removing files from KB {kb_file.id}: {str(e)}")
                    kb_file.status = StatusType.FAILED.value
                    kb_file.last_error = str(e)
                    kb_file.save()

                    deleted_files.update(status=StatusType.FAILED.value)
                    continue

        if result:
            for res in result:
                try:
                    file_instance = Files.objects.filter(id=res["file_id"]).first()
                    if file_instance:
                        meta = json.loads(file_instance.metadata)
                        if res.get("error", None):
                            meta.update({"error": res["error"]})
                        file_instance.metadata = json.dumps(meta)
                        file_instance.status = res["status"]
                        file_instance.deleted = (
                            True
                            if res["status"] == StatusType.COMPLETED.value
                            else False
                        )
                        file_instance.save()
                except Exception as e:
                    logger.error(
                        f"Error updating file status for file {res.get('file_id')}: {str(e)}"
                    )

        return result

    except Exception as e:
        logger.exception(f"Error deleting file from Knowledge Base: {e}")

        if kb_id and isinstance(kb_id, str):
            try:
                kb_file = KnowledgeBaseFile.all_objects.get(
                    id=kb_id, organization_id=org
                )
                kb_file.status = StatusType.FAILED.value
                kb_file.last_error = str(e)
                kb_file.save()
            except Exception:
                pass

        return None


@temporal_activity(time_limit=60, queue="default")
def delete_unused_compare_folder():
    try:
        compare_folder_path = os.path.join(settings.BASE_DIR, "compare")
        for folder in os.listdir(compare_folder_path):
            folder_path = os.path.join(compare_folder_path, folder)
            if os.path.isdir(folder_path):
                creation_time = os.path.getctime(folder_path)
                if (time.time() - creation_time) > 7200:
                    try:
                        delete_compare_folder(folder)
                    except Exception as e:
                        logger.error(
                            f"Error deleting compare folder {folder}: {str(e)}"
                        )
                    shutil.rmtree(folder_path)
                    logger.info(f"Deleted folder: {folder_path}")
    except Exception as e:
        logger.error(f"Error deleting unused compare folder: {str(e)}")
