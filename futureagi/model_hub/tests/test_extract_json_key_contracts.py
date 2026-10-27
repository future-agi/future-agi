"""
Contract tests for GetJsonColumnSchemaView.

Pins the { [field]: { keys: string[] } } response shape that
ExtractJsonKey.jsx depends on to populate its column dropdown.
"""
import json

import pytest

from model_hub.models.choices import CellStatus, DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.choices import DatasetSourceChoices


@pytest.mark.django_db
class TestGetJsonColumnSchemaContract:
    URL = "/model-hub/dataset/{dataset_id}/json-schema/"

    @pytest.fixture
    def dataset_with_columns(self, organization, workspace):
        dataset = Dataset.objects.create(
            name="Contract Test Dataset",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )

        json_col = Column.objects.create(
            name="structured_output",
            dataset=dataset,
            data_type=DataTypeChoices.JSON.value,
            source=SourceChoices.OTHERS.value,
            metadata={
                "json_schema": {"keys": ["id", "name", "score"]},
            },
        )

        # Simulate an api_call column whose cells contain JSON objects.
        api_col = Column.objects.create(
            name="api_response",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.API_CALL.value,
        )

        row = Row.objects.create(dataset=dataset, order=0)
        Cell.objects.create(
            dataset=dataset,
            column=api_col,
            row=row,
            value=json.dumps({"status": True, "confidence": 0.95}),
            status=CellStatus.PASS.value,
        )

        dataset.column_order = [str(json_col.id), str(api_col.id)]
        dataset.save()

        return dataset, json_col, api_col

    def test_response_shape(self, auth_client, dataset_with_columns):
        dataset, json_col, api_col = dataset_with_columns
        url = self.URL.format(dataset_id=dataset.id)

        response = auth_client.get(url)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert isinstance(result, dict)

    def test_json_column_keys_present(self, auth_client, dataset_with_columns):
        dataset, json_col, _ = dataset_with_columns
        url = self.URL.format(dataset_id=dataset.id)

        response = auth_client.get(url)
        result = response.json()["result"]

        field = str(json_col.id)
        assert field in result
        assert isinstance(result[field]["keys"], list)
        assert set(result[field]["keys"]) == {"id", "name", "score"}

    def test_api_call_column_with_json_cells_has_keys(
        self, auth_client, dataset_with_columns
    ):
        """api_call columns with JSON-object cells must appear in the schema
        so ExtractJsonKey.jsx includes them in the dropdown."""
        dataset, _, api_col = dataset_with_columns
        url = self.URL.format(dataset_id=dataset.id)

        response = auth_client.get(url)
        result = response.json()["result"]

        field = str(api_col.id)
        assert field in result, (
            "api_call column with JSON cells must appear in the schema result"
        )
        keys = result[field]["keys"]
        assert isinstance(keys, list)
        assert set(keys) >= {"status", "confidence"}

    def test_each_entry_has_keys_list(self, auth_client, dataset_with_columns):
        """Every entry in the result must have a 'keys' list — pins the shape
        the frontend hook destructures as jsonSchemas?.[field]?.keys."""
        dataset, _, _ = dataset_with_columns
        url = self.URL.format(dataset_id=dataset.id)

        response = auth_client.get(url)
        result = response.json()["result"]

        for field, entry in result.items():
            assert "keys" in entry, f"Entry for {field} missing 'keys'"
            assert isinstance(entry["keys"], list), f"'keys' for {field} must be a list"
