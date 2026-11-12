"""LC-02: repeated classic clone requests always have an available name."""

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

pytestmark = pytest.mark.django_db


@pytest.fixture
def source(organization, workspace, monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request", None
    )
    dataset = Dataset.objects.create(
        name="Golden set",
        organization=organization,
        workspace=workspace,
        dataset_config={"eval_recommendations": []},
    )
    column = Column.objects.create(
        dataset=dataset,
        name="input",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=column, row=row, value="preserved")
    return dataset


def clone(auth_client, source, body=None):
    return auth_client.post(
        f"/model-hub/develops/clone-dataset/{source.id}/", body or {}, format="json"
    )


def test_three_default_copies_have_unique_names_and_preserved_cells(
    auth_client, source
):
    ids = set()
    for expected in [
        "Copy of Golden set",
        "Copy of Golden set (2)",
        "Copy of Golden set (3)",
    ]:
        response = clone(auth_client, source)
        assert response.status_code == 200, response.data
        result = response.data["result"]
        assert result["dataset_name"] == expected
        ids.add(result["dataset_id"])
        copied = Dataset.objects.get(id=result["dataset_id"])
        assert copied.organization_id == source.organization_id
        assert copied.workspace_id == source.workspace_id
        assert list(
            Cell.objects.filter(dataset=copied).values_list("value", flat=True)
        ) == ["preserved"]
    assert len(ids) == 3


def test_generated_name_respects_org_wide_collisions(auth_client, source, user):
    other = Workspace.objects.create(
        name="Other workspace", organization=source.organization, created_by=user
    )
    for name in ["Copy of Golden set", "Copy of Golden set (2)"]:
        Dataset.objects.create(
            name=name, organization=source.organization, workspace=other
        )
    response = clone(auth_client, source)
    assert response.status_code == 200, response.data
    assert response.data["result"]["dataset_name"] == "Copy of Golden set (3)"


def test_explicit_name_collision_is_not_silently_renamed(auth_client, source):
    response = clone(auth_client, source, {"new_dataset_name": source.name})
    assert response.status_code == 400
    assert (
        Dataset.objects.filter(organization=source.organization, deleted=False).count()
        == 1
    )


def test_deleted_copy_name_can_be_reused(auth_client, source):
    Dataset.objects.create(
        name="Copy of Golden set",
        organization=source.organization,
        workspace=source.workspace,
        deleted=True,
    )
    response = clone(auth_client, source)
    assert response.status_code == 200, response.data
    assert response.data["result"]["dataset_name"] == "Copy of Golden set"


def test_copy_of_maximum_length_name_fits_model_field(auth_client, source):
    limit = Dataset._meta.get_field("name").max_length
    source.name = "x" * limit
    source.save(update_fields=["name"])
    names = []
    for _ in range(2):
        response = clone(auth_client, source)
        assert response.status_code == 200, response.data
        name = response.data["result"]["dataset_name"]
        assert len(name) <= limit
        names.append(name)
    assert names[0] != names[1]
