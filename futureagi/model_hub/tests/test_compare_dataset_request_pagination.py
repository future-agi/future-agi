"""Request-sized slices over the comparison's ten-row physical pages."""

import copy
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views import develop_dataset
from tfc.utils.storage import get_compare_metadata_path

# The real POST path uses worker connections, so fixtures must be committed.
pytestmark = pytest.mark.django_db(transaction=True)


class _PageStore:
    def __init__(self):
        self.pages = {}
        self.reads = []
        self.writes = []
        self.lock = threading.Lock()

    def upload(self, *, compare_id, compare_json, page_name):
        key = f"compare/{compare_id}/{page_name}"
        with self.lock:
            self.writes.append(key)
            self.pages[key] = copy.deepcopy(compare_json)

    def download(self, object_key):
        with self.lock:
            self.reads.append(object_key)
            return copy.deepcopy(self.pages[object_key])


@pytest.fixture
def page_store(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    monkeypatch.setattr(develop_dataset, "start_local_sweeper", Mock())
    store = _PageStore()
    monkeypatch.setattr(develop_dataset, "upload_compare_json_to_s3", store.upload)
    monkeypatch.setattr(develop_dataset, "download_json_from_s3", store.download)
    return store


@pytest.fixture
def make_pair(organization, workspace):
    def create(row_count):
        datasets = []
        columns = []
        values = [f"value-{index:02d}" for index in range(row_count)]
        dataset_info = {value: {} for value in values}
        for name in ("Base pagination dataset", "Other pagination dataset"):
            dataset = Dataset.objects.create(
                name=name, organization=organization, workspace=workspace
            )
            column = Column.objects.create(
                name="input",
                dataset=dataset,
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.OTHERS.value,
            )
            rows = Row.objects.bulk_create(
                [Row(dataset=dataset, order=index) for index in range(row_count)]
            )
            Cell.objects.bulk_create(
                [
                    Cell(dataset=dataset, column=column, row=row, value=value)
                    for row, value in zip(rows, values, strict=True)
                ]
            )
            for row, value in zip(rows, values, strict=True):
                dataset_info[value][str(dataset.id)] = str(row.id)
            datasets.append(dataset)
            columns.append(column)
        return SimpleNamespace(
            base=datasets[0],
            other=datasets[1],
            base_column=columns[0],
            values=values,
            dataset_info=dataset_info,
        )

    return create


def _page_key(compare_id, page):
    return f"compare/{compare_id}/page_{page}.json"


def _store_completed_comparison(pair, store):
    """Seed correct pages independently of the unrelated leftover-row writer.

    Reverse the input values so the expected order is physical storage order,
    not lexical order or the database's row order.
    """
    compare_id = str(uuid.uuid4())
    rows = [
        {
            "row_id": str(uuid.uuid4()),
            str(pair.base_column.id): {
                "cell_value": value,
                "cell_row_id": pair.dataset_info[value][str(pair.base.id)],
            },
        }
        for value in reversed(pair.values)
    ]
    chunks = [rows[offset : offset + 10] for offset in range(0, len(rows), 10)]
    for page, chunk in enumerate(chunks, start=1):
        store.pages[_page_key(compare_id, page)] = {
            "column_config": [
                {
                    "id": str(pair.base_column.id),
                    "name": "input",
                    "data_type": DataTypeChoices.TEXT.value,
                }
            ],
            "table": chunk,
        }
    # Even if storage has a later object, only the comparison's own pages count.
    store.pages[_page_key(compare_id, len(chunks) + 1)] = {
        "table": [{"row_id": "outside-comparison"}]
    }
    metadata_path = Path(get_compare_metadata_path(compare_id))
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "total_rows": len(rows),
                "total_pages": len(chunks),
                "total_processed": len(chunks),
                "dataset_info": pair.dataset_info,
                "base_column_name": "input",
                "base_dataset_id": str(pair.base.id),
                "comparison_datasets": [str(pair.base.id), str(pair.other.id)],
                "common_column_names": ["input"],
            }
        ),
        encoding="utf-8",
    )
    return compare_id, rows


def _post(client, pair, page_size, page_index, compare_id=None):
    payload = {
        "dataset_ids": [str(pair.other.id)],
        "base_column_name": "input",
        "page_size": page_size,
        "current_page_index": page_index,
    }
    if compare_id is not None:
        payload["compare_id"] = compare_id
    return client.post(
        f"/model-hub/datasets/{pair.base.id}/compare-datasets/",
        payload,
        format="json",
    )


def _assert_page(response, store, compare_id, rows, page_size, page_index):
    assert response.status_code == status.HTTP_200_OK, response.content
    result = response.data["result"]
    start = page_index * page_size
    end = start + page_size
    assert result["table"] == rows[start:end]
    assert result["metadata"] == {
        "compare_id": compare_id,
        "total_rows": len(rows),
        "total_pages": (len(rows) + page_size - 1) // page_size,
    }
    # Check intersecting physical pages, independently of the view's bounds.
    expected_reads = [
        _page_key(compare_id, page)
        for page, offset in enumerate(range(0, len(rows), 10), start=1)
        if offset < end and start < offset + 10
    ]
    assert sorted(store.reads) == sorted(expected_reads)


