"""Observe imports validate the whole selection before touching destination state."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import ModelTypes
from model_hub.models.develop_dataset import Column, Dataset
from tracer.models.project import Project


@pytest.mark.django_db
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("selected", [[], ["owned"]])
def test_unavailable_source_does_not_mutate_destination(
    auth_client, organization, workspace, user, existing, selected
):
    project = Project.no_workspace_objects.create(
        name="source validation",
        trace_type="observe",
        organization=organization,
        workspace=workspace,
        model_type=ModelTypes.GENERATIVE_LLM.value,
    )
    destination = Dataset.no_workspace_objects.create(
        name="destination validation",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type=ModelTypes.GENERATIVE_LLM.value,
    )
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader.list_by_ids.return_value = [
        SimpleNamespace(id=s, project_id=str(project.id)) for s in selected
    ]
    mapping = [{"col_name": "operation", "span_field": "name", "data_type": "text"}]
    body = {"project": str(project.id), "span_ids": ["owned", "unavailable"]}
    if existing:
        body.update(dataset_id=str(destination.id), new_mapping_config=mapping)
        action = "add_to_existing_dataset"
    else:
        body.update(new_dataset_name="must not exist", mapping_config=mapping)
        action = "add_to_new_dataset"
    before = (Dataset.no_workspace_objects.count(), Column.no_workspace_objects.count())
    with (
        patch("tracer.views.dataset.get_reader", return_value=reader),
        patch("tracer.views.dataset.create_new_cells") as submit,
    ):
        response = auth_client.post(f"/tracer/dataset/{action}/", body, format="json")
    assert response.status_code == 400
    assert (
        Dataset.no_workspace_objects.count(),
        Column.no_workspace_objects.count(),
    ) == before
    submit.assert_not_called()
