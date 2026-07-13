"""
Test cases for Experiment V2 Feedback API endpoints.

Run with: pytest model_hub/tests/test_experiment_v2_feedback.py -v
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    FeedbackSourceChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, Feedback, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable

# ==================== Fixtures ====================


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Organization")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="test@example.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    settings.CURRENT_WORKSPACE = workspace
    settings.ORGANIZATION = user.organization
    return client


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
def snapshot_dataset(db, organization, workspace):
    ds = Dataset.objects.create(
        name="Snapshot Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def experiment(db, dataset, snapshot_dataset):
    return ExperimentsTable.objects.create(
        name="Test Experiment",
        dataset=dataset,
        snapshot_dataset=snapshot_dataset,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def output_column(db, snapshot_dataset):
    return Column.objects.create(
        name="Output Column",
        dataset=snapshot_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def experiment_dataset(db, experiment, output_column):
    edt = ExperimentDatasetTable.objects.create(
        name="Experiment Dataset",
        status=StatusType.COMPLETED.value,
        experiment=experiment,
    )
    edt.columns.add(output_column)
    return edt


@pytest.fixture
def eval_template(db, organization):
    return EvalTemplate.objects.create(
        name="test-eval-template",
        description="A test evaluation template",
        organization=organization,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "test_eval_type"},
    )


@pytest.fixture
def eval_template_choices(db, organization):
    return EvalTemplate.objects.create(
        name="test-eval-template-choices",
        description="A choices evaluation template",
        organization=organization,
        owner=OwnerChoices.USER.value,
        config={"output": "choices", "eval_type_id": "test_eval_type"},
        choices=["Good", "Bad", "Neutral"],
    )


@pytest.fixture
def eval_template_choice_scores(db, organization):
    return EvalTemplate.objects.create(
        name="test-eval-template-choice-scores",
        description="A scoring template with a choice→score map",
        organization=organization,
        owner=OwnerChoices.USER.value,
        config={"output": "score", "eval_type_id": "test_eval_type"},
        choice_scores={"Yes": 1.0, "Maybe": 0.5, "No": 0.0},
    )


@pytest.fixture
def user_eval_metric_choice_scores(
    db, organization, workspace, dataset, eval_template_choice_scores
):
    return UserEvalMetric.objects.create(
        name="Test Eval Metric Choice Scores",
        organization=organization,
        workspace=workspace,
        template=eval_template_choice_scores,
        dataset=dataset,
        config={"mapping": {}},
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def user_eval_metric(db, organization, workspace, dataset, eval_template):
    return UserEvalMetric.objects.create(
        name="Test Eval Metric",
        organization=organization,
        workspace=workspace,
        template=eval_template,
        dataset=dataset,
        config={"mapping": {}},
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def user_eval_metric_choices(
    db, organization, workspace, dataset, eval_template_choices
):
    return UserEvalMetric.objects.create(
        name="Test Eval Metric Choices",
        organization=organization,
        workspace=workspace,
        template=eval_template_choices,
        dataset=dataset,
        config={
            "mapping": {},
            "config": {"choices": ["A", "B", "C"], "multi_choice": True},
        },
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def row(db, snapshot_dataset):
    return Row.objects.create(dataset=snapshot_dataset, order=0)


@pytest.fixture
def input_column(db, snapshot_dataset):
    return Column.objects.create(
        name="Input Column",
        dataset=snapshot_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def input_cell(db, snapshot_dataset, input_column, row):
    return Cell.objects.create(
        dataset=snapshot_dataset,
        column=input_column,
        row=row,
        value="test input value",
        status=CellStatus.PASS.value,
    )


@pytest.fixture
def eval_column_per_edt(
    db, snapshot_dataset, experiment_dataset, user_eval_metric, output_column
):
    """Per-EDT eval column with source_id = {edt_id}-{col_id}-sourceid-{metric_id}"""
    source_id = (
        f"{experiment_dataset.id}-{output_column.id}-sourceid-{user_eval_metric.id}"
    )
    col = Column.objects.create(
        name="Per-EDT Eval Column",
        dataset=snapshot_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=source_id,
        status=StatusType.COMPLETED.value,
    )
    experiment_dataset.columns.add(col)
    return col


@pytest.fixture
def eval_column_base(db, snapshot_dataset, user_eval_metric):
    """Base eval column with source_id = str(metric_id)"""
    return Column.objects.create(
        name="Base Eval Column",
        dataset=snapshot_dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(user_eval_metric.id),
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def eval_cell_per_edt(db, snapshot_dataset, eval_column_per_edt, row):
    return Cell.objects.create(
        dataset=snapshot_dataset,
        column=eval_column_per_edt,
        row=row,
        value="Passed",
        status=CellStatus.PASS.value,
    )


@pytest.fixture
def eval_cell_base(db, snapshot_dataset, eval_column_base, row):
    return Cell.objects.create(
        dataset=snapshot_dataset,
        column=eval_column_base,
        row=row,
        value="Passed",
        status=CellStatus.PASS.value,
    )


@pytest.fixture
def feedback_per_edt(
    db, organization, user, eval_column_per_edt, user_eval_metric, row
):
    return Feedback.objects.create(
        source=FeedbackSourceChoices.EXPERIMENT.value,
        source_id=str(eval_column_per_edt.id),
        user_eval_metric=user_eval_metric,
        value="Failed",
        explanation="The model hallucinated",
        user=user,
        row_id=str(row.id),
        organization=organization,
    )


@pytest.fixture
def feedback_base(db, organization, user, eval_column_base, user_eval_metric, row):
    return Feedback.objects.create(
        source=FeedbackSourceChoices.EXPERIMENT.value,
        source_id=str(eval_column_base.id),
        user_eval_metric=user_eval_metric,
        value="Failed",
        explanation="Incorrect evaluation",
        user=user,
        row_id=str(row.id),
        organization=organization,
    )


@pytest.fixture
def experiment_with_evals(experiment, user_eval_metric):
    experiment.user_eval_template_ids.add(user_eval_metric)
    return experiment


# ==================== Helper ====================

BASE_URL = "/model-hub/experiments/v2"


def url(experiment_id, path=""):
    return f"{BASE_URL}/{experiment_id}/feedback/{path}"


# ==================== GetTemplate Tests ====================


@pytest.mark.django_db
class TestExperimentFeedbackGetTemplateV2:
    def test_get_template_success_pass_fail(
        self, auth_client, experiment_with_evals, user_eval_metric
    ):
        response = auth_client.get(
            url(experiment_with_evals.id, "get-template/"),
            {"user_eval_metric_id": str(user_eval_metric.id)},
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        data = response.json()["result"]
        assert data["output_type"] == "Pass/Fail"
        assert data["choices"] == ["Passed", "Failed"]
        assert data["eval_name"] == "test-eval-template"
        assert data["user_eval_name"] == "Test Eval Metric"

    def test_get_template_success_choices(
        self, auth_client, experiment, user_eval_metric_choices
    ):
        experiment.user_eval_template_ids.add(user_eval_metric_choices)
        response = auth_client.get(
            url(experiment.id, "get-template/"),
            {"user_eval_metric_id": str(user_eval_metric_choices.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["output_type"] == "choices"
        assert data["choices"] == ["A", "B", "C"]
        assert data["multi_choice"] is True

    def test_get_template_metric_multi_choice_override_wins_over_template(
        self,
        auth_client,
        experiment,
        eval_template_choices,
        dataset,
        organization,
        workspace,
    ):
        # Metric overrides multi_choice but does not override choices; the
        # returned multi_choice must reflect the metric, not the template.
        metric = UserEvalMetric.objects.create(
            name="Tone-like Metric",
            organization=organization,
            workspace=workspace,
            template=eval_template_choices,
            dataset=dataset,
            config={"config": {"multi_choice": True}},
            status=StatusType.COMPLETED.value,
        )
        experiment.user_eval_template_ids.add(metric)

        response = auth_client.get(
            url(experiment.id, "get-template/"),
            {"user_eval_metric_id": str(metric.id)},
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        data = response.json()["result"]
        assert data["multi_choice"] is True
        assert data["choices"] == eval_template_choices.choices

    def test_get_template_returns_choice_scores_when_defined(
        self, auth_client, experiment, user_eval_metric_choice_scores
    ):
        experiment.user_eval_template_ids.add(user_eval_metric_choice_scores)
        response = auth_client.get(
            url(experiment.id, "get-template/"),
            {"user_eval_metric_id": str(user_eval_metric_choice_scores.id)},
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        data = response.json()["result"]
        assert data["choice_scores"] == {"Yes": 1.0, "Maybe": 0.5, "No": 0.0}

    def test_get_template_returns_null_choice_scores_when_absent(
        self, auth_client, experiment_with_evals, user_eval_metric
    ):
        response = auth_client.get(
            url(experiment_with_evals.id, "get-template/"),
            {"user_eval_metric_id": str(user_eval_metric.id)},
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        data = response.json()["result"]
        assert data["choice_scores"] is None

    def test_get_template_missing_metric_id(self, auth_client, experiment):
        response = auth_client.get(url(experiment.id, "get-template/"))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_template_invalid_metric_id(self, auth_client, experiment):
        response = auth_client.get(
            url(experiment.id, "get-template/"),
            {"user_eval_metric_id": str(uuid.uuid4())},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_template_rejects_metric_not_attached_to_experiment(
        self, auth_client, experiment, user_eval_metric
    ):
        response = auth_client.get(
            url(experiment.id, "get-template/"),
            {"user_eval_metric_id": str(user_eval_metric.id)},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_template_nonexistent_experiment(self, auth_client):
        response = auth_client.get(
            url(uuid.uuid4(), "get-template/"),
            {"user_eval_metric_id": str(uuid.uuid4())},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_template_cross_org_experiment(self, auth_client, snapshot_dataset):
        other_org = Organization.objects.create(name="Other Org")
        other_ds = Dataset.objects.create(
            name="Other Dataset",
            organization=other_org,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_exp = ExperimentsTable.objects.create(
            name="Other Experiment",
            dataset=other_ds,
            snapshot_dataset=snapshot_dataset,
            status=StatusType.COMPLETED.value,
        )
        response = auth_client.get(
            url(other_exp.id, "get-template/"),
            {"user_eval_metric_id": str(uuid.uuid4())},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_template_rejects_other_org_metric(
        self, auth_client, experiment, snapshot_dataset
    ):
        other_org = Organization.objects.create(name="Other Org Metric")
        other_template = EvalTemplate.objects.create(
            name="other-org-eval-template",
            description="A different org template",
            organization=other_org,
            owner=OwnerChoices.USER.value,
            config={"output": "Pass/Fail", "eval_type_id": "test_eval_type"},
        )
        other_metric = UserEvalMetric.objects.create(
            name="Other Org Metric",
            organization=other_org,
            template=other_template,
            dataset=snapshot_dataset,
            config={"mapping": {}},
            status=StatusType.COMPLETED.value,
        )

        response = auth_client.get(
            url(experiment.id, "get-template/"),
            {"user_eval_metric_id": str(other_metric.id)},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ==================== Create Tests ====================


@pytest.mark.django_db
class TestExperimentFeedbackCreateV2:
    def test_create_feedback_success(
        self,
        auth_client,
        experiment_with_evals,
        user_eval_metric,
        eval_column_per_edt,
        row,
    ):
        response = auth_client.post(
            url(experiment_with_evals.id),
            {
                "source": FeedbackSourceChoices.EXPERIMENT.value,
                "source_id": str(eval_column_per_edt.id),
                "user_eval_metric": str(user_eval_metric.id),
                "value": "Failed",
                "explanation": "Wrong answer",
                "row_id": str(row.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert "id" in data

        # Verify feedback was created
        feedback = Feedback.objects.get(id=data["id"])
        assert feedback.source == FeedbackSourceChoices.EXPERIMENT.value
        assert feedback.value == "Failed"

    def test_create_feedback_source_is_experiment(
        self,
        auth_client,
        experiment_with_evals,
        user_eval_metric,
        eval_column_per_edt,
        row,
    ):
        response = auth_client.post(
            url(experiment_with_evals.id),
            {
                "source": FeedbackSourceChoices.EXPERIMENT.value,
                "source_id": str(eval_column_per_edt.id),
                "user_eval_metric": str(user_eval_metric.id),
                "value": "Passed",
                "row_id": str(row.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        feedback = Feedback.objects.get(id=response.json()["result"]["id"])
        assert feedback.source == "experiment"

    def test_create_feedback_invalid_data(self, auth_client, experiment):
        response = auth_client.post(
            url(experiment.id),
            {"source": "invalid_source"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_feedback_nonexistent_experiment(self, auth_client):
        response = auth_client.post(
            url(uuid.uuid4()),
            {"source": "experiment", "value": "test"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_feedback_cross_org_experiment(self, auth_client, snapshot_dataset):
        other_org = Organization.objects.create(name="Other Org 2")
        other_ds = Dataset.objects.create(
            name="Other Dataset 2",
            organization=other_org,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_exp = ExperimentsTable.objects.create(
            name="Other Experiment 2",
            dataset=other_ds,
            snapshot_dataset=snapshot_dataset,
            status=StatusType.COMPLETED.value,
        )
        response = auth_client.post(
            url(other_exp.id),
            {"source": "experiment", "value": "test"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_feedback_rejects_source_column_outside_snapshot(
        self,
        auth_client,
        experiment_with_evals,
        dataset,
        user_eval_metric,
        row,
    ):
        outside_column = Column.objects.create(
            name="Outside Column",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            status=StatusType.COMPLETED.value,
        )

        response = auth_client.post(
            url(experiment_with_evals.id),
            {
                "source": FeedbackSourceChoices.EXPERIMENT.value,
                "source_id": str(outside_column.id),
                "user_eval_metric": str(user_eval_metric.id),
                "value": "Failed",
                "explanation": "Wrong snapshot",
                "row_id": str(row.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Feedback.objects.filter(explanation="Wrong snapshot").exists()

    def test_create_feedback_rejects_metric_not_attached_to_experiment(
        self, auth_client, experiment, user_eval_metric, eval_column_per_edt, row
    ):
        response = auth_client.post(
            url(experiment.id),
            {
                "source": FeedbackSourceChoices.EXPERIMENT.value,
                "source_id": str(eval_column_per_edt.id),
                "user_eval_metric": str(user_eval_metric.id),
                "value": "Failed",
                "explanation": "Detached metric",
                "row_id": str(row.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Feedback.objects.filter(explanation="Detached metric").exists()

    def test_create_feedback_rejects_non_eval_source_column_in_snapshot(
        self,
        auth_client,
        experiment_with_evals,
        user_eval_metric,
        input_column,
        row,
    ):
        response = auth_client.post(
            url(experiment_with_evals.id),
            {
                "source": FeedbackSourceChoices.EXPERIMENT.value,
                "source_id": str(input_column.id),
                "user_eval_metric": str(user_eval_metric.id),
                "value": "Failed",
                "explanation": "Input column feedback",
                "row_id": str(row.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Feedback.objects.filter(explanation="Input column feedback").exists()


# ==================== Details Tests ====================


@pytest.mark.django_db
class TestExperimentFeedbackDetailsV2:
    def test_get_details_with_metric_and_row(
        self,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        row,
    ):
        response = auth_client.get(
            url(experiment_with_evals.id, "get-feedback-details/"),
            {
                "user_eval_metric_id": str(user_eval_metric.id),
                "row_id": str(row.id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["total_count"] == 1
        assert data["feedback"][0]["value"] == "Failed"
        assert data["feedback"][0]["comment"] == "The model hallucinated"

    def test_get_details_metric_only(
        self, auth_client, experiment_with_evals, feedback_per_edt, user_eval_metric
    ):
        response = auth_client.get(
            url(experiment_with_evals.id, "get-feedback-details/"),
            {"user_eval_metric_id": str(user_eval_metric.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["total_count"] >= 1

    def test_get_details_empty_result(self, auth_client, experiment):
        response = auth_client.get(
            url(experiment.id, "get-feedback-details/"),
            {
                "user_eval_metric_id": str(uuid.uuid4()),
                "row_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["total_count"] == 0
        assert data["feedback"] == []

    def test_get_details_nonexistent_experiment(self, auth_client):
        response = auth_client.get(
            url(uuid.uuid4(), "get-feedback-details/"),
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_details_cross_org_experiment(self, auth_client, snapshot_dataset):
        other_org = Organization.objects.create(name="Other Org 3")
        other_ds = Dataset.objects.create(
            name="Other Dataset 3",
            organization=other_org,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_exp = ExperimentsTable.objects.create(
            name="Other Experiment 3",
            dataset=other_ds,
            snapshot_dataset=snapshot_dataset,
            status=StatusType.COMPLETED.value,
        )
        response = auth_client.get(
            url(other_exp.id, "get-feedback-details/"),
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_details_excludes_feedback_from_other_snapshot(
        self, auth_client, experiment, organization, user, user_eval_metric
    ):
        other_snapshot = Dataset.objects.create(
            name="Other Snapshot",
            organization=organization,
            source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
        )
        other_row = Row.objects.create(dataset=other_snapshot, order=0)
        other_column = Column.objects.create(
            name="Other Snapshot Eval Column",
            dataset=other_snapshot,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            status=StatusType.COMPLETED.value,
        )
        Feedback.objects.create(
            source=FeedbackSourceChoices.EXPERIMENT.value,
            source_id=str(other_column.id),
            user_eval_metric=user_eval_metric,
            value="Failed",
            explanation="Other snapshot feedback",
            user=user,
            row_id=str(other_row.id),
            organization=organization,
        )

        response = auth_client.get(
            url(experiment.id, "get-feedback-details/"),
            {"user_eval_metric_id": str(user_eval_metric.id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["total_count"] == 0
        assert data["feedback"] == []


# ==================== Submit Feedback Tests ====================


@pytest.mark.django_db
class TestExperimentFeedbackSubmitV2:
    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    def test_submit_retune_success(
        self,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        eval_cell_per_edt,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed = MagicMock()
        mock_embed_cls.return_value = mock_embed

        response = auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "retune",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["message"] == "Metric queued for retuning"
        assert data["action_type"] == "retune"
        mock_embed.parallel_process_metadata.assert_called_once()
        mock_embed.close.assert_called_once()

    def test_submit_rejects_metric_that_does_not_match_feedback(
        self,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        organization,
        workspace,
        dataset,
        eval_template,
    ):
        other_metric = UserEvalMetric.objects.create(
            name="Other Same Org Metric",
            organization=organization,
            workspace=workspace,
            template=eval_template,
            dataset=dataset,
            config={"mapping": {}},
            status=StatusType.COMPLETED.value,
        )

        response = auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(other_metric.id),
                "action_type": "retune",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    def test_submit_rejects_deleted_feedback(
        self,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
    ):
        feedback_per_edt.delete()

        response = auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "retune",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_runner_cls.assert_not_called()
        mock_embed_cls.assert_not_called()

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_submit_recalculate_row_per_edt_eval(
        self,
        mock_workflow,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        eval_column_per_edt,
        eval_cell_per_edt,
        experiment_dataset,
        row,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed_cls.return_value = MagicMock()
        mock_workflow.return_value = "workflow-123"

        response = auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "recalculate_row",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["message"] == "Row queued for recalculation"
        assert data["workflow_id"] == "workflow-123"

        # Verify workflow was called with eval_only=True and edt_ids
        mock_workflow.assert_called_once()
        call_kwargs = mock_workflow.call_args
        assert call_kwargs.kwargs.get("eval_only") is True or (
            len(call_kwargs.args) == 0 and call_kwargs[1].get("eval_only") is True
        )

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_submit_recalculate_row_base_eval(
        self,
        mock_workflow,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_base,
        user_eval_metric,
        eval_column_base,
        eval_cell_base,
        row,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed_cls.return_value = MagicMock()
        mock_workflow.return_value = "workflow-456"

        response = auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_base.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "recalculate_row",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["message"] == "Row queued for recalculation"

        # Verify base_eval_only was set
        mock_workflow.assert_called_once()
        call_kwargs = mock_workflow.call_args
        assert call_kwargs.kwargs.get("base_eval_only") is True or (
            len(call_kwargs.args) == 0 and call_kwargs[1].get("base_eval_only") is True
        )

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_submit_recalculate_dataset_per_edt(
        self,
        mock_workflow,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        eval_column_per_edt,
        eval_cell_per_edt,
        experiment_dataset,
        row,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed_cls.return_value = MagicMock()
        mock_workflow.return_value = "workflow-789"

        response = auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "recalculate_dataset",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["message"] == "Dataset queued for recalculation"

        # Verify row_ids is empty (all rows)
        mock_workflow.assert_called_once()
        call_kwargs = mock_workflow.call_args
        row_ids = call_kwargs.kwargs.get("row_ids", call_kwargs[1].get("row_ids", []))
        assert row_ids == []

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_submit_recalculate_dataset_base_eval(
        self,
        mock_workflow,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_base,
        user_eval_metric,
        eval_column_base,
        eval_cell_base,
        row,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed_cls.return_value = MagicMock()
        mock_workflow.return_value = "workflow-101"

        response = auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_base.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "recalculate_dataset",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["message"] == "Dataset queued for recalculation"

        mock_workflow.assert_called_once()
        call_kwargs = mock_workflow.call_args
        assert call_kwargs.kwargs.get("base_eval_only") is True or (
            call_kwargs[1].get("base_eval_only") is True
        )
        row_ids = call_kwargs.kwargs.get("row_ids", call_kwargs[1].get("row_ids", []))
        assert row_ids == []

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_submit_retune_recalculate_success(
        self,
        mock_workflow,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        eval_column_per_edt,
        eval_cell_per_edt,
        experiment_dataset,
        row,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed_cls.return_value = MagicMock()
        mock_workflow.return_value = "workflow-retune-recalc"

        response = auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "retune_recalculate",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["message"] == "Row queued for recalculation"
        assert data["workflow_id"] == "workflow-retune-recalc"

        # retune_recalculate should behave like recalculate_row (single row)
        mock_workflow.assert_called_once()
        call_kwargs = mock_workflow.call_args
        row_ids = call_kwargs.kwargs.get("row_ids", call_kwargs[1].get("row_ids", []))
        assert len(row_ids) == 1

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_submit_resets_cells_to_running(
        self,
        mock_workflow,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        eval_column_per_edt,
        eval_cell_per_edt,
        experiment_dataset,
        row,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed_cls.return_value = MagicMock()
        mock_workflow.return_value = "wf"

        # Cell starts as PASS
        assert eval_cell_per_edt.status == CellStatus.PASS.value

        auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "recalculate_row",
            },
            format="json",
        )

        eval_cell_per_edt.refresh_from_db()
        assert eval_cell_per_edt.status == CellStatus.RUNNING.value
        assert eval_cell_per_edt.value == ""

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_submit_resets_columns_to_running(
        self,
        mock_workflow,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        eval_column_per_edt,
        eval_cell_per_edt,
        experiment_dataset,
        row,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed_cls.return_value = MagicMock()
        mock_workflow.return_value = "wf"

        assert eval_column_per_edt.status == StatusType.COMPLETED.value

        auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "recalculate_row",
            },
            format="json",
        )

        eval_column_per_edt.refresh_from_db()
        assert eval_column_per_edt.status == StatusType.RUNNING.value

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_submit_sets_experiment_status_running(
        self,
        mock_workflow,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        eval_column_per_edt,
        eval_cell_per_edt,
        experiment_dataset,
        row,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = ([], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed_cls.return_value = MagicMock()
        mock_workflow.return_value = "wf"

        assert experiment_with_evals.status == StatusType.COMPLETED.value

        auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "recalculate_row",
            },
            format="json",
        )

        experiment_with_evals.refresh_from_db()
        assert experiment_with_evals.status == StatusType.RUNNING.value

    def test_submit_missing_fields(self, auth_client, experiment):
        response = auth_client.post(
            url(experiment.id, "submit-feedback/"),
            {"action_type": "retune"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_submit_invalid_action_type(
        self, auth_client, experiment, feedback_per_edt, user_eval_metric
    ):
        response = auth_client.post(
            url(experiment.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "invalid_action",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_submit_nonexistent_feedback(
        self, auth_client, experiment, user_eval_metric
    ):
        response = auth_client.post(
            url(experiment.id, "submit-feedback/"),
            {
                "feedback_id": str(uuid.uuid4()),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "retune",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_submit_nonexistent_experiment(self, auth_client):
        response = auth_client.post(
            url(uuid.uuid4(), "submit-feedback/"),
            {
                "feedback_id": str(uuid.uuid4()),
                "user_eval_metric_id": str(uuid.uuid4()),
                "action_type": "retune",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_submit_cross_org_experiment(
        self, auth_client, snapshot_dataset, feedback_per_edt, user_eval_metric
    ):
        other_org = Organization.objects.create(name="Other Org 4")
        other_ds = Dataset.objects.create(
            name="Other Dataset 4",
            organization=other_org,
            source=DatasetSourceChoices.BUILD.value,
        )
        other_exp = ExperimentsTable.objects.create(
            name="Other Experiment 4",
            dataset=other_ds,
            snapshot_dataset=snapshot_dataset,
            status=StatusType.COMPLETED.value,
        )
        response = auth_client.post(
            url(other_exp.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "retune",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("model_hub.views.experiment_feedback_v2.EmbeddingManager")
    @patch("model_hub.views.experiment_feedback_v2.EvaluationRunner")
    def test_submit_embedding_manager_called(
        self,
        mock_runner_cls,
        mock_embed_cls,
        auth_client,
        experiment_with_evals,
        feedback_per_edt,
        user_eval_metric,
        eval_cell_per_edt,
        input_cell,
    ):
        mock_runner = MagicMock()
        mock_runner._get_required_fields_and_mappings.return_value = (["field1"], {})
        mock_runner_cls.return_value = mock_runner
        mock_embed = MagicMock()
        mock_embed_cls.return_value = mock_embed

        auth_client.post(
            url(experiment_with_evals.id, "submit-feedback/"),
            {
                "feedback_id": str(feedback_per_edt.id),
                "user_eval_metric_id": str(user_eval_metric.id),
                "action_type": "retune",
            },
            format="json",
        )

        mock_embed.parallel_process_metadata.assert_called_once()
        call_kwargs = mock_embed.parallel_process_metadata.call_args
        # Verify the metadatas contain feedback info
        metadatas = call_kwargs.kwargs.get(
            "metadatas", call_kwargs[1].get("metadatas", {})
        )
        assert "feedback_comment" in metadatas
        assert "feedback_value" in metadatas
        mock_embed.close.assert_called_once()


# ==================== StartEvalsProcess (experiment-scoped) Tests ====================


@pytest.mark.django_db
class TestStartEvalsProcessExperimentScoped:
    """Regression tests for POST /develops/<dataset_id>/start_evals_process/
    when called with experiment_id.

    The endpoint delegates to ExperimentRerunCellsV2View, which must match
    per-EDT eval columns whose source_id is
    "{edt_id}-{col_id}-sourceid-{metric_id}".
    """

    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_rerun_matches_per_edt_eval_column(
        self,
        mock_workflow,
        auth_client,
        experiment_with_evals,
        user_eval_metric,
        eval_column_per_edt,
        experiment_dataset,
        snapshot_dataset,
    ):
        mock_workflow.return_value = "wf-per-edt"

        response = auth_client.post(
            f"/model-hub/develops/{snapshot_dataset.id}/start_evals_process/",
            {
                "experiment_id": str(experiment_with_evals.id),
                "user_eval_ids": [str(user_eval_metric.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()

        eval_column_per_edt.refresh_from_db()
        assert eval_column_per_edt.status == StatusType.RUNNING.value
        mock_workflow.assert_called_once()

    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_rerun_matches_base_eval_column(
        self,
        mock_workflow,
        auth_client,
        experiment_with_evals,
        user_eval_metric,
        eval_column_base,
        snapshot_dataset,
    ):
        mock_workflow.return_value = "wf-base"

        response = auth_client.post(
            f"/model-hub/develops/{snapshot_dataset.id}/start_evals_process/",
            {
                "experiment_id": str(experiment_with_evals.id),
                "user_eval_ids": [str(user_eval_metric.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()

        eval_column_base.refresh_from_db()
        assert eval_column_base.status == StatusType.RUNNING.value
        mock_workflow.assert_called_once()

    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_rerun_missing_user_eval_ids(
        self, mock_workflow, auth_client, experiment_with_evals, snapshot_dataset
    ):
        response = auth_client.post(
            f"/model-hub/develops/{snapshot_dataset.id}/start_evals_process/",
            {"experiment_id": str(experiment_with_evals.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_workflow.assert_not_called()


@pytest.mark.django_db
class TestExperimentRerunCellsV2:
    @patch("tfc.temporal.experiments.start_rerun_cells_v2_workflow")
    def test_rerun_cells_accepts_max_concurrent_rows(
        self,
        mock_workflow,
        auth_client,
        experiment,
        snapshot_dataset,
        output_column,
        row,
    ):
        mock_workflow.return_value = "wf-cell-rerun"
        Cell.objects.create(
            dataset=snapshot_dataset,
            column=output_column,
            row=row,
            value="old output",
            status=CellStatus.PASS.value,
        )

        response = auth_client.post(
            f"{BASE_URL}/{experiment.id}/rerun-cells/",
            {
                "cells": [
                    {
                        "column_id": str(output_column.id),
                        "row_id": str(row.id),
                    }
                ],
                "max_concurrent_rows": 2,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        mock_workflow.assert_called_once()
        assert mock_workflow.call_args.kwargs["max_concurrent_rows"] == 2


@pytest.mark.django_db
class TestExperimentDeleteV2:
    @patch("tfc.temporal.experiments.cancel_experiment_workflow")
    def test_delete_soft_deletes_v2_generated_eval_columns(
        self,
        mock_cancel,
        auth_client,
        experiment_with_evals,
        output_column,
        eval_column_per_edt,
        eval_column_base,
        user_eval_metric,
        snapshot_dataset,
    ):
        per_edt_reason = Column.objects.create(
            name="Per-EDT Eval Reason",
            dataset=snapshot_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_column_per_edt.id}-sourceid-{user_eval_metric.id}",
            status=StatusType.COMPLETED.value,
        )
        base_reason = Column.objects.create(
            name="Base Eval Reason",
            dataset=snapshot_dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_column_base.id}-sourceid-{user_eval_metric.id}",
            status=StatusType.COMPLETED.value,
        )

        response = auth_client.delete(
            f"{BASE_URL}/delete/",
            {"experiment_ids": [str(experiment_with_evals.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        mock_cancel.assert_called_once_with(str(experiment_with_evals.id))

        for column in [
            output_column,
            eval_column_per_edt,
            per_edt_reason,
            eval_column_base,
            base_reason,
        ]:
            column.refresh_from_db()
            assert column.deleted is True, column.name
