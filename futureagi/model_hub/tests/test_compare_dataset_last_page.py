import json
from types import SimpleNamespace
import uuid

import pytest

from model_hub.views.develop_dataset import (
    CompareDatasetsView,
    _prepare_compare_dataset_impl,
)
from tfc.utils.storage import get_compare_metadata_path


def _run_compare_dataset_pipeline(prepare_fn, row_count, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    compare_id = str(uuid.uuid4())
    dataset_id = 1
    main_ds_id = str(dataset_id)
    base_column_name = "test_col"
    main_base_column = SimpleNamespace(id=uuid.uuid4(), name=base_column_name)

    common_base_values = [f"val_{i}" for i in range(row_count)]
    data_by_dataset = {main_ds_id: {}}

    for i, val in enumerate(common_base_values):
        cell = SimpleNamespace(
            value=f"val_{i}",
            status="completed",
            value_infos="{}",
            row_id=uuid.uuid4(),
        )
        data_by_dataset[main_ds_id][val] = {base_column_name: cell}

    uploaded_pages = {}

    def mock_upload_compare_json_to_s3(*args, **kwargs):
        uploaded_pages[kwargs["page_name"]] = kwargs["compare_json"]

    monkeypatch.setattr(
        "model_hub.views.develop_dataset.upload_compare_json_to_s3",
        mock_upload_compare_json_to_s3,
    )

    prepare_fn(
        dataset_id=dataset_id,
        common_base_values=common_base_values,
        base_column_name=base_column_name,
        data_by_dataset=data_by_dataset,
        comparison_datasets=[],
        columns_lookup={},
        main_base_column=main_base_column,
        common_columns=set(),
        compare_id=compare_id,
        column_config={"columns": []},
        dataset_info={},
        dynamic_sources={},
    )

    metadata_path = get_compare_metadata_path(compare_id)
    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    return uploaded_pages, metadata


@pytest.mark.django_db
@pytest.mark.parametrize(
    "prepare_fn",
    [
        _prepare_compare_dataset_impl,
        lambda **kwargs: CompareDatasetsView().prepare_compare_dataset(**kwargs),
    ],
    ids=["impl", "view"],
)
def test_compare_dataset_last_page_pagination(prepare_fn, monkeypatch, tmp_path):
    # Test 11 rows: page_1 has 10 rows, page_2 has exactly 1 row (row 10)
    uploaded_pages_11, metadata_11 = _run_compare_dataset_pipeline(
        prepare_fn, 11, monkeypatch, tmp_path
    )
    page_1 = uploaded_pages_11["page_1.json"]["table"]
    page_2 = uploaded_pages_11["page_2.json"]["table"]
    file_row_ids_11 = metadata_11["file_row_ids"]

    assert len(page_1) == 10
    assert len(page_2) == 1
    assert file_row_ids_11[str(page_1[0]["row_id"])] == "page_1"
    assert file_row_ids_11[str(page_2[0]["row_id"])] == "page_2"

    # Test 15 rows: page_2 has 5 rows
    uploaded_pages_15, _ = _run_compare_dataset_pipeline(
        prepare_fn, 15, monkeypatch, tmp_path
    )
    assert len(uploaded_pages_15["page_1.json"]["table"]) == 10
    assert len(uploaded_pages_15["page_2.json"]["table"]) == 5
