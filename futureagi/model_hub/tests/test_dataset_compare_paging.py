"""Compare endpoint paging and compare_id reuse, driven like CompareData.jsx.

The grid requests ten-row blocks: block 0 without a compare_id, later blocks
with the compare_id returned by block 0. Only the S3 page transport is
replaced with an in-memory store; page writing and slicing are the real code.

transaction=True is required because the view builds the comparison in
ThreadPoolExecutor workers, which open their own database connections and
cannot see rows created inside a test transaction.
"""

import json
import threading

import pytest
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

COMPARE_URL = "/model-hub/datasets/{dataset_id}/compare-datasets/"
PAGE_SIZE = 10


@pytest.fixture
def page_store(monkeypatch, tmp_path):
    """In-memory replacement for the compare page JSON objects."""
    store = {}
    lock = threading.Lock()

    def upload(compare_json, compare_id, page_name, bucket_name=None):
        key = f"compare/{compare_id}/{page_name}"
        with lock:
            store[key] = json.loads(json.dumps(compare_json))
        return key

    def download(object_key, bucket_name=None):
        with lock:
            if object_key not in store:
                raise ValueError(f"missing object {object_key}")
            return json.loads(json.dumps(store[object_key]))

    monkeypatch.setattr(
        "model_hub.views.develop_dataset.upload_compare_json_to_s3", upload
    )
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.download_json_from_s3", download
    )
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    return store


def _make_dataset(organization, workspace, name, inputs, output_prefix):
    dataset = Dataset.objects.create(
        name=name, organization=organization, workspace=workspace
    )
    input_col = Column.objects.create(
        name="input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    output_col = Column.objects.create(
        name="output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    for order, value in enumerate(inputs):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(dataset=dataset, column=input_col, row=row, value=value)
        Cell.objects.create(
            dataset=dataset,
            column=output_col,
            row=row,
            value=f"{output_prefix}-{value}",
        )
    return dataset, input_col, output_col


def _post(auth_client, dataset_id, other_ids, page, compare_id, base="input"):
    return auth_client.post(
        COMPARE_URL.format(dataset_id=dataset_id),
        {
            "dataset_ids": [str(other_id) for other_id in other_ids],
            "base_column_name": base,
            "page_size": PAGE_SIZE,
            "current_page_index": page,
            "compare_id": compare_id,
        },
        format="json",
    )


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.content[:500]
    body = response.json()
    assert body.get("status") is True, body
    return body["result"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("n_common", [11, 12, 21])
def test_compare_blocks_return_each_joined_row_once(
    auth_client, organization, workspace, page_store, n_common
):
    shared = [f"shared-{index:02d}" for index in range(n_common)]
    left, left_input, _ = _make_dataset(
        organization, workspace, "Paging left", [*shared, "left-only"], "left"
    )
    right, _, _ = _make_dataset(
        organization, workspace, "Paging right", [*shared, "right-only"], "right"
    )

    blocks = []
    compare_id = None
    total_blocks = (n_common + PAGE_SIZE - 1) // PAGE_SIZE
    for page in range(total_blocks):
        result = _result(_post(auth_client, left.id, [right.id], page, compare_id))
        compare_id = compare_id or result["metadata"]["compare_id"]
        assert result["metadata"] == {
            "compare_id": compare_id,
            "total_rows": n_common,
            "total_pages": total_blocks,
        }
        blocks.append(
            [row[str(left_input.id)]["cell_value"] for row in result["table"]]
        )

    remainder = n_common % PAGE_SIZE or PAGE_SIZE
    stored_rows = {
        key.rsplit("/", 1)[-1]: len(value["table"])
        for key, value in page_store.items()
        if key.startswith(f"compare/{compare_id}/")
    }
    assert stored_rows == {
        f"page_{page}.json": PAGE_SIZE if page < total_blocks else remainder
        for page in range(1, total_blocks + 1)
    }
    assert [len(block) for block in blocks] == [PAGE_SIZE] * (total_blocks - 1) + [
        remainder
    ]
    returned = [value for block in blocks for value in block]
    assert sorted(returned) == shared


@pytest.mark.django_db(transaction=True)
def test_cached_compare_id_is_rejected_for_another_selection(
    auth_client, organization, workspace, page_store
):
    shared = [f"stale-{index:02d}" for index in range(3)]
    d0, _, _ = _make_dataset(organization, workspace, "Stale d0", shared, "d0")
    d1, _, d1_output = _make_dataset(organization, workspace, "Stale d1", shared, "d1")
    d2, _, _ = _make_dataset(organization, workspace, "Stale d2", shared, "d2")

    compare_a = _result(_post(auth_client, d0.id, [d1.id], 0, None))["metadata"][
        "compare_id"
    ]

    # The comparison it was built for may keep reusing it.
    reused = _result(_post(auth_client, d0.id, [d1.id], 0, compare_a))
    assert reused["metadata"]["compare_id"] == compare_a
    assert all(str(d1_output.id) in row for row in reused["table"])

    # A different compared dataset or base column must not be served d0-vs-d1.
    for other_ids, base in (([d2.id], "input"), ([d1.id], "output")):
        response = _post(auth_client, d0.id, other_ids, 0, compare_a, base=base)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            other_ids,
            base,
            response.content[:500],
        )
        assert str(d1_output.id) not in response.content.decode()
