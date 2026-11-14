"""Expected resource absence must not mask backend failures or emit tracebacks."""

import json
import logging
import uuid

import pytest
from django.db import InterfaceError, OperationalError
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tracer.models.project import Project
from tracer.models.trace_scan import TraceScanConfig

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]

DATASET_VIEW = "model_hub.views.develop_dataset"
PROJECT_VIEW = "tracer.views.project.ProjectView"
ENDPOINTS = [
    pytest.param("schema", id="dataset-schema"),
    pytest.param("download", id="dataset-download"),
    pytest.param("retrieve", id="project-retrieve"),
    pytest.param("destroy", id="project-destroy"),
    pytest.param("tags", id="project-tags"),
]


def request_resource(client, endpoint, resource_id):
    if endpoint == "schema":
        return client.get(f"/model-hub/dataset/{resource_id}/json-schema/")
    if endpoint == "download":
        return client.get(f"/model-hub/develops/{resource_id}/download_dataset/")
    if endpoint == "tags":
        return client.patch(
            f"/tracer/project/{resource_id}/tags/", {"tags": ["qa"]}, format="json"
        )
    method = client.delete if endpoint == "destroy" else client.get
    return method(f"/tracer/project/{resource_id}/")


def make_resource(endpoint, organization, workspace, *, deleted=False):
    fields = {
        "name": "Synthetic resource",
        "organization": organization,
        "workspace": workspace,
        "deleted": deleted,
    }
    if endpoint in {"schema", "download"}:
        return Dataset.no_workspace_objects.create(**fields)
    return Project.no_workspace_objects.create(
        **fields, model_type="GenerativeLLM", trace_type="experiment"
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize(
    "absence", ["missing", "deleted", "other_org", "other_workspace"]
)
def test_invisible_resource_returns_quiet_standard_404(
    auth_client, organization, workspace, user, endpoint, absence, caplog
):
    resource_id = uuid.uuid4()
    if absence != "missing":
        if absence == "other_org":
            organization = Organization.objects.create(name="Synthetic other org")
        if absence in {"other_org", "other_workspace"}:
            workspace = Workspace.objects.create(
                name="Synthetic other workspace",
                organization=organization,
                created_by=user,
                is_default=False,
            )
        resource = make_resource(
            endpoint, organization, workspace, deleted=absence == "deleted"
        )
        resource_id = resource.id

    with capture_logs() as logs, caplog.at_level(logging.ERROR):
        response = request_resource(auth_client, endpoint, resource_id)

    assert response.status_code == 404
    body = response.json()
    assert body["status"] is False
    assert body["type"] == "not_found"
    assert body["code"]
    assert body["detail"] == body["message"] == body["result"]
    assert "Synthetic" not in response.content.decode()
    assert not [entry for entry in logs if entry["log_level"] in {"error", "critical"}]
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    if endpoint in {"retrieve", "destroy", "tags"}:
        # The product's copy, not Django's "No Project matches the given query."
        assert body["message"] == "Project Not Found"
        assert body["code"] == "not_found"


@pytest.mark.parametrize("method", ["patch", "put"])
def test_missing_project_update_uses_product_not_found_copy(auth_client, method):
    response = getattr(auth_client, method)(
        f"/tracer/project/{uuid.uuid4()}/", {"tags": ["qa"]}, format="json"
    )

    assert response.status_code == 404
    body = response.json()
    assert body["type"] == "not_found"
    assert body["code"] == "not_found"
    assert body["detail"] == body["message"] == body["result"] == "Project Not Found"


@pytest.mark.parametrize("endpoint", ["retrieve", "destroy", "tags"])
@pytest.mark.parametrize(
    "resource_id", ["not-a-uuid", "00000000-0000-0000-0000-00000000000z"]
)
def test_malformed_project_id_returns_quiet_standard_400(
    auth_client, endpoint, resource_id, caplog, settings
):
    settings.DEBUG = False
    auth_client.raise_request_exception = False

    with capture_logs() as logs, caplog.at_level(logging.ERROR):
        response = request_resource(auth_client, endpoint, resource_id)

    assert response.status_code == 400
    body = response.json()
    assert body["status"] is False
    assert body["type"] == "validation_error"
    assert body["code"] == "invalid"
    assert body["detail"] == body["message"] == body["result"]
    assert resource_id not in response.content.decode()
    assert not [entry for entry in logs if entry["log_level"] in {"error", "critical"}]
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.parametrize("payload", [[], ["qa"], "qa", 42, True, None])
def test_non_object_project_tags_body_returns_quiet_400_without_mutation(
    auth_client, organization, workspace, payload, caplog, settings
):
    project = make_resource("tags", organization, workspace)
    project.tags = ["original"]
    project.save(update_fields=["tags"])
    updated_at = project.updated_at
    settings.DEBUG = False
    auth_client.raise_request_exception = False

    with capture_logs() as logs, caplog.at_level(logging.ERROR):
        response = auth_client.patch(
            f"/tracer/project/{project.id}/tags/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    assert response.status_code == 400
    body = response.json()
    assert body["status"] is False
    assert body["type"] == "validation_error"
    assert body["detail"] == body["message"] == body["result"]
    assert not [entry for entry in logs if entry["log_level"] in {"error", "critical"}]
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    project.refresh_from_db()
    assert project.tags == ["original"]
    assert project.updated_at == updated_at
    assert project.deleted is False


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("failure_stage", ["lookup", "processing"])
@pytest.mark.parametrize("error_class", [OperationalError, InterfaceError])
def test_database_failure_remains_observable_503(
    auth_client, organization, workspace, endpoint, failure_stage, error_class, mocker
):
    resource = make_resource(endpoint, organization, workspace)
    target = fault_target(endpoint, failure_stage)
    mocker.patch(target, side_effect=error_class("synthetic private database detail"))

    with capture_logs() as logs:
        response = request_resource(auth_client, endpoint, resource.id)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] is False
    assert body["type"] == "service_unavailable"
    assert "synthetic private" not in response.content.decode()
    assert any(entry["event"] == "api_database_unavailable" for entry in logs)


def fault_target(endpoint, failure_stage):
    if failure_stage == "lookup":
        return (
            f"{DATASET_VIEW}.get_object_or_404"
            if endpoint in {"schema", "download"}
            else f"{PROJECT_VIEW}.get_object"
        )
    return {
        "schema": f"{DATASET_VIEW}.get_json_column_schemas",
        "download": f"{DATASET_VIEW}.pd.DataFrame",
        "retrieve": f"{PROJECT_VIEW}.get_serializer",
        "destroy": f"{PROJECT_VIEW}._soft_delete_projects",
        "tags": "tracer.models.project.Project.save",
    }[endpoint]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_unexpected_processing_failure_remains_observable_500(
    auth_client, organization, workspace, endpoint, mocker, caplog, settings
):
    resource = make_resource(endpoint, organization, workspace)
    mocker.patch(
        fault_target(endpoint, "processing"),
        side_effect=RuntimeError("synthetic private processing detail"),
    )
    settings.DEBUG = False
    auth_client.raise_request_exception = False

    with caplog.at_level(logging.ERROR, logger="django.request"):
        response = request_resource(auth_client, endpoint, resource.id)

    assert response.status_code == 500
    assert "synthetic private" not in response.content.decode()
    if endpoint == "download":
        assert response.json()["status"] is False
        assert response.json()["type"] == "server_error"
    assert any(
        record.exc_info for record in caplog.records if record.levelno >= logging.ERROR
    )


@pytest.mark.parametrize("endpoint", ["schema", "download"])
def test_visible_dataset_retains_schema_and_csv(
    auth_client, organization, workspace, endpoint
):
    dataset = make_resource(endpoint, organization, workspace)
    column = Column.objects.create(
        dataset=dataset,
        name="payload",
        data_type=DataTypeChoices.JSON.value,
        source=SourceChoices.OTHERS.value,
        metadata={"json_schema": {"keys": ["answer"]}},
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=column, row=row, value='{"answer": 42}')

    response = request_resource(auth_client, endpoint, dataset.id)

    assert response.status_code == 200
    if endpoint == "schema":
        assert response.json()["result"][str(column.id)]["keys"] == ["answer"]
    else:
        assert response["Content-Type"] == "text/csv"
        content = b"".join(response.streaming_content).decode()
        assert "payload" in content
        assert "42" in content


@pytest.mark.parametrize("sampling_rate", [None, 0.25])
def test_visible_project_retains_sampling_rate(
    auth_client, organization, workspace, sampling_rate
):
    project = make_resource("retrieve", organization, workspace)
    if sampling_rate is not None:
        TraceScanConfig.objects.create(project=project, sampling_rate=sampling_rate)

    response = request_resource(auth_client, "retrieve", project.id)

    assert response.status_code == 200
    assert response.json()["result"]["sampling_rate"] == (sampling_rate or 0)


def test_visible_project_tags_and_destroy(auth_client, organization, workspace):
    project = make_resource("tags", organization, workspace)
    response = request_resource(auth_client, "tags", project.id)
    assert response.status_code == 200
    assert response.json()["result"]["tags"] == ["qa"]
    project.refresh_from_db()
    assert project.tags == ["qa"]

    response = request_resource(auth_client, "destroy", project.id)
    assert response.status_code == 200
    project.refresh_from_db()
    assert project.deleted is True
