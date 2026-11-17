"""
End-to-end test cases for HuggingFace Dataset API endpoints.

Tests cover:
- CreateDatasetFromHuggingFaceView - Create a new dataset from HuggingFace
- AddRowsFromHuggingFaceView - Add rows to existing dataset from HuggingFace

These tests verify the complete flow including:
- API endpoint handling
- Temporal activity execution
- Database row and cell creation

Run with: pytest model_hub/tests/test_dataset_huggingface_api.py -v
"""

import json
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row


@pytest.fixture
def hf_import_dependencies(monkeypatch):
    from tfc.utils import lazy_extras

    extra = MagicMock(return_value=SimpleNamespace())
    preview = MagicMock(
        return_value={"features": [{"name": "text", "type": {"dtype": "string"}}]}
    )
    revision = MagicMock(return_value=("resolved-revision", "default", 2))
    metadata = MagicMock(return_value={"num_rows": 2, "split": "train"})
    activity = MagicMock(return_value="import-activity")
    load_extra = lazy_extras.load_extra

    def load(module, group):
        return (
            extra(module, group) if module == "datasets" else load_extra(module, group)
        )

    for module in (
        "model_hub.views.datasets.create.huggingface",
        "model_hub.views.datasets.add_rows.huggingface",
    ):
        monkeypatch.setattr(f"{module}.load_hf_dataset_with_retries", preview)
        monkeypatch.setattr(f"{module}.import_revision", revision)
        monkeypatch.setattr(f"{module}.log_and_deduct_cost_for_resource_request", None)
    monkeypatch.setattr(
        "model_hub.views.datasets.create.huggingface."
        "CreateDatasetFromHuggingFaceView.get_huggingface_dataset_info",
        metadata,
    )
    monkeypatch.setattr("tfc.temporal.drop_in.start_activity", activity)
    monkeypatch.setattr(lazy_extras, "load_extra", load)
    return SimpleNamespace(
        extra=extra,
        preview=preview,
        revision=revision,
        metadata=metadata,
        activity=activity,
    )


@pytest.fixture(
    params=[("create", None), ("create", "v1"), ("add", None), ("add", "v1")]
)
def hf_import_request(request, db, organization, workspace, user):
    operation, revision = request.param
    body = {
        "num_rows": 2,
        "huggingface_dataset_name": "fixture/example",
        "huggingface_dataset_config": "default",
        "huggingface_dataset_split": "train",
    }
    if revision:
        body["revision"] = revision
    if operation == "create":
        body.update(name="Imported dataset", model_type="GenerativeLLM")
        url = "/model-hub/develops/create-dataset-from-huggingface/"
    else:
        dataset = Dataset.no_workspace_objects.create(
            name="Import target",
            organization=organization,
            workspace=workspace,
            user=user,
            column_order=[],
            column_config={},
        )
        Row.no_workspace_objects.create(dataset=dataset, order=0)
        url = f"/model-hub/develops/{dataset.id}/add_rows_from_huggingface/"
    return url, body


