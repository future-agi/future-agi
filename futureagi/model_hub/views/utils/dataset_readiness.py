"""Admission guard for consumers of file imports, after tenant lookup.

Atomic publication prevents partial rows. This additionally prevents an empty
export/copy/run of the queued shell before publication (and reads straddling the
commit). Non-file datasets keep their existing behavior.
"""

from tfc.utils.general_methods import GeneralMethods

PROCESSING_IMPORT_STATES = {"queued", "processing", "creating_cells"}


def dataset_import_grid_response(dataset, *, page_size, page_index, columns_only):
    """Return a stable processing shell before any legacy table inventory reads.

    READ COMMITTED can otherwise see pre-publication Columns and post-publication
    Rows in one request. The tenant-scoped Dataset lookup is the admission point:
    a completed state implies the entire table was committed before later reads.
    Exact continuation reads use their existing repeatable-read snapshot instead.
    """
    config = dataset.dataset_config if isinstance(dataset.dataset_config, dict) else {}
    if config.get("file_processing_status") == "failed":
        # A retry may publish after this lookup too; never read a failed shell.
        return dataset_import_refusal(dataset)
    if config.get("file_processing_status") not in PROCESSING_IMPORT_STATES:
        return None
    result = {"column_config": [], "is_processing_data": True}
    if not columns_only:
        result.update(
            {
                "table": [],
                "dataset_config": config,
                "synthetic_dataset": bool(dataset.synthetic_dataset_config),
                "metadata": {
                    "dataset_name": dataset.name,
                    "total_rows": 0,
                    "total_pages": 0,
                    "page_size": page_size,
                    "current_page_index": page_index,
                    "has_more": False,
                    "next_page_index": None,
                    "next_cursor": None,
                    "is_exact": False,
                    "snapshot_bound": False,
                    "error_messages": [],
                },
            }
        )
    return GeneralMethods().success_response(result)


def dataset_import_refusal(dataset):
    config = dataset.dataset_config if isinstance(dataset.dataset_config, dict) else {}
    state = config.get("file_processing_status")
    if state in PROCESSING_IMPORT_STATES:
        response = GeneralMethods().custom_error_response(
            409,
            "Dataset import is still processing. Try again after it completes.",
            code="dataset_processing",
        )
        response["Retry-After"] = "2"
        return response
    if state == "failed":
        return GeneralMethods().custom_error_response(
            409,
            "Dataset import failed. Upload the file again before using this dataset.",
            code="dataset_import_failed",
        )
    return None