@pytest.mark.parametrize("row_count", [15, 25])
@pytest.mark.parametrize("page_size", [1, 7, 10, 15, 20, 25, 40])
def test_cached_requests_partition_stored_rows_in_order(
    auth_client, make_pair, page_store, row_count, page_size
):
    pair = make_pair(row_count)
    compare_id, rows = _store_completed_comparison(pair, page_store)
    collected = []
    for page_index in range((row_count + page_size - 1) // page_size):
        page_store.reads.clear()
        response = _post(auth_client, pair, page_size, page_index, compare_id)
        _assert_page(response, page_store, compare_id, rows, page_size, page_index)
        collected.extend(response.data["result"]["table"])

    assert collected == rows
    assert len({row["row_id"] for row in collected}) == row_count
    assert page_store.writes == []

    # Start beyond the final physical page: return empty without any storage read.
    physical_capacity = len(range(0, row_count, 10)) * 10
    out_of_range = (physical_capacity + page_size - 1) // page_size
    page_store.reads.clear()
    response = _post(auth_client, pair, page_size, out_of_range, compare_id)
    _assert_page(response, page_store, compare_id, rows, page_size, out_of_range)
    assert page_store.reads == []
    assert page_store.writes == []


@pytest.mark.parametrize(
    ("row_count", "page_size", "page_index"),
    [
        (15, 7, 1),  # Rows 7:14 cross the first physical-page boundary.
        (25, 7, 1),
        (15, 7, 2),  # Short last request page.
        (25, 7, 3),
        (25, 15, 1),
        (25, 25, 0),
        (15, 25, 0),  # Request larger than the entire comparison.
        (25, 40, 0),
        (25, 10, 1),  # Existing ten/twenty-row request behavior.
        (25, 20, 1),
        (15, 7, 3),  # Empty physical ranges must not read storage.
        (25, 25, 2),
    ],
)
def test_fresh_and_cached_requests_use_the_same_physical_slice(
    auth_client, make_pair, page_store, row_count, page_size, page_index
):
    # These 15/25-row positive controls have correct existing writer output;
    # do not turn this regression into a test of its separate 11-row defect.
    pair = make_pair(row_count)
    fresh = _post(auth_client, pair, page_size, page_index)
    assert fresh.status_code == status.HTTP_200_OK, fresh.content
    compare_id = fresh.data["result"]["metadata"]["compare_id"]
    metadata = json.loads(Path(get_compare_metadata_path(compare_id)).read_text())
    assert metadata["status"] == "completed"
    stored_pages = [
        page_store.pages[_page_key(compare_id, page)]
        for page in range(1, metadata["total_pages"] + 1)
    ]
    assert [len(page["table"]) for page in stored_pages] == [
        min(10, row_count - offset) for offset in range(0, row_count, 10)
    ]
    rows = [row for page in stored_pages for row in page["table"]]
    assert sorted(row[str(pair.base_column.id)]["cell_value"] for row in rows) == (
        pair.values
    )
    assert len({row["row_id"] for row in rows}) == row_count
    _assert_page(fresh, page_store, compare_id, rows, page_size, page_index)

    page_store.reads.clear()
    page_store.writes.clear()
    cached = _post(auth_client, pair, page_size, page_index, compare_id)
    _assert_page(cached, page_store, compare_id, rows, page_size, page_index)
    assert page_store.writes == []


@pytest.mark.parametrize("initial_processed", [0, 1, None])
def test_cached_request_waits_until_all_requested_pages_are_written(
    auth_client, make_pair, page_store, monkeypatch, initial_processed
):
    pair = make_pair(35)
    compare_id, rows = _store_completed_comparison(pair, page_store)
    metadata_path = get_compare_metadata_path(compare_id)
    metadata = json.loads(Path(metadata_path).read_text())
    metadata["status"] = "processing"
    pending_pages = {
        page: page_store.pages.pop(_page_key(compare_id, page)) for page in range(1, 5)
    }
    progress = iter([initial_processed, 2, 3])

    def read_metadata(_path):
        processed = next(progress)
        # Publish a page only when the writer's metadata read makes it ready.
        for page, contents in pending_pages.items():
            if page <= (processed or 0):
                page_store.pages[_page_key(compare_id, page)] = contents
        return {**metadata, "total_processed": processed}

    read = Mock(side_effect=read_metadata)
    sleep = Mock()
    monkeypatch.setattr(
        develop_dataset.CompareDatasetsView, "read_metadata_safely", read
    )
    monkeypatch.setattr(develop_dataset.time, "sleep", sleep)

    # Rows 15:30 need pages 2 AND 3. Page 4 is not needed or available.
    response = _post(auth_client, pair, 15, 1, compare_id)

    _assert_page(response, page_store, compare_id, rows, 15, 1)
    assert read.call_args_list == [call(metadata_path)] * 3
    assert sleep.call_args_list == [call(0.1)] * 2
    assert _page_key(compare_id, 4) not in page_store.pages


@pytest.mark.parametrize("ready_status", ["processing", "completed"])
def test_cached_request_waits_for_last_page_bounded_by_total_pages(
    auth_client, make_pair, page_store, monkeypatch, ready_status
):
    pair = make_pair(25)
    compare_id, rows = _store_completed_comparison(pair, page_store)
    metadata_path = get_compare_metadata_path(compare_id)
    metadata = json.loads(Path(metadata_path).read_text())
    initial = {**metadata, "status": "processing", "total_processed": 2}
    ready = {
        **metadata,
        "status": ready_status,
        # The writer marks completion with floor(total_rows / 10), which
        # excludes the short last page even though it has been uploaded.
        "total_processed": 2 if ready_status == "completed" else 3,
    }
    last_page_key = _page_key(compare_id, 3)
    last_page = page_store.pages.pop(last_page_key)
    snapshots = iter([initial, ready])

    def read_metadata(_path):
        snapshot = next(snapshots)
        if snapshot is ready:
            page_store.pages[last_page_key] = last_page
        return snapshot

    read = Mock(side_effect=read_metadata)
    sleep = Mock()
    monkeypatch.setattr(
        develop_dataset.CompareDatasetsView, "read_metadata_safely", read
    )
    monkeypatch.setattr(develop_dataset.time, "sleep", sleep)

    # Rows 20:40 would cover pages 3 and 4, but this comparison ends at 3.
    response = _post(auth_client, pair, 20, 1, compare_id)

    _assert_page(response, page_store, compare_id, rows, 20, 1)
    assert read.call_args_list == [call(metadata_path)] * 2
    sleep.assert_called_once_with(0.1)


@pytest.mark.parametrize(
    ("page_size", "page_index", "metadata_status", "total_processed"),
    [
        pytest.param(15, 1, "processing", 3, id="requested-pages-ready"),
        pytest.param(10, 4, "processing", None, id="empty-range"),
        pytest.param(40, 0, "completed", 3, id="completed-short-last-page"),
        pytest.param(10, 2, "completed", None, id="completed-without-progress"),
        pytest.param(10, 2, "failed", None, id="failed-does-not-poll"),
    ],
)
def test_cached_request_does_not_wait_without_pending_requested_pages(
    auth_client,
    make_pair,
    page_store,
    monkeypatch,
    page_size,
    page_index,
    metadata_status,
    total_processed,
):
    pair = make_pair(35)
    compare_id, rows = _store_completed_comparison(pair, page_store)
    metadata_path = get_compare_metadata_path(compare_id)
    metadata = json.loads(Path(metadata_path).read_text())
    metadata.update(status=metadata_status, total_processed=total_processed)
    # An unexpected poll fails immediately, rather than spinning for 300s.
    read = Mock(side_effect=[metadata])
    sleep = Mock()
    monkeypatch.setattr(
        develop_dataset.CompareDatasetsView, "read_metadata_safely", read
    )
    monkeypatch.setattr(develop_dataset.time, "sleep", sleep)

    response = _post(auth_client, pair, page_size, page_index, compare_id)

    _assert_page(response, page_store, compare_id, rows, page_size, page_index)
    read.assert_called_once_with(metadata_path)
    sleep.assert_not_called()


def test_cached_request_times_out_while_requested_pages_are_missing(
    auth_client, make_pair, page_store, monkeypatch
):
    pair = make_pair(25)
    compare_id, _rows = _store_completed_comparison(pair, page_store)
    metadata_path = get_compare_metadata_path(compare_id)
    metadata = json.loads(Path(metadata_path).read_text())
    metadata.update(status="processing", total_processed=0)
    page_store.pages.clear()
    read = Mock(return_value=metadata)
    clock = SimpleNamespace(time=Mock(side_effect=[0, 0, 301]), sleep=Mock())
    monkeypatch.setattr(
        develop_dataset.CompareDatasetsView, "read_metadata_safely", read
    )
    monkeypatch.setattr(develop_dataset, "time", clock)

    response = _post(auth_client, pair, 10, 1, compare_id)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert read.call_args_list == [call(metadata_path)] * 2
    assert page_store.reads == []
    clock.sleep.assert_not_called()


def test_physical_page_completion_order_does_not_reorder_rows(
    auth_client, make_pair, page_store, monkeypatch
):
    pair = make_pair(25)
    compare_id, rows = _store_completed_comparison(pair, page_store)
    original_as_completed = develop_dataset.as_completed
    completion_order = []

    def reverse_page_completions(futures):
        if isinstance(futures, dict):
            # Deliver page futures in reverse order deterministically, without
            # sleeps or changes to the real executor or its database workers.
            for future, page in sorted(
                futures.items(), key=lambda item: item[1], reverse=True
            ):
                future.result()
                completion_order.append(page)
                yield future
        else:
            yield from original_as_completed(futures)

    monkeypatch.setattr(develop_dataset, "as_completed", reverse_page_completions)
    response = _post(auth_client, pair, 25, 0, compare_id)
    _assert_page(response, page_store, compare_id, rows, 25, 0)
    assert completion_order == [3, 2, 1]
    assert page_store.writes == []
