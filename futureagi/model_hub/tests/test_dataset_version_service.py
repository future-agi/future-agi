import math
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.services.dataset_version_service import (
    DatasetVersionItemInput,
    canonical_content_hash,
    create_dataset_version,
)


@pytest.fixture
def snapshot_dataset(organization, workspace, user):
    return Dataset.objects.create(
        name="Snapshot service dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )


def _item(*, external_id=None, example_id=None, text="hello"):
    return DatasetVersionItemInput(
        input={"text": text},
        expected={"answer": "ok"},
        metadata={"split": "eval"},
        external_id=external_id,
        example_id=example_id,
    )


@pytest.mark.parametrize(
    "value",
    [uuid.uuid4(), {1: "non-string-key"}, ("tuple",), {"bytes": b"bad"}],
)
def test_canonical_hash_rejects_non_json_values(value):
    with pytest.raises(ValidationError):
        canonical_content_hash(input=value, expected=None, metadata={})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_hash_rejects_non_finite_numbers(value):
    with pytest.raises(ValidationError):
        canonical_content_hash(input={"value": value}, expected=None, metadata={})


def test_canonical_hash_is_order_independent_and_content_sensitive():
    first = canonical_content_hash(
        input={"b": 2, "a": 1}, expected=None, metadata={"z": True}
    )
    second = canonical_content_hash(
        input={"a": 1, "b": 2}, expected=None, metadata={"z": True}
    )
    changed = canonical_content_hash(
        input={"a": 1, "b": 3}, expected=None, metadata={"z": True}
    )

    assert first == second
    assert first != changed


def test_canonical_hash_rejects_null_object_keys_and_matches_model_helper():
    with pytest.raises(ValidationError):
        canonical_content_hash(
            input={"bad\x00key": "value"}, expected=None, metadata={}
        )

    payload = {"unicode": "café", "value": 1.0}
    assert DatasetVersionItem.calculate_content_hash(
        input=payload, expected=None, metadata={}
    ) == canonical_content_hash(input=payload, expected=None, metadata={})


@pytest.mark.django_db
def test_service_creates_version_and_preserves_explicit_identity(
    snapshot_dataset, user
):
    example_id = uuid.uuid4()
    version = create_dataset_version(
        dataset=snapshot_dataset,
        created_by=user,
        items=[_item(external_id="case-1", example_id=example_id)],
    )

    saved_item = version.items.get()
    assert version.organization_id == snapshot_dataset.organization_id
    assert saved_item.example_id == example_id
    assert saved_item.external_id == "case-1"
    assert saved_item.content_hash == canonical_content_hash(
        input=saved_item.input,
        expected=saved_item.expected,
        metadata=saved_item.metadata,
    )


@pytest.mark.django_db
def test_service_carries_parent_external_identity_and_generates_new_identity(
    snapshot_dataset, user
):
    parent = create_dataset_version(
        dataset=snapshot_dataset,
        created_by=user,
        items=[_item(external_id="case-1")],
    )
    parent_item = parent.items.get()
    child = create_dataset_version(
        dataset=snapshot_dataset,
        created_by=user,
        parent_version=parent,
        items=[
            _item(external_id="case-1", text="changed"),
            _item(external_id="case-2"),
        ],
    )

    child_items = {item.external_id: item for item in child.items.all()}
    assert child_items["case-1"].example_id == parent_item.example_id
    assert child_items["case-2"].example_id != parent_item.example_id


@pytest.mark.django_db
def test_service_uses_stable_legacy_row_identity(snapshot_dataset, user):
    row = Row.objects.create(dataset=snapshot_dataset, order=0)
    version = create_dataset_version(
        dataset=snapshot_dataset,
        created_by=user,
        items=[DatasetVersionItemInput(input={"text": "row"}, legacy_row_id=row.id)],
    )

    assert (
        version.items.get().example_id
        == DatasetVersionItem.example_id_for_legacy_row(row.id)
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "items",
    [
        [_item(external_id="duplicate"), _item(external_id="duplicate")],
        [
            _item(example_id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
            _item(example_id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        ],
    ],
)
def test_service_rejects_duplicate_identity_without_header(
    snapshot_dataset, user, items
):
    with pytest.raises(ValidationError):
        create_dataset_version(dataset=snapshot_dataset, created_by=user, items=items)

    assert not DatasetVersion.objects.filter(dataset=snapshot_dataset).exists()


@pytest.mark.django_db
def test_service_rejects_wrong_parent_without_header(
    snapshot_dataset, organization, workspace, user
):
    other_dataset = Dataset.objects.create(
        name="Other dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    other_parent = DatasetVersion.objects.create(dataset=other_dataset)
    with pytest.raises(ValidationError):
        create_dataset_version(
            dataset=snapshot_dataset,
            created_by=user,
            parent_version=other_parent,
            items=[DatasetVersionItemInput(input={"text": "bad"})],
        )

    assert not DatasetVersion.objects.filter(dataset=snapshot_dataset).exists()


@pytest.mark.django_db
def test_service_rejects_wrong_legacy_row_without_header(
    snapshot_dataset, organization, workspace, user
):
    other_dataset = Dataset.objects.create(
        name="Other dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    row = Row.objects.create(dataset=other_dataset, order=0)

    with pytest.raises(ValidationError):
        create_dataset_version(
            dataset=snapshot_dataset,
            created_by=user,
            items=[
                DatasetVersionItemInput(input={"text": "bad"}, legacy_row_id=row.id)
            ],
        )

    assert not DatasetVersion.objects.filter(dataset=snapshot_dataset).exists()


@pytest.mark.django_db
def test_service_rejects_actor_from_another_organization(snapshot_dataset):
    other_organization = Organization.objects.create(name="Other actor org")
    other_user = User.objects.create_user(
        email="other-actor@example.com",
        password="testpassword123",
        name="Other Actor",
        organization=other_organization,
    )

    with pytest.raises(ValidationError, match="organization"):
        create_dataset_version(
            dataset=snapshot_dataset,
            created_by=other_user,
            items=[_item(external_id="blocked")],
        )


@pytest.mark.django_db
def test_service_rejects_actor_without_workspace_access(snapshot_dataset, organization):
    actor = User.objects.create_user(
        email="workspace-denied@example.com",
        password="testpassword123",
        name="Restricted Actor",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=actor, organization=organization, level=3, is_active=True
    )

    with pytest.raises(ValidationError, match="workspace"):
        create_dataset_version(
            dataset=snapshot_dataset,
            created_by=actor,
            items=[_item()],
        )


@pytest.mark.parametrize("value", [{"bad\x00key": 1}, {"bad": "\ud800"}, {"\ud800": 1}])
def test_canonical_hash_rejects_postgres_incompatible_strings(value):
    with pytest.raises(ValidationError):
        canonical_content_hash(input=value, expected=None, metadata={})


@pytest.mark.django_db
def test_service_rolls_back_header_when_item_insert_fails(snapshot_dataset, user):
    bulk_create = DatasetVersionItem.objects.bulk_create

    def insert_then_duplicate(items, **kwargs):
        bulk_create(items[:1], **kwargs)
        duplicate = DatasetVersionItem(
            dataset_version_id=items[0].dataset_version_id,
            example_id=items[0].example_id,
            input={"duplicate": True},
        )
        bulk_create([duplicate])

    with patch(
        "model_hub.services.dataset_version_service.DatasetVersionItem.objects.bulk_create",
        side_effect=insert_then_duplicate,
    ):
        with pytest.raises(IntegrityError):
            create_dataset_version(
                dataset=snapshot_dataset,
                created_by=user,
                items=[_item(external_id="rollback")],
            )

    assert not DatasetVersion.objects.filter(dataset=snapshot_dataset).exists()
    assert not DatasetVersionItem.objects.filter(
        dataset_version__dataset=snapshot_dataset
    ).exists()


@pytest.mark.django_db
def test_service_keeps_parent_unchanged_when_creating_child(snapshot_dataset, user):
    parent = create_dataset_version(
        dataset=snapshot_dataset,
        created_by=user,
        items=[_item(external_id="case-1")],
    )
    parent_item = parent.items.get()
    parent_hash = parent.content_hash

    create_dataset_version(
        dataset=snapshot_dataset,
        created_by=user,
        parent_version=parent,
        items=[_item(external_id="case-1", text="child")],
    )

    parent.refresh_from_db()
    parent_item.refresh_from_db()
    assert parent.content_hash == parent_hash
    assert parent_item.input == {"text": "hello"}


@pytest.mark.django_db
@pytest.mark.parametrize("value", [1.0, 1e20, 1e-7])
def test_item_hash_survives_postgres_json_roundtrip(snapshot_dataset, user, value):
    version = create_dataset_version(
        dataset=snapshot_dataset,
        created_by=user,
        items=[DatasetVersionItemInput(input={"value": value})],
    )
    item = version.items.get()

    assert item.content_hash == canonical_content_hash(
        input=item.input, expected=item.expected, metadata=item.metadata
    )


@pytest.mark.django_db(transaction=True)
def test_version_only_becomes_visible_with_all_items(snapshot_dataset, user):
    bulk_create = DatasetVersionItem.objects.bulk_create
    observed = []

    def externally_visible(version_id):
        try:
            return DatasetVersion.no_workspace_objects.filter(id=version_id).exists()
        finally:
            connections["default"].close()

    def insert_and_check(items, **kwargs):
        result = bulk_create(items, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            observed.append(
                pool.submit(externally_visible, items[0].dataset_version_id).result(
                    timeout=10
                )
            )
        return result

    with patch(
        "model_hub.services.dataset_version_service.DatasetVersionItem.objects.bulk_create",
        side_effect=insert_and_check,
    ):
        version = create_dataset_version(
            dataset=snapshot_dataset,
            created_by=user,
            items=[_item(external_id="a"), _item(external_id="b")],
        )

    assert observed == [False]
    assert DatasetVersion.no_workspace_objects.filter(id=version.id).exists()
    assert version.items.count() == 2


@pytest.fixture
def inherited_member(snapshot_dataset, user):
    row = Row.objects.create(dataset=snapshot_dataset, order=0)
    item = DatasetVersionItemInput(
        legacy_row_id=row.pk,
        example_id=uuid.uuid4(),
        input={"values": {"question": [False, 0, "captured"]}},
        expected={"answer": "saved"},
        metadata={"split_names": ["test"], "category": {"label": "original"}},
        external_id="saved-example",
        source_trace_id="saved-trace",
        source_span_id="saved-span",
    )
    parent = create_dataset_version(
        dataset=snapshot_dataset, created_by=user, items=[item]
    )
    row.delete()
    return parent, item


@pytest.mark.django_db
def test_deleted_source_inherits_exact_parent_evidence(
    snapshot_dataset, user, inherited_member
):
    parent, item = inherited_member
    before = list(parent.items.values())
    child = create_dataset_version(
        dataset=snapshot_dataset, created_by=user, parent_version=parent, items=[item]
    )
    saved = child.items.get()
    assert child.parent_version_id == parent.pk
    assert {name: getattr(saved, name) for name in vars(item)} == vars(item)
    assert saved.content_hash == before[0]["content_hash"]
    assert list(parent.items.values()) == before
    assert Row.no_workspace_objects.filter(pk=item.legacy_row_id).count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changes",
    [
        {"example_id": uuid.uuid4()},
        {"legacy_row_id": uuid.uuid4()},
        {"input": {"values": {"question": [0, 0, "captured"]}}},
        {"expected": {"answer": "different"}},
        {"metadata": {"split_names": ["other"]}},
        {"external_id": "different"},
        {"source_trace_id": "different"},
        {"source_span_id": "different"},
    ],
)
def test_deleted_source_rejects_changed_inheritance_atomically(
    snapshot_dataset, user, inherited_member, changes
):
    parent, item = inherited_member
    before = list(parent.items.values())
    with pytest.raises(ValidationError):
        create_dataset_version(
            dataset=snapshot_dataset,
            created_by=user,
            parent_version=parent,
            items=[replace(item, **changes)],
        )
    assert DatasetVersion.objects.filter(dataset=snapshot_dataset).count() == 1
    assert list(parent.items.values()) == before


@pytest.mark.django_db
def test_deleted_source_requires_the_explicit_matching_parent(
    snapshot_dataset, user, inherited_member
):
    parent, item = inherited_member
    unmatched = create_dataset_version(
        dataset=snapshot_dataset, created_by=user, items=[_item()]
    )
    for selected_parent in (None, unmatched):
        with pytest.raises(ValidationError):
            create_dataset_version(
                dataset=snapshot_dataset,
                created_by=user,
                parent_version=selected_parent,
                items=[item],
            )
    assert DatasetVersion.objects.filter(dataset=snapshot_dataset).count() == 2
    assert parent.items.get().legacy_row_id == item.legacy_row_id


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["organization_id", "workspace_id", "dataset_id"])
def test_deleted_source_refuses_foreign_parent_scope(
    snapshot_dataset, user, inherited_member, organization, field
):
    from accounts.models.workspace import Workspace

    parent, item = inherited_member
    other_org = Organization.objects.create(name="Other inherited organization")
    other_workspace = Workspace.objects.create(
        name="Other inherited workspace", organization=organization, created_by=user
    )
    other_dataset = Dataset.objects.create(
        name="Other inherited dataset",
        organization=organization,
        workspace=other_workspace,
        column_order=[],
        column_config={},
    )
    foreign = {
        "organization_id": other_org.pk,
        "workspace_id": other_workspace.pk,
        "dataset_id": other_dataset.pk,
    }
    DatasetVersion.no_workspace_objects.filter(pk=parent.pk).update(
        **{field: foreign[field]}
    )
    before = DatasetVersion.no_workspace_objects.count()
    with pytest.raises(ValidationError):
        create_dataset_version(
            dataset=snapshot_dataset,
            created_by=user,
            parent_version=parent,
            items=[item],
        )
    assert DatasetVersion.no_workspace_objects.count() == before


@pytest.mark.django_db
def test_active_source_still_allows_new_captured_values(snapshot_dataset, user):
    row = Row.objects.create(dataset=snapshot_dataset, order=0)
    item = DatasetVersionItemInput(input="first", legacy_row_id=row.pk)
    parent = create_dataset_version(
        dataset=snapshot_dataset, created_by=user, items=[item]
    )
    child = create_dataset_version(
        dataset=snapshot_dataset,
        created_by=user,
        parent_version=parent,
        items=[replace(item, input="later", metadata={"split_names": ["new"]})],
    )
    assert child.items.get().input == "later"
    assert child.items.get().metadata == {"split_names": ["new"]}
    assert parent.items.get().input == "first"