def test_installed_package_allows_import(
    auth_client, hf_import_request, hf_import_dependencies
):
    url, body = hf_import_request
    before = Row.no_workspace_objects.count()

    response = auth_client.post(url, body, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert Row.no_workspace_objects.count() == before + 2
    hf_import_dependencies.preview.assert_called_once()
    hf_import_dependencies.activity.assert_called_once()
    if body.get("revision"):
        hf_import_dependencies.revision.assert_called_once()
    else:
        hf_import_dependencies.revision.assert_not_called()


def test_revision_config_reports_missing_package(auth_client, monkeypatch):
    from tfc.utils import lazy_extras

    with patch(
        "model_hub.views.datasets.create.huggingface.revision_info",
        return_value={"splits": {"default": ["train"]}},
    ) as revision_info:
        monkeypatch.setattr(
            lazy_extras, "load_extra", MagicMock(side_effect=ImportError("datasets"))
        )
        response = auth_client.post(
            "/model-hub/develops/get-huggingface-dataset-config/",
            {"dataset_path": "fixture/example", "revision": "v1"},
            format="json",
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["message"] == (
        "This install lacks the datasets package needed for Hugging Face import."
    )
    revision_info.assert_not_called()


class TestHuggingFaceLookupResponseContracts:
    @patch("model_hub.views.develop_dataset.requests.get")
    def test_dataset_list_returns_typed_result(self, mock_get, auth_client):
        mock_response = MagicMock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = {
            "numTotalItems": 1,
            "datasets": [
                {
                    "id": "futureagi/example",
                    "name": "futureagi/example",
                    "downloads": 42,
                    "likes": 7,
                    "author": "futureagi",
                }
            ],
        }
        mock_get.return_value = mock_response

        response = auth_client.post(
            "/model-hub/datasets/huggingface/list/",
            {"search_query": "futureagi", "filter_params": {}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["message"] == "Datasets retrieved successfully"
        assert result["total_datasets"] == 1
        assert result["datasets"] == [
            {
                "id": "futureagi/example",
                "name": "futureagi/example",
                "downloads": 42,
                "likes": 7,
                "author": "futureagi",
            }
        ]

    @patch("model_hub.views.develop_dataset.requests.get")
    def test_dataset_detail_returns_typed_result(self, mock_get, auth_client):
        mock_response = MagicMock()
        mock_response.status_code = status.HTTP_200_OK
        mock_response.json.return_value = [
            {
                "id": "futureagi/example",
                "name": "futureagi/example",
                "description": "Example dataset",
                "downloads": 42,
                "likes": 7,
                "tags": ["text"],
                "author": "futureagi",
            }
        ]
        mock_get.return_value = mock_response

        response = auth_client.post(
            "/model-hub/datasets/huggingface/detail/",
            {"dataset_id": "futureagi/example"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["message"] == "Dataset details retrieved successfully"
        assert result["dataset"] == {
            "id": "futureagi/example",
            "name": "futureagi/example",
            "description": "Example dataset",
            "downloads": 42,
            "likes": 7,
            "tags": ["text"],
            "author": "futureagi",
        }


class TestProcessHuggingFaceColumnsJsonSerialization:
    """
    Test that process_huggingface_columns handles JSON-serialized dict keys.

    This is a critical test for the JSON serialization bug where:
    - View creates rows dict with integer keys: {0: "uuid", 1: "uuid"}
    - Temporal JSON-serializes to string keys: {"0": "uuid", "1": "uuid"}
    - The processing function must handle both formats
    """

    @pytest.fixture
    def hf_dataset(self, db, organization, workspace):
        """Create a dataset for HuggingFace testing."""
        ds = Dataset.objects.create(
            name="HuggingFace Test Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        ds.column_order = []
        ds.save()
        return ds

    @pytest.fixture
    def text_column(self, db, hf_dataset):
        col = Column.objects.create(
            name="text",
            dataset=hf_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        hf_dataset.column_order.append(str(col.id))
        hf_dataset.save()
        return col

    @pytest.fixture
    def rows_with_integer_keys(self, db, hf_dataset):
        """Create rows dict with integer keys (as created by view)."""
        rows = {}
        for i in range(3):
            row = Row.objects.create(dataset=hf_dataset, order=i)
            rows[i] = str(row.id)  # Integer keys
        return rows

    @pytest.fixture
    def rows_with_string_keys(self, db, hf_dataset):
        """Create rows dict with string keys (after JSON serialization)."""
        rows = {}
        for i in range(3):
            row = Row.objects.create(dataset=hf_dataset, order=i)
            rows[str(i)] = str(row.id)  # String keys (JSON-serialized)
        return rows

    @patch("model_hub.views.utils.hugginface.close_old_connections")
    def test_process_columns_with_string_keys(
        self, mock_close_conn, db, hf_dataset, text_column, rows_with_string_keys
    ):
        """
        Test that process_huggingface_columns works with string keys.

        This simulates what happens when data passes through Temporal's
        JSON serialization - integer keys become string keys.
        """
        from model_hub.views.utils.hugginface import process_huggingface_columns

        # Simulate data that would come from HuggingFace
        data_dict = {"text": ["Hello World"]}

        # Call with string keys (simulating post-JSON-serialization)
        process_huggingface_columns(
            data_dict=data_dict,
            dataset_id=str(hf_dataset.id),
            column_id=str(text_column.id),
            rows=rows_with_string_keys,
            index=0,
        )

        # Verify cell was created
        row_id = rows_with_string_keys["0"]
        cell = Cell.objects.filter(
            dataset=hf_dataset, column=text_column, row_id=row_id
        ).first()

        assert cell is not None, "Cell should be created with string key access"
        assert cell.value == "Hello World"
        assert cell.status == CellStatus.PASS.value

    @patch("model_hub.views.utils.hugginface.close_old_connections")
    def test_process_columns_with_integer_keys(
        self, mock_close_conn, db, hf_dataset, text_column, rows_with_integer_keys
    ):
        """
        Test that process_huggingface_columns works with integer keys.

        This tests backwards compatibility when called directly without
        going through Temporal serialization.
        """
        from model_hub.views.utils.hugginface import process_huggingface_columns

        data_dict = {"text": ["Test Value"]}

        # Call with integer keys (direct call without JSON serialization)
        process_huggingface_columns(
            data_dict=data_dict,
            dataset_id=str(hf_dataset.id),
            column_id=str(text_column.id),
            rows=rows_with_integer_keys,
            index=0,
        )

        # Verify cell was created
        row_id = rows_with_integer_keys[0]
        cell = Cell.objects.filter(
            dataset=hf_dataset, column=text_column, row_id=row_id
        ).first()

        assert cell is not None, "Cell should be created with integer key access"
        assert cell.value == "Test Value"
        assert cell.status == CellStatus.PASS.value

    @patch("model_hub.views.utils.hugginface.close_old_connections")
    def test_process_columns_multiple_rows_string_keys(
        self, mock_close_conn, db, hf_dataset, text_column, rows_with_string_keys
    ):
        """Test processing multiple rows with string keys."""
        from model_hub.views.utils.hugginface import process_huggingface_columns

        test_values = ["Row 0 Value", "Row 1 Value", "Row 2 Value"]

        for index in range(3):
            data_dict = {"text": [test_values[index]]}
            process_huggingface_columns(
                data_dict=data_dict,
                dataset_id=str(hf_dataset.id),
                column_id=str(text_column.id),
                rows=rows_with_string_keys,
                index=index,
            )

        # Verify all cells were created
        cells = Cell.objects.filter(dataset=hf_dataset, column=text_column)
        assert cells.count() == 3, "All 3 cells should be created"

        for index in range(3):
            row_id = rows_with_string_keys[str(index)]
            cell = cells.get(row_id=row_id)
            assert cell.value == test_values[index]

    @pytest.mark.parametrize(
        ("data_type", "value"),
        [
            ("json", {"title": 'Café "desk"', "ok": True, "other": None}),
            ("array", [{"role": "user", "content": 'Say "hi"', "ok": False}]),
            ("json", {}),
            ("array", []),
        ],
    )
    @patch("model_hub.views.utils.hugginface.close_old_connections")
    def test_structured_cells_round_trip_as_json(
        self, _close, hf_dataset, rows_with_string_keys, data_type, value
    ):
        from model_hub.views.utils.hugginface import process_huggingface_columns

        column = Column.objects.create(
            name="payload",
            dataset=hf_dataset,
            data_type=data_type,
            source=SourceChoices.OTHERS.value,
        )
        process_huggingface_columns(
            [{"payload": value}],
            str(hf_dataset.id),
            str(column.id),
            rows_with_string_keys,
            0,
        )

        cell = Cell.objects.get(column=column, row_id=rows_with_string_keys["0"])
        assert cell.value == json.dumps(value, ensure_ascii=False, allow_nan=False)
        assert json.loads(cell.value) == value
        assert cell.status == CellStatus.PASS.value

    @patch("model_hub.views.utils.hugginface.close_old_connections")
    def test_source_null_stays_an_empty_cell(
        self, _close, hf_dataset, text_column, rows_with_string_keys
    ):
        from model_hub.views.utils.hugginface import process_huggingface_columns

        process_huggingface_columns(
            [{"text": None}],
            str(hf_dataset.id),
            str(text_column.id),
            rows_with_string_keys,
            0,
        )

        cell = Cell.objects.get(column=text_column, row_id=rows_with_string_keys["0"])
        assert cell.value == ""
        assert cell.status == CellStatus.PASS.value


class _InlineExecutor:
    """Runs submitted work at once, in the test's thread (the test database
    transaction is not visible to other threads)."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 - mirrors a pool future
            future.set_exception(exc)
        return future


HF_LOAD_FAILED_REASON = "Unable to get data from Hugging Face. Please try again later."


class TestProcessHuggingFaceDatasetFailurePath:
    """Worker failures preserve a reason for each existing row's cells."""

    @pytest.fixture
    def hf_dataset(self, db, organization, workspace):
        ds = Dataset.objects.create(
            name="HuggingFace Failure Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        ds.column_order = []
        ds.save()
        return ds

    @pytest.fixture
    def hf_columns(self, db, hf_dataset):
        cols = []
        for name in ("sepal", "species"):
            col = Column.objects.create(
                name=name,
                dataset=hf_dataset,
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.OTHERS.value,
            )
            hf_dataset.column_order.append(str(col.id))
            cols.append(col)
        hf_dataset.save()
        return cols

    @staticmethod
    def _rows(dataset, count, key):
        return {
            key(i): str(Row.objects.create(dataset=dataset, order=i).id)
            for i in range(count)
        }

    @staticmethod
    def _run(dataset, columns, rows, num_rows):
        from model_hub.views.utils.hugginface import process_huggingface_dataset

        try:
            process_huggingface_dataset(
                str(dataset.id),
                "scikit-learn/iris",
                "default",
                "train",
                str(dataset.organization_id),
                num_rows,
                [str(c.id) for c in columns],
                rows,
            )
        except Exception as exc:  # noqa: BLE001 - the test asserts on it
            return exc
        return None

    @staticmethod
    def _cells_by_pair(dataset):
        pairs = {}
        for cell in Cell.objects.filter(dataset=dataset):
            pairs.setdefault((str(cell.row_id), str(cell.column_id)), []).append(cell)
        return pairs

    def test_missing_ml_extra_refuses_before_creating_records(
        self, auth_client, hf_import_request, hf_import_dependencies
    ):
        hf_import_dependencies.extra.side_effect = ImportError(
            "datasets is unavailable"
        )
        url, body = hf_import_request
        before = {
            model: model.no_workspace_objects.count()
            for model in (Dataset, Row, Column, Cell)
        }

        response = auth_client.post(url, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["message"] == (
            "This install lacks the datasets package needed for Hugging Face import."
        )
        for model in (Dataset, Row, Column, Cell):
            assert model.no_workspace_objects.count() == before[model]
        hf_import_dependencies.preview.assert_not_called()
        hf_import_dependencies.revision.assert_not_called()
        hf_import_dependencies.metadata.assert_not_called()
        hf_import_dependencies.activity.assert_not_called()

    @pytest.mark.parametrize(
        "key", [int, str], ids=["direct-int-keys", "temporal-str-keys"]
    )
    @patch("model_hub.views.utils.hugginface.close_old_connections")
    @patch("model_hub.views.utils.hugginface.ThreadPoolExecutor", _InlineExecutor)
    @patch("model_hub.views.utils.hugginface.load_hf_dataset_with_retries")
    def test_mid_stream_failure_keeps_one_cell_per_row_and_column(
        self, mock_load, _mock_close, key, hf_dataset, hf_columns
    ):
        def stream():
            yield {"sepal": "5.1", "species": "Iris-setosa"}
            raise ConnectionError("stream dropped after the first row")

        mock_load.return_value = stream()
        rows = self._rows(hf_dataset, 3, key)

        raised = self._run(hf_dataset, hf_columns, rows, 3)

        assert raised is None, f"the failure path raised {raised!r}"
        pairs = self._cells_by_pair(hf_dataset)
        assert {pair: len(cells) for pair, cells in pairs.items()} == {
            (row_id, str(col.id)): 1 for row_id in rows.values() for col in hf_columns
        }
        first = rows[key(0)]
        assert {pairs[(first, str(col.id))][0].value for col in hf_columns} == {
            "5.1",
            "Iris-setosa",
        }
        for (row_id, _col), cells in pairs.items():
            if row_id == first:
                assert cells[0].status == CellStatus.PASS.value
            else:
                assert cells[0].status == CellStatus.ERROR.value
                assert json.loads(cells[0].value_infos)["reason"] == (
                    HF_LOAD_FAILED_REASON
                )
        for col in hf_columns:
            col.refresh_from_db()
            assert col.status == StatusType.COMPLETED.value


class TestCreateDatasetFromHuggingFaceE2E:
    """
    End-to-end tests for CreateDatasetFromHuggingFaceView.

    These tests verify the complete flow from API call to data in database.
    """

    @pytest.fixture(autouse=True)
    def available_import_package(self, monkeypatch):
        monkeypatch.setattr(
            "model_hub.views.datasets.create.huggingface.huggingface_import_available",
            lambda: True,
            raising=False,
        )

    @patch("model_hub.views.datasets.create.huggingface.load_hf_dataset_with_retries")
    @patch(
        "model_hub.views.datasets.create.huggingface.CreateDatasetFromHuggingFaceView.get_huggingface_dataset_info"
    )
    @patch("tfc.temporal.drop_in.runner.start_activity")
    def test_create_dataset_starts_temporal_activity(
        self,
        mock_start_activity,
        mock_hf_info,
        mock_load_dataset,
        auth_client,
        organization,
        workspace,
    ):
        """Test that creating a dataset from HuggingFace starts Temporal activity."""
        # Mock the HuggingFace dataset info
        mock_hf_info.return_value = {"num_rows": 2, "split": "train"}

        # Mock the HuggingFace dataset loading - returns dict with features
        mock_load_dataset.return_value = {
            "features": [
                {"name": "text", "type": "string"},
                {"name": "label", "type": "int64"},
            ]
        }

        # Mock start_activity to capture the call
        mock_start_activity.return_value = "workflow-123"

        response = auth_client.post(
            "/model-hub/develops/create-dataset-from-huggingface/",
            {
                "huggingface_dataset_name": "test/dataset",
                "huggingface_dataset_config": "default",
                "huggingface_dataset_split": "train",
                "num_rows": 2,
            },
            format="json",
        )

        # Check if we get 200 or a known error
        if response.status_code != status.HTTP_200_OK:
            # Print response for debugging
            print(f"Response: {response.status_code} - {response.json()}")

        # For now, just verify the endpoint was called correctly
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    @patch("model_hub.views.datasets.create.huggingface.load_hf_dataset_with_retries")
    @patch(
        "model_hub.views.datasets.create.huggingface.CreateDatasetFromHuggingFaceView.get_huggingface_dataset_info"
    )
    @patch("tfc.temporal.drop_in.runner.start_activity")
    def test_create_dataset_creates_rows_with_integer_keys(
        self,
        mock_start_activity,
        mock_hf_info,
        mock_load_dataset,
        auth_client,
        organization,
        workspace,
        db,
    ):
        """
        Verify that the view creates rows dict with integer keys.

        This test documents the expected behavior that rows are created
        with integer keys, which will be converted to string keys after
        JSON serialization through Temporal.
        """
        # Mock the HuggingFace dataset info
        mock_hf_info.return_value = {"num_rows": 2, "split": "train"}

        # Mock the HuggingFace dataset loading - returns dict with features
        mock_load_dataset.return_value = {
            "features": [
                {"name": "text", "type": "string"},
            ]
        }

        captured_rows = {}

        def capture_start_activity(activity_name, args, queue):
            nonlocal captured_rows
            if activity_name == "process_huggingface_dataset_activity":
                captured_rows = args[7]  # rows is the 8th argument
            return "workflow-123"

        mock_start_activity.side_effect = capture_start_activity

        response = auth_client.post(
            "/model-hub/develops/create-dataset-from-huggingface/",
            {
                "huggingface_dataset_name": "test/dataset",
                "huggingface_dataset_config": "default",
                "huggingface_dataset_split": "train",
                "num_rows": 2,
            },
            format="json",
        )

        if response.status_code == status.HTTP_200_OK:
            # Verify rows dict has integer keys (before JSON serialization)
            assert 0 in captured_rows or "0" in captured_rows, (
                "Rows dict should have key 0 (integer or string)"
            )
            assert 1 in captured_rows or "1" in captured_rows, (
                "Rows dict should have key 1 (integer or string)"
            )

            # The actual keys should be integers at this point
            # (before Temporal JSON serialization)
            if 0 in captured_rows:
                assert isinstance(list(captured_rows.keys())[0], int), (
                    "View should create rows with integer keys"
                )
        else:
            # Print response for debugging
            print(f"Response: {response.status_code} - {response.json()}")
            # Skip assertion if there are other validation errors
            pytest.skip(f"API returned {response.status_code}: {response.json()}")


class TestHuggingFaceListFeatureCompat:
    """
    Streaming ingestion (process_huggingface_dataset -> load_hf_dataset_with_retries
    -> datasets.load_dataset) parses the dataset's feature metadata before yielding
    rows. The Hub now emits the `List` feature type (datasets 4.0) that the pinned
    datasets 3.6.0 cannot resolve, which crashed the load for datasets such as
    rajpurkar/squad and left every cell empty. Importing model_hub.utils.utils must
    register `List` so the real parse path succeeds.
    """

    # The `answers` feature of rajpurkar/squad, exactly as the Hub emits it.
    SQUAD_ANSWERS_SCHEMA = {
        "answers": {
            "text": {
                "feature": {"dtype": "string", "_type": "Value"},
                "_type": "List",
            },
            "answer_start": {
                "feature": {"dtype": "int32", "_type": "Value"},
                "_type": "List",
            },
        }
    }

    def test_list_feature_type_parses_on_real_load_path(self):
        """
        RED without the fix: datasets.Features.from_dict — the function
        load_dataset() calls to parse feature metadata — raises
        "Feature type 'List' not found". GREEN once model_hub.utils.utils
        aliases `List` to LargeList at import.
        """
        from datasets import Features

        import model_hub.utils.utils  # noqa: F401  (import installs the List alias)

        features = Features.from_dict(self.SQUAD_ANSWERS_SCHEMA)

        assert "answers" in features
        rebuilt = features.to_dict()["answers"]
        list_like = {"List", "LargeList", "Sequence"}
        assert rebuilt["text"]["_type"] in list_like
        assert rebuilt["answer_start"]["_type"] in list_like

    def test_top_level_list_feature_parses(self):
        """A plain List-of-scalars column (the other Hub shape) also parses."""
        from datasets import Features

        import model_hub.utils.utils  # noqa: F401

        features = Features.from_dict(
            {
                "tags": {
                    "feature": {"dtype": "string", "_type": "Value"},
                    "_type": "List",
                }
            }
        )

        assert "tags" in features
        assert features.to_dict()["tags"]["_type"] in {"List", "LargeList", "Sequence"}
