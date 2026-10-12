import uuid

import pytest
from django.core.exceptions import ValidationError

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset, Row


def test_content_hash_is_canonical_for_mapping_order():
    first = DatasetVersionItem.calculate_content_hash(
        input={"b": 2, "a": 1}, expected=None, metadata={"z": True}
    )
    second = DatasetVersionItem.calculate_content_hash(
        input={"a": 1, "b": 2}, expected=None, metadata={"z": True}
    )

    assert first == second


def test_legacy_row_identity_is_stable_and_namespaced():
    row_id = uuid.uuid4()

    first = DatasetVersionItem.example_id_for_legacy_row(row_id)
    second = DatasetVersionItem.example_id_for_legacy_row(row_id)

    assert first == second
    assert first != row_id


@pytest.fixture
def version_dataset(organization, workspace, user):
    return Dataset.objects.create(
        name="Versioned dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )


@pytest.mark.django_db
def test_version_copies_tenant_fields_and_item_hashes(
    version_dataset, organization, workspace
):
    version = DatasetVersion.objects.create(dataset=version_dataset)

    assert version.organization_id == organization.id
    assert version.workspace_id == workspace.id

    item = DatasetVersionItem.objects.create(
        dataset_version=version,
        input={"question": "What is 2 + 2?"},
        expected={"answer": "4"},
        metadata={"split": "eval"},
    )

    assert item.content_hash == DatasetVersionItem.calculate_content_hash(
        input=item.input,
        expected=item.expected,
        metadata=item.metadata,
    )


@pytest.mark.django_db
def test_example_identity_can_be_reused_across_versions(version_dataset):
    first = DatasetVersion.objects.create(dataset=version_dataset)
    second = DatasetVersion.objects.create(
        dataset=version_dataset, parent_version=first
    )
    example_id = uuid.uuid4()

    first_item = DatasetVersionItem.objects.create(
        dataset_version=first,
        example_id=example_id,
        input={"text": "before"},
    )
    second_item = DatasetVersionItem.objects.create(
        dataset_version=second,
        example_id=example_id,
        input={"text": "after"},
    )

    assert first_item.example_id == second_item.example_id
    assert second.parent_version_id == first.id


@pytest.mark.django_db
def test_legacy_row_projection_has_deterministic_identity(version_dataset):
    row = Row.objects.create(dataset=version_dataset, order=0)

    assert DatasetVersionItem.example_id_for_legacy_row(row.id) == (
        DatasetVersionItem.example_id_for_legacy_row(row.id)
    )


@pytest.mark.parametrize("field", ["organization_id", "workspace_id"])
def test_version_rejects_copied_tenant_mismatch(field):
    organization_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    dataset = Dataset(
        id=uuid.uuid4(),
        name="dataset",
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    version = DatasetVersion(
        dataset=dataset,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    setattr(version, field, uuid.uuid4())

    with pytest.raises(ValidationError, match=field.removesuffix("_id")):
        version.clean()
