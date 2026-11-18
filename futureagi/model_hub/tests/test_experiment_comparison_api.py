"""
Test cases for Experiment Comparison API endpoints.

Tests cover:
- ExperimentDatasetComparisonView - POST /experiments/<id>/compare-experiments/
- ExperimentComparisonDetailsView - GET /experiments/<id>/comparisons/

Run with: pytest model_hub/tests/test_experiment_comparison_api.py -v
"""

import json
import uuid

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import (
    ExperimentComparison,
    ExperimentDatasetTable,
    ExperimentsTable,
)

# ==================== Fixtures ====================


@pytest.fixture
def dataset(db, organization, workspace):
    ds = Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def output_column(db, dataset):
    col = Column.objects.create(
        name="Output Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.fixture
def row(db, dataset):
    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def cell_with_metadata(db, dataset, output_column, row):
    """Create a cell with usage metadata (completion_tokens, total_tokens, response_time)."""
    value_infos = json.dumps(
        {
            "metadata": {
                "usage": {
                    "completion_tokens": 100,
                    "total_tokens": 150,
                },
                "response_time": 1.5,
            }
        }
    )
    return Cell.objects.create(
        dataset=dataset,
        column=output_column,
        row=row,
        value="Test output",
        value_infos=value_infos,
    )


@pytest.fixture
def experiment(db, dataset, output_column):
    return ExperimentsTable.objects.create(
        name="Test Experiment",
        dataset=dataset,
        column=output_column,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def experiment_dataset(db, experiment):
    exp_dataset = ExperimentDatasetTable.objects.create(
        name="Experiment Dataset 1",
        status=StatusType.COMPLETED.value,
    )
    experiment.experiments_datasets.add(exp_dataset)
    return exp_dataset


@pytest.fixture
def experiment_dataset_column(db, experiment_dataset, dataset):
    """Create a column associated with an experiment dataset, with a cell containing metadata."""
    col = Column.objects.create(
        name="exp-col",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    experiment_dataset.columns.add(col)

    row = Row.objects.create(dataset=dataset, order=1)
    value_infos = json.dumps(
        {
            "metadata": {
                "usage": {
                    "completion_tokens": 200,
                    "total_tokens": 300,
                },
                "response_time": 2.0,
            }
        }
    )
    Cell.objects.create(
        dataset=dataset,
        column=col,
        row=row,
        value="Experiment output",
        value_infos=value_infos,
    )
    return col


@pytest.fixture
def comparison_weights():
    """Default weights payload for compare-experiments endpoint."""
    return {
        "weights": {
            "response_time": 1,
            "completion_tokens": 1,
            "total_tokens": 1,
        },
    }


@pytest.fixture
def comparison(db, experiment, experiment_dataset):
    """Create an ExperimentComparison record."""
    return ExperimentComparison.objects.create(
        experiment=experiment,
        experiment_dataset=experiment_dataset,
        response_time_weight=1.0,
        scores_weight={"response_time": 1, "completion_tokens": 1, "total_tokens": 1},
        total_tokens_weight=1.0,
        completion_tokens_weight=1.0,
        avg_completion_tokens=100.0,
        avg_total_tokens=150.0,
        avg_response_time=1.5,
        avg_score=None,
        normalized_completion_tokens=5.0,
        normalized_total_tokens=5.0,
        normalized_response_time=5.0,
        normalized_score=None,
        overall_rating=5.0,
        rank=1,
    )


# ==================== ExperimentComparisonDetailsView (GET) Tests ====================


@pytest.mark.django_db
class TestExperimentComparisonDetailsView:
    """Tests for GET /experiments/<id>/comparisons/"""

    def test_get_comparisons_success(self, auth_client, experiment, comparison):
        """Test successfully retrieving comparisons."""
        response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["experiment_id"] == str(experiment.id)
        assert data["total_comparisons"] == 1
        assert len(data["comparisons"]) == 1

    def test_get_comparisons_returns_weights(self, auth_client, experiment, comparison):
        """Test that comparison response includes weight config."""
        response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )

        data = response.json()["result"]
        comp = data["comparisons"][0]
        assert "weights" in comp
        assert comp["weights"]["response_time"] == 1.0
        assert comp["weights"]["completion_tokens"] == 1.0
        assert comp["weights"]["total_tokens"] == 1.0

    def test_get_comparisons_returns_metrics(self, auth_client, experiment, comparison):
        """Test that comparison response includes raw and normalized metrics."""
        response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )

        data = response.json()["result"]
        comp = data["comparisons"][0]
        assert "metrics" in comp
        assert comp["metrics"]["raw"]["avg_completion_tokens"] == 100.0
        assert comp["metrics"]["raw"]["avg_total_tokens"] == 150.0
        assert comp["metrics"]["raw"]["avg_response_time"] == 1.5
        assert comp["metrics"]["normalized"]["completion_tokens"] == 5.0

    def test_get_comparisons_empty(self, auth_client, experiment):
        """Test getting comparisons when none exist."""
        response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["total_comparisons"] == 0
        assert data["comparisons"] == []

    def test_get_comparisons_ordered_by_rank(
        self, auth_client, experiment, experiment_dataset
    ):
        """Test that comparisons are returned ordered by rank."""
        # Create a second experiment dataset
        exp_dataset_2 = ExperimentDatasetTable.objects.create(
            name="Experiment Dataset 2",
            status=StatusType.COMPLETED.value,
        )
        experiment.experiments_datasets.add(exp_dataset_2)

        # Create comparisons with different ranks (rank 2 first, rank 1 second)
        ExperimentComparison.objects.create(
            experiment=experiment,
            experiment_dataset=experiment_dataset,
            response_time_weight=1.0,
            scores_weight={},
            total_tokens_weight=1.0,
            completion_tokens_weight=1.0,
            avg_completion_tokens=200.0,
            avg_total_tokens=300.0,
            avg_response_time=3.0,
            normalized_completion_tokens=3.0,
            normalized_total_tokens=3.0,
            normalized_response_time=3.0,
            overall_rating=3.0,
            rank=2,
        )
        ExperimentComparison.objects.create(
            experiment=experiment,
            experiment_dataset=exp_dataset_2,
            response_time_weight=1.0,
            scores_weight={},
            total_tokens_weight=1.0,
            completion_tokens_weight=1.0,
            avg_completion_tokens=100.0,
            avg_total_tokens=150.0,
            avg_response_time=1.0,
            normalized_completion_tokens=8.0,
            normalized_total_tokens=8.0,
            normalized_response_time=8.0,
            overall_rating=8.0,
            rank=1,
        )

        response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )

        data = response.json()["result"]
        assert data["total_comparisons"] == 2
        assert data["comparisons"][0]["rank"] == 1
        assert data["comparisons"][1]["rank"] == 2

    def test_get_comparisons_returns_latest_per_dataset(
        self, auth_client, experiment, experiment_dataset
    ):
        """Test that only the latest comparison per dataset is returned when
        multiple comparisons exist (e.g. config was changed multiple times)."""
        # Create an older comparison with different weights
        ExperimentComparison.objects.create(
            experiment=experiment,
            experiment_dataset=experiment_dataset,
            response_time_weight=5.0,
            scores_weight={
                "response_time": 5,
                "completion_tokens": 5,
                "total_tokens": 5,
            },
            total_tokens_weight=5.0,
            completion_tokens_weight=5.0,
            avg_completion_tokens=50.0,
            avg_total_tokens=75.0,
            avg_response_time=0.5,
            normalized_completion_tokens=2.0,
            normalized_total_tokens=2.0,
            normalized_response_time=2.0,
            overall_rating=2.0,
            rank=1,
        )

        # Create a newer comparison with different weights (simulating config change)
        ExperimentComparison.objects.create(
            experiment=experiment,
            experiment_dataset=experiment_dataset,
            response_time_weight=1.0,
            scores_weight={
                "response_time": 1,
                "completion_tokens": 1,
                "total_tokens": 1,
            },
            total_tokens_weight=1.0,
            completion_tokens_weight=1.0,
            avg_completion_tokens=100.0,
            avg_total_tokens=150.0,
            avg_response_time=1.5,
            normalized_completion_tokens=5.0,
            normalized_total_tokens=5.0,
            normalized_response_time=5.0,
            overall_rating=5.0,
            rank=1,
        )

        response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )

        data = response.json()["result"]
        # Should only return the latest comparison per dataset, not both
        assert data["total_comparisons"] == 1
        comp = data["comparisons"][0]
        assert comp["overall_rating"] == 5.0
        assert comp["weights"]["response_time"] == 1.0

    def test_get_comparisons_unauthenticated(self, experiment):
        """Test that unauthenticated requests are rejected."""
        client = APIClient()
        response = client.get(f"/model-hub/experiments/{experiment.id}/comparisons/")

        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_get_comparisons_rank_suffix(self, auth_client, experiment, comparison):
        """Test that rank suffix is correctly computed."""
        response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )

        data = response.json()["result"]
        comp = data["comparisons"][0]
        assert comp["rank"] == 1
        assert comp["rank_suffix"] == "st"


