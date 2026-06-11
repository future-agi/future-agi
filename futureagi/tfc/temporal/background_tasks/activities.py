"""
Temporal activities for background tasks.

These activities replace the IMPROVED_EXECUTOR ThreadPoolExecutor pattern
with Temporal workflows for better reliability and observability.
"""

from tfc.temporal.drop_in import temporal_activity


@temporal_activity(time_limit=600, queue="default")
def run_post_registration_activity(user_id: str, generated_password: str):
    """
    Process post-registration steps.

    - Sends signup email
    - Sends HubSpot notification
    - Sends Slack notification
    """
    from accounts.utils import _run_post_registration

    return _run_post_registration(user_id, generated_password)


@temporal_activity(time_limit=3600, queue="tasks_l")
def process_huggingface_dataset_activity(
    dataset_id: str,
    huggingface_dataset_name: str,
    huggingface_dataset_config: str,
    huggingface_dataset_split: str,
    organization_id: str,
    num_rows: int,
    column_order: list,
    rows: dict,
):
    """
    Process HuggingFace dataset creation/import asynchronously.
    """
    from model_hub.views.utils.hugginface import process_huggingface_dataset

    return process_huggingface_dataset(
        dataset_id,
        huggingface_dataset_name,
        huggingface_dataset_config,
        huggingface_dataset_split,
        organization_id,
        num_rows,
        column_order,
        rows,
    )


@temporal_activity(time_limit=300, queue="default")
def delete_compare_folder_activity(compare_id: str):
    """
    Delete compare folder and associated files.
    """
    import os
    import shutil

    from tfc.utils.storage import delete_compare_folder, get_compare_local_dir

    compare_dir = get_compare_local_dir(compare_id)
    if os.path.isdir(compare_dir):
        shutil.rmtree(compare_dir)
    delete_compare_folder(compare_id)


@temporal_activity(time_limit=3600, queue="tasks_l")
def prepare_compare_dataset_activity(
    dataset_id: str,
    common_base_values: list,
    base_column_name: str,
    data_by_dataset: dict,
    comparison_datasets: list,
    columns_lookup: dict,
    main_base_column: dict,
    common_columns: list,
    compare_id: str,
    column_config: list,
    dataset_info: list,
    dynamic_sources: dict,
):
    """
    Prepare compare dataset JSON files.

    This replicates the CompareDatasetsView.prepare_compare_dataset method.
    """
    from model_hub.views.develop_dataset import _prepare_compare_dataset_impl

    return _prepare_compare_dataset_impl(
        dataset_id,
        common_base_values,
        base_column_name,
        data_by_dataset,
        comparison_datasets,
        columns_lookup,
        main_base_column,
        common_columns,
        compare_id,
        column_config,
        dataset_info,
        dynamic_sources,
    )


@temporal_activity(time_limit=600, queue="default")
def ingest_kb_files_activity(file_metadata: dict, kb_id: str, org_id: str):
    """
    Wait for S3 uploads to complete, then ingest knowledge base files.

    S3 uploads happen in background daemon threads on the backend pod.
    This activity polls S3 to check if files exist before triggering
    the ingestion process (embedding generation, chunking, etc.).

    Args:
        file_metadata: Dict mapping file_id -> {name, extension}
        kb_id: Knowledge Base ID
        org_id: Organization ID
    """
    from model_hub.utils.kb_helpers import ingest_kb_files_impl

    return ingest_kb_files_impl(file_metadata, kb_id, org_id)


__all__ = [
    "run_post_registration_activity",
    "process_huggingface_dataset_activity",
    "delete_compare_folder_activity",
    "prepare_compare_dataset_activity",
    "ingest_kb_files_activity",
]
