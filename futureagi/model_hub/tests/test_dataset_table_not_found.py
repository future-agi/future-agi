"""Dataset table absence uses the same public 404 contract as schema reads."""

import logging
import uuid

import pytest
from django.db import OperationalError
from rest_framework.test import APIClient
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.integration,
    pytest.mark.api,
]


@pytest.fixture
def table_client(organization, workspace, user, settings):
    settings.DEBUG = False
    key = OrgApiKey.objects.create(
        name="Synthetic dataset key",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
        enabled=True,
        api_key=uuid.uuid4().hex,
        secret_key=uuid.uuid4().hex,
    )
    client = APIClient(raise_request_exception=False)
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


def table_request(client, dataset_id, exact_snapshot):
    return client.get(
        f"/model-hub/develops/{dataset_id}/get-dataset-table/",
        {"exact_snapshot": exact_snapshot},
    )


@pytest.mark.parametrize("exact_snapshot", [False, True], ids=["legacy", "exact"])
@pytest.mark.parametrize(
    "absence", ["missing", "deleted", "other_org", "other_workspace"]
)
def test_invisible_dataset_table_returns_quiet_standard_404(
    table_client, organization, workspace, user, absence, exact_snapshot, caplog
):
    dataset_id = uuid.uuid4()
    if absence != "missing":
        if absence == "other_org":
            organization = Organization.objects.create(name="Synthetic foreign org")
        if absence in {"other_org", "other_workspace"}:
            workspace = Workspace.objects.create(
                name="Synthetic foreign workspace",
                organization=organization,
                created_by=user,
                is_default=False,
            )
        dataset = Dataset.no_workspace_objects.create(
            name="Synthetic private dataset",
            organization=organization,
            workspace=workspace,
            deleted=absence == "deleted",
        )
        dataset_id = dataset.id

    with capture_logs() as logs, caplog.at_level(logging.ERROR):
        response = table_request(table_client, dataset_id, exact_snapshot)

    assert response.status_code == 404
    body = response.json()
    assert body["status"] is False
    assert body["type"] == "not_found"
    assert body["code"] == "not_found"
    assert body["detail"] == body["message"] == body["result"]
    assert "Synthetic" not in response.content.decode()
    assert response.exc_info is None
    assert not [entry for entry in logs if entry["log_level"] in {"error", "critical"}]
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.parametrize("exact_snapshot", [False, True], ids=["legacy", "exact"])
def test_visible_dataset_table_retains_content(
    table_client, organization, workspace, exact_snapshot
):
    dataset = Dataset.objects.create(
        name="Synthetic visible dataset", organization=organization, workspace=workspace
    )
    column = Column.objects.create(
        dataset=dataset,
        name="payload",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset, column=column, row=row, value="synthetic payload"
    )

    response = table_request(table_client, dataset.id, exact_snapshot)

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["metadata"]["total_rows"] == 1
    assert result["table"][0]["row_id"] == str(row.id)
    assert "synthetic payload" in response.content.decode()


@pytest.mark.parametrize("exact_snapshot", [False, True], ids=["legacy", "exact"])
@pytest.mark.parametrize("error_class", [OperationalError, RuntimeError])
def test_dataset_table_fault_is_not_reclassified_as_absence(
    table_client, exact_snapshot, error_class, mocker
):
    mocker.patch(
        "model_hub.views.develop_dataset.Dataset.objects.select_related",
        side_effect=error_class("synthetic private database detail"),
    )

    with capture_logs() as logs:
        response = table_request(table_client, uuid.uuid4(), exact_snapshot)

    expected_status = 503 if exact_snapshot and error_class is OperationalError else 500
    assert response.status_code == expected_status
    assert response.json()["status"] is False
    assert "synthetic private" not in response.content.decode()
    assert any(entry["log_level"] in {"warning", "error"} for entry in logs)
