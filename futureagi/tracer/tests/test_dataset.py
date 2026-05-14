import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    SourceChoices,
)
from model_hub.models.develop_dataset import Column, Dataset


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


@pytest.fixture
def dataset(db, organization, user, workspace):
    """Create a test dataset."""
    return Dataset.objects.create(
        id=uuid.uuid4(),
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        source=DatasetSourceChoices.OBSERVE.value,
        user=user,
    )


@pytest.fixture
def dataset_columns(db, dataset):
    """Create test columns for a dataset."""
    columns = []
    for name in ["input", "output"]:
        col = Column.objects.create(
            id=uuid.uuid4(),
            name=name,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
            dataset=dataset,
        )
        columns.append(col)
    dataset.column_order = [str(c.id) for c in columns]
    dataset.column_config = {str(c.id): {"is_visible": True} for c in columns}
    dataset.save()
    return columns


@pytest.fixture
def observe_spans(db, observe_project, session_trace):
    """Create observation spans for observe project."""
    from datetime import timedelta

    from django.utils import timezone

    from tracer.models.observation_span import ObservationSpan

    spans = []
    for i in range(3):
        span_id = f"observe_span_{i}_{uuid.uuid4().hex[:8]}"
        span = ObservationSpan.objects.create(
            id=span_id,
            project=observe_project,
            trace=session_trace,
            name=f"Observe Span {i}",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=10 - i),
            end_time=timezone.now() - timedelta(seconds=9 - i),
            input={"messages": [{"role": "user", "content": f"Input {i}"}]},
            output={"choices": [{"message": {"content": f"Output {i}"}}]},
            model="gpt-4",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost=0.001,
            latency_ms=100,
            status="OK",
        )
        spans.append(span)
    return spans