# ==================== ExperimentDatasetComparisonView (POST) Tests ====================


@pytest.mark.django_db
class TestExperimentDatasetComparisonView:
    """Tests for POST /experiments/<id>/compare-experiments/"""

    def test_compare_experiments_success(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        experiment_dataset_column,
        cell_with_metadata,
        comparison_weights,
    ):
        """Test successfully comparing experiment datasets."""
        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            comparison_weights,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["experiment_id"] == str(experiment.id)
        assert data["total_datasets"] > 0
        assert "weights_applied" in data
        assert "dataset_comparisons" in data

    def test_compare_experiments_saves_comparison(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        experiment_dataset_column,
        cell_with_metadata,
        comparison_weights,
    ):
        """Test that POST creates ExperimentComparison records in the database."""
        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            comparison_weights,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        comparisons = ExperimentComparison.objects.filter(
            experiment=experiment, deleted=False
        )
        assert comparisons.count() > 0

    def test_compare_experiments_weights_stored(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        experiment_dataset_column,
        cell_with_metadata,
        comparison_weights,
    ):
        """Test that weights from the request are stored in comparison records."""
        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            comparison_weights,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        comp = ExperimentComparison.objects.filter(
            experiment=experiment, deleted=False
        ).first()
        assert comp is not None
        assert comp.response_time_weight == 1
        assert comp.completion_tokens_weight == 1
        assert comp.total_tokens_weight == 1

    def test_compare_experiments_returns_rankings(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        experiment_dataset_column,
        cell_with_metadata,
        comparison_weights,
    ):
        """Test that response includes rank information for each dataset."""
        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            comparison_weights,
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        for comp in data["dataset_comparisons"]:
            assert "rank" in comp
            assert "rank_suffix" in comp
            assert comp["rank"] >= 1

    def test_compare_experiments_not_found(self, auth_client, comparison_weights):
        """Test comparing with non-existent experiment returns error."""
        fake_id = uuid.uuid4()
        response = auth_client.post(
            f"/model-hub/experiments/{fake_id}/compare-experiments/",
            comparison_weights,
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_compare_experiments_with_different_weights(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        experiment_dataset_column,
        cell_with_metadata,
    ):
        """Test that changing weights produces different results."""
        # First comparison with equal weights
        weights_1 = {
            "response_time": 1,
            "completion_tokens": 1,
            "total_tokens": 1,
        }
        response_1 = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            {"weights": weights_1},
            format="json",
        )
        assert response_1.status_code == status.HTTP_200_OK

        # Second comparison with different weights
        weights_2 = {
            "response_time": 10,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        response_2 = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            {"weights": weights_2},
            format="json",
        )
        assert response_2.status_code == status.HTTP_200_OK

        data_1 = response_1.json()["result"]
        data_2 = response_2.json()["result"]
        assert data_1["weights_applied"] != data_2["weights_applied"]

    def test_compare_experiments_no_datasets(
        self,
        auth_client,
        dataset,
        output_column,
        cell_with_metadata,
        comparison_weights,
    ):
        """Test comparing experiment with no experiment datasets (only base column)."""
        experiment = ExperimentsTable.objects.create(
            name="Empty Experiment",
            dataset=dataset,
            column=output_column,
            status=StatusType.COMPLETED.value,
        )

        response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            comparison_weights,
            format="json",
        )

        # Should still work with just the base column
        assert response.status_code == status.HTTP_200_OK


