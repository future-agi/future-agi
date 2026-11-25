import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from redis.exceptions import RedisError
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    SourceChoices,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.utils.dataset_limit import DatasetLimitCheck, DatasetLimitOutcome
from tfc.constants.api_calls import APICallTypeChoices
from tfc.utils.error_codes import get_error_message
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace

DATASET_LIMIT_ALLOWED = DatasetLimitCheck(DatasetLimitOutcome.ALLOWED)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


def get_result_rows(response):
    result = get_result(response)
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    return result


@pytest.fixture
def other_workspace(db, organization, user):
    """Create a same-organization workspace outside the active request scope."""
    return Workspace.objects.create(
        name=f"Other Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


@pytest.fixture
def other_organization(db):
    return Organization.objects.create(
        name=f"Other Organization {uuid.uuid4().hex[:8]}"
    )


@pytest.fixture
def other_user(db, other_organization):
    return User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=other_organization,
    )


def create_observe_span_for_workspace(organization, workspace, suffix="other"):
    """Create an Observe project, trace, and root span in a specific workspace."""
    project = Project.no_workspace_objects.create(
        name=f"Observe Project {suffix} {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    trace = Trace.no_workspace_objects.create(
        project=project,
        name=f"Trace {suffix}",
        input={"prompt": f"Input {suffix}"},
        output={"response": f"Output {suffix}"},
    )
    span = ObservationSpan.no_workspace_objects.create(
        id=f"observe_{suffix}_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=f"Observe Span {suffix}",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=2),
        end_time=timezone.now() - timedelta(seconds=1),
        input={"messages": [{"role": "user", "content": f"Input {suffix}"}]},
        output={"choices": [{"message": {"content": f"Output {suffix}"}}]},
        model="gpt-4",
        status="OK",
    )
    return project, trace, span