@pytest.mark.integration
@pytest.mark.api
class TestAddToNewDatasetAPI:
    """Tests for POST /tracer/dataset/add_to_new_dataset/ endpoint."""

    def test_unauthenticated_request(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.post("/tracer/dataset/add_to_new_dataset/", {})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_missing_required_fields(self, auth_client):
        """Request without required fields should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_mappingConfig(self, auth_client, observe_project, observe_spans):
        """Request without mappingConfig should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset",
                "project": str(observe_project.id),
                "span_ids": [s.id for s in observe_spans],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_empty_mappingConfig(self, auth_client, observe_project, observe_spans):
        """Request with empty mappingConfig should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset",
                "project": str(observe_project.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_missing_project_derives_from_spans(
        self, mock_check_allowed, mock_task, auth_client, observe_spans
    ):
        """Request without project derives it from selected spans."""
        mock_check_allowed.return_value = True
        mock_task.delay.return_value = None

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset",
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [{"col_name": "input", "data_type": "text"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_no_spans_or_traces_provided(self, auth_client, observe_project):
        """Request without spanIds or traceIds should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset",
                "project": str(observe_project.id),
                "mapping_config": [{"col_name": "input", "data_type": "text"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_success_with_spanIds(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        observe_project,
        observe_spans,
    ):
        """Successfully create dataset with spanIds."""
        mock_check_allowed.return_value = True
        mock_task.delay.return_value = None

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset From Spans",
                "project": str(observe_project.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input", "data_type": "text"},
                    {"col_name": "output", "span_field": "output", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert "dataset_id" in result
        assert result["dataset_name"] == "New Dataset From Spans"
        assert result["status"] == "processing"

        # Verify dataset was created
        dataset = Dataset.objects.get(id=result["dataset_id"])
        assert dataset.name == "New Dataset From Spans"
        assert dataset.source == DatasetSourceChoices.OBSERVE.value

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_success_with_traceIds(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        observe_project,
        session_trace,
        observe_spans,
    ):
        """Successfully create dataset with traceIds."""
        mock_check_allowed.return_value = True
        mock_task.delay.return_value = None

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset From Traces",
                "project": str(observe_project.id),
                "trace_ids": [str(session_trace.id)],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert "dataset_id" in result
        assert result["status"] == "processing"

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_success_with_selectAll(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        observe_project,
        observe_spans,
    ):
        """Successfully create dataset with selectAll=True."""
        mock_check_allowed.return_value = True
        mock_task.delay.return_value = None

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "span_ids": [],  # Exclude none
                "mapping_config": [
                    {"col_name": "input", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["status"] == "processing"

    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_duplicate_dataset_name(
        self, mock_check_allowed, auth_client, observe_project, observe_spans, dataset
    ):
        """Creating dataset with existing name should return 400."""
        mock_check_allowed.return_value = True

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": dataset.name,  # Duplicate name
                "project": str(observe_project.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [
                    {"col_name": "input", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_dataset_creation_limit_reached(
        self, auth_client, observe_project, observe_spans
    ):
        """Should return 400 when dataset creation limit is reached."""
        with patch(
            "tracer.views.dataset.check_if_dataset_creation_is_allowed"
        ) as mock_check:
            mock_check.return_value = False

            response = auth_client.post(
                "/tracer/dataset/add_to_new_dataset/",
                {
                    "new_dataset_name": "Limited Dataset",
                    "project": str(observe_project.id),
                    "span_ids": [s.id for s in observe_spans],
                    "mapping_config": [
                        {"col_name": "input", "data_type": "text"},
                    ],
                },
                format="json",
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestAddToExistingDatasetAPI:
    """Tests for POST /tracer/dataset/add_to_existing_dataset/ endpoint."""

    def test_unauthenticated_request(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.post("/tracer/dataset/add_to_existing_dataset/", {})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_missing_required_fields(self, auth_client):
        """Request without required fields should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_datasetId(self, auth_client, observe_spans):
        """Request without datasetId should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [{"col_name": "input", "span_field": "input"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_dataset_not_found(self, auth_client, observe_spans):
        """Request with non-existent datasetId should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(uuid.uuid4()),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [{"col_name": "input", "span_field": "input"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_no_spans_or_traces_provided(self, auth_client, dataset, dataset_columns):
        """Request without spanIds or traceIds should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "mapping_config": [{"col_name": "input", "span_field": "input"}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_column_not_found(self, auth_client, dataset, observe_spans):
        """Request with non-existent column name should return 400."""
        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [
                    {"col_name": "nonexistent_column", "span_field": "input"}
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_success_with_spanIds(
        self,
        mock_task,
        auth_client,
        dataset,
        dataset_columns,
        observe_project,
        observe_spans,
    ):
        """Successfully add to existing dataset with spanIds."""
        mock_task.delay.return_value = None

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "project": str(observe_project.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input"},
                    {"col_name": "output", "span_field": "output"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["dataset_id"] == str(dataset.id)
        assert result["status"] == "processing"

    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_success_with_traceIds(
        self,
        mock_task,
        auth_client,
        dataset,
        dataset_columns,
        observe_project,
        session_trace,
        observe_spans,
    ):
        """Successfully add to existing dataset with traceIds."""
        mock_task.delay.return_value = None

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "project": str(observe_project.id),
                "trace_ids": [str(session_trace.id)],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["status"] == "processing"

    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_success_with_selectAll(
        self,
        mock_task,
        auth_client,
        dataset,
        dataset_columns,
        observe_project,
        observe_spans,
    ):
        """Successfully add to existing dataset with selectAll=True."""
        mock_task.delay.return_value = None

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "project": str(observe_project.id),
                "select_all": True,
                "span_ids": [],  # Exclude none
                "mapping_config": [
                    {"col_name": "input", "span_field": "input"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["status"] == "processing"

    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_success_with_newMappingConfig(
        self,
        mock_task,
        auth_client,
        dataset,
        dataset_columns,
        observe_project,
        observe_spans,
    ):
        """Successfully add to existing dataset with new columns."""
        mock_task.delay.return_value = None

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "project": str(observe_project.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input"},
                ],
                "new_mapping_config": [
                    {"col_name": "model", "span_field": "model", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["status"] == "processing"

        # Verify new column was created
        new_column = Column.objects.filter(dataset=dataset, name="model").first()
        assert new_column is not None

    def test_deleted_dataset(self, auth_client, dataset, observe_spans):
        """Request with deleted dataset should return 400."""
        dataset.deleted = True
        dataset.save()

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_dataset_from_different_organization(
        self, auth_client, observe_spans, workspace
    ):
        """Request with dataset from different organization should return 400."""
        other_org = Organization.objects.create(name="Other Organization")
        other_dataset = Dataset.objects.create(
            id=uuid.uuid4(),
            name="Other Dataset",
            organization=other_org,
            workspace=workspace,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            source=DatasetSourceChoices.OBSERVE.value,
        )

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(other_dataset.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