# ==================== Integration Tests ====================


@pytest.mark.django_db
class TestComparisonConfigPersistence:
    """Integration tests verifying that comparison config is properly persisted
    and retrievable via the GET endpoint after being set via POST."""

    def test_post_then_get_returns_consistent_data(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        experiment_dataset_column,
        cell_with_metadata,
    ):
        """Test that GET returns data consistent with what POST computed."""
        weights = {
            "response_time": 2,
            "completion_tokens": 3,
            "total_tokens": 1,
        }

        # POST to create comparisons
        post_response = auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            {"weights": weights},
            format="json",
        )
        assert post_response.status_code == status.HTTP_200_OK

        # GET to retrieve comparisons
        get_response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )
        assert get_response.status_code == status.HTTP_200_OK

        get_data = get_response.json()["result"]
        assert get_data["total_comparisons"] > 0

        # Verify weights match what we posted
        for comp in get_data["comparisons"]:
            assert comp["weights"]["response_time"] == 2
            assert comp["weights"]["completion_tokens"] == 3
            assert comp["weights"]["total_tokens"] == 1

    def test_config_update_reflected_in_get(
        self,
        auth_client,
        experiment,
        experiment_dataset,
        experiment_dataset_column,
        cell_with_metadata,
    ):
        """Test that updating config via POST is reflected in subsequent GET.
        This is the core bug scenario: changing weights should update the stored config.
        """
        # First POST with initial weights
        initial_weights = {
            "response_time": 1,
            "completion_tokens": 1,
            "total_tokens": 1,
        }
        auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            {"weights": initial_weights},
            format="json",
        )

        # Second POST with updated weights
        updated_weights = {
            "response_time": 5,
            "completion_tokens": 3,
            "total_tokens": 2,
        }
        auth_client.post(
            f"/model-hub/experiments/{experiment.id}/compare-experiments/",
            {"weights": updated_weights},
            format="json",
        )

        # GET should return the latest config
        get_response = auth_client.get(
            f"/model-hub/experiments/{experiment.id}/comparisons/"
        )
        get_data = get_response.json()["result"]

        # Each dataset should appear only once (latest comparison)
        dataset_ids = [c["experiment_dataset_id"] for c in get_data["comparisons"]]
        assert len(dataset_ids) == len(set(dataset_ids)), (
            "Duplicate dataset entries found — GET should return only the latest "
            "comparison per dataset"
        )
