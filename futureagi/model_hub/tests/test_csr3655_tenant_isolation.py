import uuid

import pytest
from django.urls import reverse
from rest_framework import status

from accounts.models import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.experiments import ExperimentsTable


def _other_org_dataset(*, with_workspace=True):
    other_org = Organization.objects.create(name="Other Organization")
    other_user = User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=other_org,
    )
    other_workspace = None
    if with_workspace:
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=other_user,
        )
    dataset = Dataset.objects.create(
        name="Other dataset",
        organization=other_org,
        workspace=other_workspace,
    )
    column = Column.objects.create(
        name="status",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    return dataset, column


@pytest.mark.parametrize(
    "path_name,payload_extra",
    [
        (
            "classify-column",
            {"labels": ["a", "b"], "new_column_name": "classified"},
        ),
        (
            "extract-entities",
            {"instruction": "names", "new_column_name": "entities"},
        ),
        (
            "add_vector_db_column",
            {
                "sub_type": "pinecone",
                "api_key": "not-a-real-key",
                "new_column_name": "vectors",
            },
        ),
    ],
)
def test_column_create_rejects_other_org_dataset(
    auth_client, db, path_name, payload_extra, monkeypatch
):
    dataset, column = _other_org_dataset(with_workspace=False)
    before = Column.objects.filter(dataset=dataset).count()
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.classify_column_async.delay",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not enqueue a foreign classification job")
        ),
    )

    response = auth_client.post(
        reverse(path_name, kwargs={"dataset_id": dataset.id}),
        {"column_id": str(column.id), **payload_extra},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Column.objects.filter(dataset=dataset).count() == before


def test_experiment_download_rejects_other_org(auth_client, db):
    dataset, column = _other_org_dataset(with_workspace=False)
    experiment = ExperimentsTable.objects.create(
        name="Other experiment",
        dataset=dataset,
        column=column,
    )

    for path in (
        f"/model-hub/experiments/{experiment.id}/download/",
        f"/model-hub/experiments/v2/{experiment.id}/download/",
    ):
        response = auth_client.get(path)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "text/csv" not in response.get("Content-Type", "")