def ch_only_span_row(project, trace_id, span_id, parent_span_id=""):
    now = timezone.now()
    return {
        "id": span_id,
        "project_id": str(project.id),
        "trace_id": str(trace_id),
        "parent_span_id": parent_span_id,
        "org_id": str(project.organization_id),
        "name": "CH-only Root Span",
        "observation_type": "llm",
        "operation_name": "",
        "start_time": now - timedelta(seconds=2),
        "end_time": now - timedelta(seconds=1),
        "input": {"messages": [{"role": "user", "content": "hello"}]},
        "output": {"choices": [{"message": {"content": "hi"}}]},
        "model": "gpt-4",
        "provider": "openai",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "latency_ms": 100,
        "cost": 0.001,
        "status": "OK",
        "status_message": "",
        "tags": [],
        "metadata": {},
        "span_events": [],
        "span_attributes": {},
        "resource_attributes": {},
        "created_at": now,
        "updated_at": now,
        "deleted": False,
    }


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
class TestDatasetRootCrudAPI:
    """Tests for generated /tracer/dataset/ root CRUD aliases."""

    def test_root_create_uses_request_scope_and_defaults_observe_source(
        self, auth_client, organization, workspace, user, other_organization, other_user
    ):
        response = auth_client.post(
            "/tracer/dataset/",
            {
                "name": "Root CRUD Observe Dataset",
                "organization": str(other_organization.id),
                "user": str(other_user.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = get_result(response)
        assert data["organization"] == str(organization.id)
        assert data["user"] == str(user.id)
        assert data["source"] == DatasetSourceChoices.OBSERVE.value

        dataset = Dataset.no_workspace_objects.get(id=data["id"])
        assert dataset.organization_id == organization.id
        assert dataset.workspace_id == workspace.id
        assert dataset.user_id == user.id
        assert dataset.source == DatasetSourceChoices.OBSERVE.value
        assert not Dataset.no_workspace_objects.filter(
            name="Root CRUD Observe Dataset",
            organization=other_organization,
        ).exists()

    def test_root_list_detail_update_and_delete_are_request_scoped(
        self,
        auth_client,
        dataset,
        organization,
        workspace,
        user,
        other_workspace,
        other_organization,
        other_user,
    ):
        hidden_dataset = Dataset.no_workspace_objects.create(
            id=uuid.uuid4(),
            name="Hidden Other Workspace Dataset",
            organization=organization,
            workspace=other_workspace,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            source=DatasetSourceChoices.OBSERVE.value,
            user=user,
        )

        list_response = auth_client.get("/tracer/dataset/?name=Dataset", format="json")
        assert list_response.status_code == status.HTTP_200_OK
        rows = get_result_rows(list_response)
        assert isinstance(rows, list)
        ids = {row["id"] for row in rows}
        assert str(dataset.id) in ids
        assert str(hidden_dataset.id) not in ids

        hidden_detail = auth_client.get(f"/tracer/dataset/{hidden_dataset.id}/")
        assert hidden_detail.status_code == status.HTTP_404_NOT_FOUND

        put_response = auth_client.put(
            f"/tracer/dataset/{dataset.id}/",
            {
                "name": "Root CRUD Replaced",
                "organization": str(other_organization.id),
                "user": str(other_user.id),
                "model_type": ModelTypes.GENERATIVE_LLM.value,
                "source": DatasetSourceChoices.OBSERVE.value,
            },
            format="json",
        )
        assert put_response.status_code == status.HTTP_200_OK

        patch_response = auth_client.patch(
            f"/tracer/dataset/{dataset.id}/",
            {
                "name": "Root CRUD Patched",
                "organization": str(other_organization.id),
                "user": str(other_user.id),
            },
            format="json",
        )
        assert patch_response.status_code == status.HTTP_200_OK

        dataset.refresh_from_db()
        assert dataset.name == "Root CRUD Patched"
        assert dataset.organization_id == organization.id
        assert dataset.workspace_id == workspace.id
        assert dataset.user_id == user.id

        hidden_patch = auth_client.patch(
            f"/tracer/dataset/{hidden_dataset.id}/",
            {"name": "Leaked Root Patch"},
            format="json",
        )
        assert hidden_patch.status_code == status.HTTP_404_NOT_FOUND

        delete_response = auth_client.delete(f"/tracer/dataset/{dataset.id}/")
        assert delete_response.status_code == status.HTTP_204_NO_CONTENT
        dataset.refresh_from_db()
        assert dataset.deleted is True
        assert dataset.deleted_at is not None

        hidden_dataset.refresh_from_db()
        assert hidden_dataset.name == "Hidden Other Workspace Dataset"
        assert hidden_dataset.deleted is False


@pytest.mark.integration
@pytest.mark.api
class TestAddToNewDatasetAPI:
    """Tests for POST /tracer/dataset/add_to_new_dataset/ endpoint."""

    def test_unauthenticated_request(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.post("/tracer/dataset/add_to_new_dataset/", {})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

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
        self, mock_check_allowed, mock_task, auth_client, observe_spans, ch_seed
    ):
        """Request without project derives it from selected spans."""
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

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
        workspace,
        observe_project,
        observe_spans,
        ch_seed,
    ):
        """Successfully create dataset with spanIds."""
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

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
        assert dataset.workspace_id == workspace.id

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_success_with_ch_only_span_ids_without_project(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        observe_project,
        ch_seed,
    ):
        """Span ids derive their project from ClickHouse, not PG spans."""
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED
        mock_task.delay.return_value = None
        trace_id = uuid.uuid4()
        span_id = f"ch_span_{uuid.uuid4().hex[:16]}"
        ch_seed([ch_only_span_row(observe_project, trace_id, span_id)])
        assert not ObservationSpan.no_workspace_objects.filter(id=span_id).exists()

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset From CH Spans",
                "span_ids": [span_id],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_task.delay.assert_called_once()
        assert mock_task.delay.call_args.args[0] == [span_id]

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
        ch_seed,
    ):
        """Successfully create dataset with traceIds."""
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

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
    def test_success_with_ch_only_trace_ids(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        observe_project,
        ch_seed,
    ):
        """Trace ids no longer need PG Trace/ObservationSpan rows to export."""
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED
        mock_task.delay.return_value = None
        trace_id = uuid.uuid4()
        span_id = f"ch_root_{uuid.uuid4().hex[:16]}"
        ch_seed([ch_only_span_row(observe_project, trace_id, span_id)])
        assert not Trace.no_workspace_objects.filter(id=trace_id).exists()

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": "New Dataset From CH Traces",
                "project": str(observe_project.id),
                "trace_ids": [str(trace_id)],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_task.delay.assert_called_once()
        assert mock_task.delay.call_args.args[0] == [span_id]

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_success_with_selectAll(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        observe_project,
        observe_spans,
        ch_seed,
    ):
        """Successfully create dataset with selectAll=True."""
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

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
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED

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

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_duplicate_dataset_name_in_same_org_other_workspace_is_allowed(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        organization,
        user,
        workspace,
        other_workspace,
        observe_project,
        observe_spans,
        ch_seed,
    ):
        """Same-org duplicate names outside the active workspace do not block create."""
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED
        mock_task.delay.return_value = None
        ch_seed(observe_spans)
        dataset_name = f"Cross Workspace Name {uuid.uuid4().hex[:8]}"
        Dataset.no_workspace_objects.create(
            id=uuid.uuid4(),
            name=dataset_name,
            organization=organization,
            workspace=other_workspace,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            source=DatasetSourceChoices.OBSERVE.value,
            user=user,
        )

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": dataset_name,
                "project": str(observe_project.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [
                    {"col_name": "input", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        created = Dataset.no_workspace_objects.get(id=result["dataset_id"])
        assert created.workspace_id == workspace.id

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_other_workspace_span_ids_do_not_create_dataset(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        organization,
        other_workspace,
        ch_seed,
    ):
        """Selected spans from another workspace are hidden before dataset creation."""
        mock_check_allowed.return_value = DATASET_LIMIT_ALLOWED
        mock_task.delay.return_value = None
        _, _, other_span = create_observe_span_for_workspace(
            organization, other_workspace, suffix="new_dataset_guard"
        )
        ch_seed(other_span)
        dataset_name = f"Hidden Span Dataset {uuid.uuid4().hex[:8]}"

        response = auth_client.post(
            "/tracer/dataset/add_to_new_dataset/",
            {
                "new_dataset_name": dataset_name,
                "span_ids": [other_span.id],
                "mapping_config": [
                    {"col_name": "input", "data_type": "text"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Dataset.no_workspace_objects.filter(
            name=dataset_name,
            organization=organization,
        ).exists()
        mock_task.delay.assert_not_called()

    @pytest.mark.requires_ee
    def test_dataset_creation_limit_reached(
        self, auth_client, organization, observe_project, observe_spans
    ):
        """A reached limit answers like every other dataset-create route.

        429 with the plan-limit message, plus the upgrade alert the frontend
        shows for a 429 (it drops the 429 itself). No usage row is written:
        this route never recorded DATASET_ADD usage, and the limit counts
        Dataset rows, not usage rows. It is a quota refusal, not a failure,
        so nothing is logged as an error.
        """
        from ee.usage.models.usage import APICallLog

        with (
            patch(
                "tracer.views.dataset.check_if_dataset_creation_is_allowed"
            ) as mock_check,
            patch("ee.usage.utils.usage_entries.call_websocket") as websocket,
            patch("tracer.views.dataset.logger") as logger,
        ):
            mock_check.return_value = DatasetLimitCheck(
                DatasetLimitOutcome.LIMIT_REACHED, limit=3
            )

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

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert response.json()["message"] == get_error_message(
            "DATASET_CREATE_LIMIT_REACHED"
        )
        websocket.assert_called_once()
        assert websocket.call_args.args[0] == str(organization.id)
        alert = websocket.call_args.kwargs["message"]
        assert alert["alert_title"] == get_error_message("RESOURCE_ALERT_TITLE").format(
            "Dataset"
        )
        assert "supports only 3 dataset" in alert["alert_description"]
        assert not APICallLog.objects.filter(
            organization=organization,
            api_call_type__name=APICallTypeChoices.DATASET_ADD.value,
        ).exists()
        assert not Dataset.no_workspace_objects.filter(
            name="Limited Dataset", organization=organization
        ).exists()
        logger.exception.assert_not_called()

    @pytest.mark.requires_ee
    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_reached_limit_still_refuses_when_the_alert_cannot_be_sent(
        self, mock_task, auth_client, organization, observe_project, observe_spans
    ):
        """A failed websocket push never turns the refusal into a creation."""
        with (
            patch(
                "tracer.views.dataset.check_if_dataset_creation_is_allowed",
                return_value=DatasetLimitCheck(
                    DatasetLimitOutcome.LIMIT_REACHED, limit=3
                ),
            ),
            patch(
                "ee.usage.utils.usage_entries.call_websocket",
                side_effect=ConnectionError("websocket relay down"),
            ) as websocket,
        ):
            response = auth_client.post(
                "/tracer/dataset/add_to_new_dataset/",
                {
                    "new_dataset_name": "Alert Down Dataset",
                    "project": str(observe_project.id),
                    "span_ids": [s.id for s in observe_spans],
                    "mapping_config": [
                        {"col_name": "input", "data_type": "text"},
                    ],
                },
                format="json",
            )

        websocket.assert_called_once()
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert response.json()["message"] == get_error_message(
            "DATASET_CREATE_LIMIT_REACHED"
        )
        assert not Dataset.no_workspace_objects.filter(
            name="Alert Down Dataset", organization=organization
        ).exists()
        mock_task.delay.assert_not_called()

    @pytest.mark.requires_ee
    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_denied_entitlement_answers_429_with_upgrade_alert(
        self, mock_task, auth_client, organization, observe_project, observe_spans
    ):
        """The real check, not a mocked outcome, drives the same refusal."""
        from ee.usage.schemas.events import CheckResult

        denied = CheckResult(allowed=False, error_code="ENTITLEMENT_LIMIT", limit=2)

        with (
            patch(
                "ee.usage.services.entitlements.Entitlements.can_create",
                return_value=denied,
            ),
            patch("ee.usage.utils.usage_entries.call_websocket") as websocket,
        ):
            response = auth_client.post(
                "/tracer/dataset/add_to_new_dataset/",
                {
                    "new_dataset_name": "Denied Dataset",
                    "project": str(observe_project.id),
                    "span_ids": [s.id for s in observe_spans],
                    "mapping_config": [
                        {"col_name": "input", "data_type": "text"},
                    ],
                },
                format="json",
            )

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert (
            "supports only 2 dataset"
            in (websocket.call_args.kwargs["message"]["alert_description"])
        )
        mock_task.delay.assert_not_called()

    @pytest.mark.parametrize(
        ("is_cloud", "expected_status"),
        [(True, status.HTTP_503_SERVICE_UNAVAILABLE), (False, status.HTTP_200_OK)],
    )
    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_entitlement_error_refuses_creation_on_cloud_only(
        self,
        mock_task,
        auth_client,
        organization,
        observe_project,
        observe_spans,
        ch_seed,
        is_cloud,
        expected_status,
    ):
        """An erroring dataset entitlement check refuses on cloud with a retry."""
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=is_cloud),
            patch(
                "ee.usage.services.entitlements.Entitlements.can_create",
                side_effect=RedisError("entitlement cache unavailable"),
            ),
        ):
            response = auth_client.post(
                "/tracer/dataset/add_to_new_dataset/",
                {
                    "new_dataset_name": "Unverified Dataset",
                    "project": str(observe_project.id),
                    "span_ids": [s.id for s in observe_spans],
                    "mapping_config": [
                        {"col_name": "input", "data_type": "text"},
                    ],
                },
                format="json",
            )

        assert response.status_code == expected_status
        if is_cloud:
            assert response.json()["code"] == "dataset_limit_check_failed"
            assert response.json()["message"] == get_error_message(
                "DATASET_LIMIT_CHECK_FAILED"
            )
        assert (
            Dataset.no_workspace_objects.filter(
                name="Unverified Dataset", organization=organization
            ).exists()
            is not is_cloud
        )


@pytest.mark.django_db
class TestCreateNewDatasetEntitlement:
    """create_new_dataset must act on the typed entitlement outcome."""

    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_denied_entitlement_blocks_creation(
        self, mock_check, organization, workspace, user
    ):
        from model_hub.views.utils.dataset_limit import DatasetLimitReached
        from tracer.views.dataset import create_new_dataset

        mock_check.return_value = DatasetLimitCheck(
            DatasetLimitOutcome.LIMIT_REACHED, limit=3
        )

        with pytest.raises(DatasetLimitReached):
            create_new_dataset("Blocked Dataset", organization, workspace, user.id)

        mock_check.assert_called_once_with(organization)
        assert not Dataset.no_workspace_objects.filter(
            name="Blocked Dataset", organization=organization
        ).exists()

    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_allowed_entitlement_creates_dataset(
        self, mock_check, organization, workspace, user
    ):
        from tracer.views.dataset import create_new_dataset

        mock_check.return_value = DATASET_LIMIT_ALLOWED

        dataset = create_new_dataset(
            "Allowed Dataset", organization, workspace, user.id
        )

        assert dataset.name == "Allowed Dataset"


@pytest.mark.integration
@pytest.mark.api
class TestAddToExistingDatasetAPI:
    """Tests for POST /tracer/dataset/add_to_existing_dataset/ endpoint."""

    def test_unauthenticated_request(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.post("/tracer/dataset/add_to_existing_dataset/", {})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

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
        ch_seed,
    ):
        """Successfully add to existing dataset with spanIds."""
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

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
    def test_success_with_ch_only_span_ids_without_project(
        self,
        mock_task,
        auth_client,
        dataset,
        dataset_columns,
        observe_project,
        ch_seed,
    ):
        """Existing dataset import resolves selected span ids from ClickHouse."""
        mock_task.delay.return_value = None
        trace_id = uuid.uuid4()
        span_id = f"ch_existing_span_{uuid.uuid4().hex[:16]}"
        ch_seed([ch_only_span_row(observe_project, trace_id, span_id)])
        assert not ObservationSpan.no_workspace_objects.filter(id=span_id).exists()

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "span_ids": [span_id],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_task.delay.assert_called_once()
        assert mock_task.delay.call_args.args[0] == [span_id]

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
        ch_seed,
    ):
        """Successfully add to existing dataset with traceIds."""
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

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
    def test_success_with_ch_only_trace_ids(
        self,
        mock_task,
        auth_client,
        dataset,
        dataset_columns,
        observe_project,
        ch_seed,
    ):
        """Existing dataset import resolves selected trace roots from ClickHouse."""
        mock_task.delay.return_value = None
        trace_id = uuid.uuid4()
        span_id = f"ch_existing_root_{uuid.uuid4().hex[:16]}"
        ch_seed([ch_only_span_row(observe_project, trace_id, span_id)])
        assert not Trace.no_workspace_objects.filter(id=trace_id).exists()

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "project": str(observe_project.id),
                "trace_ids": [str(trace_id)],
                "mapping_config": [
                    {"col_name": "input", "span_field": "input"},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_task.delay.assert_called_once()
        assert mock_task.delay.call_args.args[0] == [span_id]

    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_success_with_selectAll(
        self,
        mock_task,
        auth_client,
        dataset,
        dataset_columns,
        observe_project,
        observe_spans,
        ch_seed,
    ):
        """Successfully add to existing dataset with selectAll=True."""
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

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
        ch_seed,
    ):
        """Successfully add to existing dataset with new columns."""
        mock_task.delay.return_value = None
        ch_seed(observe_spans)

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

    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_dataset_from_same_org_other_workspace_is_hidden(
        self,
        mock_task,
        auth_client,
        organization,
        user,
        other_workspace,
        observe_spans,
    ):
        """Existing-dataset add cannot mutate a dataset from another workspace."""
        mock_task.delay.return_value = None
        other_dataset = Dataset.no_workspace_objects.create(
            id=uuid.uuid4(),
            name="Other Workspace Dataset",
            organization=organization,
            workspace=other_workspace,
            model_type=ModelTypes.GENERATIVE_LLM.value,
            source=DatasetSourceChoices.OBSERVE.value,
            user=user,
        )
        Column.objects.create(
            id=uuid.uuid4(),
            name="input",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
            dataset=other_dataset,
        )

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(other_dataset.id),
                "span_ids": [s.id for s in observe_spans],
                "mapping_config": [{"col_name": "input", "span_field": "input"}],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_task.delay.assert_not_called()

    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_other_workspace_span_ids_do_not_dispatch_to_existing_dataset(
        self,
        mock_task,
        auth_client,
        organization,
        other_workspace,
        dataset,
        dataset_columns,
        ch_seed,
    ):
        """Existing-dataset add ignores same-org span ids outside active workspace."""
        mock_task.delay.return_value = None
        _, _, other_span = create_observe_span_for_workspace(
            organization, other_workspace, suffix="existing_dataset_guard"
        )
        ch_seed(other_span)

        response = auth_client.post(
            "/tracer/dataset/add_to_existing_dataset/",
            {
                "dataset_id": str(dataset.id),
                "span_ids": [other_span.id],
                "mapping_config": [{"col_name": "input", "span_field": "input"}],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestProcessSpansChunkTask:
    def test_processes_export_cells_from_clickhouse_seed(
        self,
        dataset,
        dataset_columns,
        observe_spans,
        ch_seed,
    ):
        from model_hub.models.develop_dataset import Cell, Row
        from tracer.tasks.dataset import process_spans_chunk_task

        span = observe_spans[0]
        ch_seed([span])
        column_span_mapping_data = [
            {
                "column_id": str(dataset_columns[0].id),
                "column_name": "input",
                "span_field": "input",
            },
            {
                "column_id": str(dataset_columns[1].id),
                "column_name": "output",
                "span_field": "output",
            },
        ]

        result = process_spans_chunk_task.run_sync(
            [span.id],
            str(dataset.id),
            column_span_mapping_data,
            str(span.project_id),
            str(span.project.organization_id),
        )

        assert result == {"rows_created": 1, "cells_created": 2}
        row = Row.objects.get(dataset=dataset)
        values = {
            str(cell.column_id): cell.value
            for cell in Cell.objects.filter(dataset=dataset, row=row)
        }
        assert "Input 0" in values[str(dataset_columns[0].id)]
        assert "Output 0" in values[str(dataset_columns[1].id)]


# ---------------------------------------------------------------------------
# Select-all carries the grid's filters, so the dataset receives exactly the
# rows the grid counted (not every root span in the project). The grid always
# sends its ``created_at`` window, so the payloads below include it.
# ---------------------------------------------------------------------------


def _filter(column_id, filter_type, filter_op, filter_value):
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def _seed_ch_traces(ch_seed, project, count, *, child_on_first=False, age=None):
    """Seed ``count`` CH-only traces with one root span each.

    Returns ``[(trace_id, root_span_id), ...]``. ``child_on_first`` adds a
    child span under the first root (fan-out must still give one row);
    ``age`` backdates every seeded span by that timedelta.
    """
    rows = []
    traces = []
    for i in range(count):
        trace_id = str(uuid.uuid4())
        root_id = f"ch_root_{i}_{uuid.uuid4().hex[:12]}"
        rows.append(ch_only_span_row(project, trace_id, root_id))
        traces.append((trace_id, root_id))
    if child_on_first and traces:
        trace_id, root_id = traces[0]
        rows.append(
            ch_only_span_row(
                project,
                trace_id,
                f"ch_child_{uuid.uuid4().hex[:12]}",
                parent_span_id=root_id,
            )
        )
    if age is not None:
        for row in rows:
            for key in ("start_time", "end_time", "created_at", "updated_at"):
                row[key] = row[key] - age
    ch_seed(rows)
    return traces


def _dispatched_span_ids(mock_task):
    return [
        span_id for call in mock_task.delay.call_args_list for span_id in call.args[0]
    ]


def _grid_iso(value):
    """Format like the grid's ``Date.toISOString()``: ms precision, ``Z``."""
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _recent_window_filter():
    """The ``created_at`` window the grid sends with every list request."""
    now = timezone.now()
    return _filter(
        "created_at",
        "datetime",
        "between",
        [
            _grid_iso(now - timedelta(days=7)),
            _grid_iso(now + timedelta(minutes=5)),
        ],
    )


@pytest.mark.integration
@pytest.mark.api
class TestFilteredSelectAllAddsExactSelection:
    """Select-all with the grid's filters adds exactly the selected rows."""

    NEW_URL = "/tracer/dataset/add_to_new_dataset/"
    EXISTING_URL = "/tracer/dataset/add_to_existing_dataset/"
    MAPPING = [{"col_name": "input", "span_field": "input", "data_type": "text"}]

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_filter_narrows_select_all_to_matching_trace(
        self, mock_check_allowed, mock_task, auth_client, observe_project, ch_seed
    ):
        """Reproduction: the project holds 3 traces, the filter matches 1."""
        mock_check_allowed.return_value = (True, {})
        mock_task.delay.return_value = None
        traces = _seed_ch_traces(ch_seed, observe_project, 3)
        target_trace_id, target_root_id = traces[1]

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Filtered Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "trace_ids": [],
                "filters": [
                    _recent_window_filter(),
                    _filter("trace_id", "text", "equals", target_trace_id),
                ],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _dispatched_span_ids(mock_task) == [target_root_id]

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_time_window_excludes_rows_outside_it(
        self, mock_check_allowed, mock_task, auth_client, observe_project, ch_seed
    ):
        """The grid's time window bounds select-all; older traces stay out."""
        mock_check_allowed.return_value = (True, {})
        mock_task.delay.return_value = None
        recent = _seed_ch_traces(ch_seed, observe_project, 2)
        _seed_ch_traces(ch_seed, observe_project, 3, age=timedelta(days=40))

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Windowed Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "trace_ids": [],
                "filters": [_recent_window_filter()],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        dispatched = _dispatched_span_ids(mock_task)
        assert len(dispatched) == 2
        assert set(dispatched) == {root_id for _trace_id, root_id in recent}

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_deselected_traces_are_subtracted_and_one_row_per_trace(
        self, mock_check_allowed, mock_task, auth_client, observe_project, ch_seed
    ):
        mock_check_allowed.return_value = (True, {})
        mock_task.delay.return_value = None
        traces = _seed_ch_traces(ch_seed, observe_project, 4, child_on_first=True)
        deselected_trace_id, _deselected_root = traces[3]

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Select All Minus One",
                "project": str(observe_project.id),
                "select_all": True,
                "trace_ids": [deselected_trace_id],
                "filters": [],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        dispatched = _dispatched_span_ids(mock_task)
        # 3 traces selected -> exactly 3 rows: the child span of the first
        # trace does not add a row, and the deselected trace is left out.
        assert len(dispatched) == 3
        assert set(dispatched) == {root_id for _trace_id, root_id in traces[:3]}

    @pytest.mark.parametrize(
        "with_window", [False, True], ids=["no_filters", "created_at_window"]
    )
    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_selection_spanning_several_resolver_pages(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        observe_project,
        ch_seed,
        monkeypatch,
        with_window,
    ):
        """Resolver continuations are followed until the selection is complete.

        The ``created_at_window`` case is the shape the grid always sends: page
        2 onwards resumes under the time window, and older traces stay out.
        """
        import tracer.views.dataset as dataset_views

        monkeypatch.setattr(dataset_views, "_FILTERED_SELECT_ALL_PAGE_SIZE", 2)
        mock_check_allowed.return_value = (True, {})
        mock_task.delay.return_value = None
        traces = _seed_ch_traces(ch_seed, observe_project, 5)
        if with_window:
            _seed_ch_traces(ch_seed, observe_project, 3, age=timedelta(days=40))

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Paged Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "trace_ids": [],
                "filters": [_recent_window_filter()] if with_window else [],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        dispatched = _dispatched_span_ids(mock_task)
        assert len(dispatched) == 5
        assert set(dispatched) == {root_id for _trace_id, root_id in traces}

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_selection_over_page_ceiling_fails_closed(
        self,
        mock_check_allowed,
        mock_task,
        auth_client,
        observe_project,
        ch_seed,
        monkeypatch,
    ):
        import tracer.views.dataset as dataset_views

        monkeypatch.setattr(dataset_views, "_FILTERED_SELECT_ALL_PAGE_SIZE", 1)
        monkeypatch.setattr(dataset_views, "_FILTERED_SELECT_ALL_MAX_PAGES", 2)
        mock_check_allowed.return_value = (True, {})
        _seed_ch_traces(ch_seed, observe_project, 3)

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Too Large Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "trace_ids": [],
                "filters": [],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "too large" in str(response.json())
        mock_task.delay.assert_not_called()
        assert not Dataset.no_workspace_objects.filter(
            name="Too Large Select All"
        ).exists()

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_short_pages_at_ceiling_are_not_reported_as_too_large(
        self, mock_check_allowed, mock_task, auth_client, observe_project, monkeypatch
    ):
        """Checkpoint pages (fewer ids than the page size) that use up the page
        ceiling mean the read did not finish, not that the selection is big."""
        import tracer.views.dataset as dataset_views
        from model_hub.services.bulk_selection import ResolveResult

        monkeypatch.setattr(dataset_views, "_FILTERED_SELECT_ALL_PAGE_SIZE", 5)
        monkeypatch.setattr(dataset_views, "_FILTERED_SELECT_ALL_MAX_PAGES", 2)
        mock_check_allowed.return_value = (True, {})

        def checkpoint_page(**_kwargs):
            # A fresh continuation each call: progress, but no ids.
            return ResolveResult(
                ids=[], total_matching=0, truncated=True, continuation=object()
            )

        with patch(
            "model_hub.services.bulk_selection.resolve_filtered_trace_ids",
            side_effect=checkpoint_page,
        ):
            response = auth_client.post(
                self.NEW_URL,
                {
                    "new_dataset_name": "Checkpointed Select All",
                    "project": str(observe_project.id),
                    "select_all": True,
                    "trace_ids": [],
                    "filters": [],
                    "mapping_config": self.MAPPING,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = str(response.json())
        assert "could not be read completely" in body
        assert "too large" not in body
        mock_task.delay.assert_not_called()
        assert not Dataset.no_workspace_objects.filter(
            name="Checkpointed Select All"
        ).exists()

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_every_resolver_page_shares_one_request_deadline(
        self, mock_check_allowed, mock_task, auth_client, observe_project, ch_seed
    ):
        from model_hub.services.bulk_selection import ResolveResult
        from tracer.services.clickhouse.read_budget import ReadDeadline

        mock_check_allowed.return_value = (True, {})
        mock_task.delay.return_value = None
        traces = _seed_ch_traces(ch_seed, observe_project, 2)
        deadlines = []
        next_page = object()

        def two_pages(**kwargs):
            deadlines.append(kwargs.get("deadline"))
            if kwargs.get("cursor") is None:
                return ResolveResult(
                    ids=[traces[0][0]],
                    total_matching=1,
                    truncated=True,
                    continuation=next_page,
                )
            return ResolveResult(ids=[traces[1][0]], total_matching=2, truncated=False)

        with patch(
            "model_hub.services.bulk_selection.resolve_filtered_trace_ids",
            side_effect=two_pages,
        ):
            response = auth_client.post(
                self.NEW_URL,
                {
                    "new_dataset_name": "Deadline Select All",
                    "project": str(observe_project.id),
                    "select_all": True,
                    "trace_ids": [],
                    "filters": [],
                    "mapping_config": self.MAPPING,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert len(deadlines) == 2
        assert isinstance(deadlines[0], ReadDeadline)
        assert deadlines[1] is deadlines[0]
        assert set(_dispatched_span_ids(mock_task)) == {
            root_id for _trace_id, root_id in traces
        }

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_request_deadline_exceeded_fails_closed_without_creating_dataset(
        self, mock_check_allowed, mock_task, auth_client, observe_project
    ):
        from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

        mock_check_allowed.return_value = (True, {})
        with patch(
            "model_hub.services.bulk_selection.resolve_filtered_trace_ids",
            side_effect=ReadDeadlineExceeded("read deadline exceeded"),
        ):
            response = auth_client.post(
                self.NEW_URL,
                {
                    "new_dataset_name": "Slow Select All",
                    "project": str(observe_project.id),
                    "select_all": True,
                    "trace_ids": [],
                    "filters": [],
                    "mapping_config": self.MAPPING,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_task.delay.assert_not_called()
        assert not Dataset.no_workspace_objects.filter(name="Slow Select All").exists()

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_select_all_matching_nothing_creates_no_dataset(
        self, mock_check_allowed, mock_task, auth_client, observe_project, ch_seed
    ):
        """A filtered select-all that resolves to 0 rows leaves no empty dataset."""
        mock_check_allowed.return_value = (True, {})
        _seed_ch_traces(ch_seed, observe_project, 2)

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Empty Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "trace_ids": [],
                "filters": [
                    _recent_window_filter(),
                    _filter("trace_id", "text", "equals", str(uuid.uuid4())),
                ],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "No rows match the selection" in str(response.json())
        mock_task.delay.assert_not_called()
        assert not Dataset.no_workspace_objects.filter(name="Empty Select All").exists()

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_incomplete_resolve_fails_closed_without_creating_dataset(
        self, mock_check_allowed, mock_task, auth_client, observe_project
    ):
        from model_hub.services.bulk_selection import BulkSelectionReadIncomplete

        mock_check_allowed.return_value = (True, {})
        with patch(
            "model_hub.services.bulk_selection.resolve_filtered_trace_ids",
            side_effect=BulkSelectionReadIncomplete("scan_budget_exceeded"),
        ):
            response = auth_client.post(
                self.NEW_URL,
                {
                    "new_dataset_name": "Unreadable Select All",
                    "project": str(observe_project.id),
                    "select_all": True,
                    "trace_ids": [],
                    "filters": [],
                    "mapping_config": self.MAPPING,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_task.delay.assert_not_called()
        assert not Dataset.no_workspace_objects.filter(
            name="Unreadable Select All"
        ).exists()

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_spans_tab_select_all_adds_exact_span_set(
        self, mock_check_allowed, mock_task, auth_client, observe_project, ch_seed
    ):
        mock_check_allowed.return_value = (True, {})
        mock_task.delay.return_value = None
        trace_id = str(uuid.uuid4())
        root_id = f"ch_root_{uuid.uuid4().hex[:12]}"
        child_ids = [f"ch_child_{i}_{uuid.uuid4().hex[:12]}" for i in range(3)]
        ch_seed(
            [ch_only_span_row(observe_project, trace_id, root_id)]
            + [
                ch_only_span_row(
                    observe_project, trace_id, child_id, parent_span_id=root_id
                )
                for child_id in child_ids
            ]
        )

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Span Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "span_ids": [child_ids[0]],
                "filters": [_recent_window_filter()],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        dispatched = _dispatched_span_ids(mock_task)
        assert len(dispatched) == 3
        assert set(dispatched) == {root_id, child_ids[1], child_ids[2]}

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_spans_tab_select_all_honours_time_window(
        self, mock_check_allowed, mock_task, auth_client, observe_project, ch_seed
    ):
        """The span resolver applies the grid's window: old spans stay out."""
        mock_check_allowed.return_value = (True, {})
        mock_task.delay.return_value = None
        trace_id = str(uuid.uuid4())
        root_id = f"ch_root_{uuid.uuid4().hex[:12]}"
        child_ids = [f"ch_child_{i}_{uuid.uuid4().hex[:12]}" for i in range(2)]
        ch_seed(
            [ch_only_span_row(observe_project, trace_id, root_id)]
            + [
                ch_only_span_row(
                    observe_project, trace_id, child_id, parent_span_id=root_id
                )
                for child_id in child_ids
            ]
        )
        old_trace_id = str(uuid.uuid4())
        old_root_id = f"ch_old_root_{uuid.uuid4().hex[:12]}"
        old_rows = [
            ch_only_span_row(observe_project, old_trace_id, old_root_id),
            ch_only_span_row(
                observe_project,
                old_trace_id,
                f"ch_old_child_{uuid.uuid4().hex[:12]}",
                parent_span_id=old_root_id,
            ),
        ]
        for row in old_rows:
            for key in ("start_time", "end_time", "created_at", "updated_at"):
                row[key] = row[key] - timedelta(days=40)
        ch_seed(old_rows)

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Windowed Span Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "span_ids": [child_ids[0]],
                "filters": [_recent_window_filter()],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        dispatched = _dispatched_span_ids(mock_task)
        assert len(dispatched) == 2
        assert set(dispatched) == {root_id, child_ids[1]}

    @patch("tracer.views.dataset.process_spans_chunk_task")
    def test_existing_dataset_select_all_honours_filters(
        self,
        mock_task,
        auth_client,
        dataset,
        dataset_columns,
        observe_project,
        ch_seed,
    ):
        mock_task.delay.return_value = None
        traces = _seed_ch_traces(ch_seed, observe_project, 3)
        target_trace_id, target_root_id = traces[0]

        response = auth_client.post(
            self.EXISTING_URL,
            {
                "dataset_id": str(dataset.id),
                "project": str(observe_project.id),
                "select_all": True,
                "trace_ids": [],
                "filters": [
                    _recent_window_filter(),
                    _filter("trace_id", "text", "equals", target_trace_id),
                ],
                "mapping_config": [{"col_name": "input", "span_field": "input"}],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _dispatched_span_ids(mock_task) == [target_root_id]

    @patch("tracer.views.dataset.process_spans_chunk_task")
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_select_all_without_filters_keeps_legacy_project_scope(
        self, mock_check_allowed, mock_task, auth_client, observe_project, ch_seed
    ):
        """Old clients that send no ``filters`` keep the whole-project path."""
        mock_check_allowed.return_value = (True, {})
        mock_task.delay.return_value = None
        traces = _seed_ch_traces(ch_seed, observe_project, 3)

        response = auth_client.post(
            self.NEW_URL,
            {
                "new_dataset_name": "Legacy Select All",
                "project": str(observe_project.id),
                "select_all": True,
                "trace_ids": [],
                "mapping_config": self.MAPPING,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert set(_dispatched_span_ids(mock_task)) == {
            root_id for _trace_id, root_id in traces
        }


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestFilteredSelectAllInsertedRows:
    @patch("tracer.views.dataset.check_if_dataset_creation_is_allowed")
    def test_new_dataset_gets_exactly_the_selected_row_count(
        self, mock_check_allowed, auth_client, observe_project, ch_seed
    ):
        """Seed 6 recent traces and 3 older ones, select all minus 2 under the
        grid's 7-day window: exactly 4 Rows land (the whole-project path
        would insert 7)."""
        from model_hub.models.develop_dataset import Row
        from tracer.tasks.dataset import process_spans_chunk_task

        mock_check_allowed.return_value = (True, {})
        traces = _seed_ch_traces(ch_seed, observe_project, 6, child_on_first=True)
        _seed_ch_traces(ch_seed, observe_project, 3, age=timedelta(days=40))
        deselected = [traces[4][0], traces[5][0]]

        with patch("tracer.views.dataset.process_spans_chunk_task") as mock_task:
            mock_task.delay.return_value = None
            response = auth_client.post(
                "/tracer/dataset/add_to_new_dataset/",
                {
                    "new_dataset_name": "Exact Row Count",
                    "project": str(observe_project.id),
                    "select_all": True,
                    "trace_ids": deselected,
                    "filters": [_recent_window_filter()],
                    "mapping_config": [
                        {
                            "col_name": "input",
                            "span_field": "input",
                            "data_type": "text",
                        }
                    ],
                },
                format="json",
            )
            assert response.status_code == status.HTTP_200_OK, response.json()
            dispatched_calls = [call.args for call in mock_task.delay.call_args_list]

        # Run the dispatched chunks the way the worker receives them
        # (serialized, so the project and org ids arrive as strings).
        for span_ids, dataset_id, mapping, project_id, org_id in dispatched_calls:
            process_spans_chunk_task.run_sync(
                span_ids, dataset_id, mapping, str(project_id), str(org_id)
            )

        dataset_id = get_result(response)["dataset_id"]
        assert Row.objects.filter(dataset_id=dataset_id, deleted=False).count() == 4
