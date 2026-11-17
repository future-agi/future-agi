"""Dataset admin writes only intentionally changed fields; requires the database."""

import json
import uuid

import pytest
from django import forms
from django.contrib.admin import AdminSite
from django.core.exceptions import ValidationError

from model_hub.admin import DatasetAdmin
from model_hub.models.develop_dataset import Dataset
from model_hub.tests.test_dataset_metadata import (
    dataset as dataset,
    read_metadata,
    save_metadata,
)


pytestmark = pytest.mark.django_db


class DatasetAdminForm(forms.ModelForm):
    admin_note = forms.CharField(required=False)

    class Meta:
        model = Dataset
        fields = ("name", "description", "metadata", "admin_note")


def bound_form(instance, **changes):
    data = {
        "name": instance.name,
        "description": instance.description,
        "metadata": json.dumps(instance.metadata),
        "admin_note": "",
    }
    # Callable model defaults render a hidden initial value. Submit that value
    # too, as the browser does, so unchanged JSON isn't compared against None.
    initial_metadata = DatasetAdminForm(instance=instance)["metadata"]
    if initial_metadata.field.show_hidden_initial:
        data[initial_metadata.html_initial_name] = initial_metadata.value()
    data.update(changes)
    form = DatasetAdminForm(data=data, instance=instance)
    assert form.is_valid(), form.errors
    return form


@pytest.fixture
def dataset_admin():
    return DatasetAdmin(Dataset, AdminSite())


@pytest.mark.parametrize(
    "metadata",
    [{"enabled": False, "zero": 0, "text": "2", "ratio": 2.5}, {}],
    ids=["replace", "clear"],
)
def test_existing_admin_metadata_edit_persists(dataset_admin, rf, dataset, metadata):
    dataset.metadata = {"original": True}
    dataset.save(update_fields=["metadata", "updated_at"])
    before_updated_at = dataset.updated_at
    form = bound_form(dataset, metadata=json.dumps(metadata))
    assert form.changed_data == ["metadata"]

    dataset_admin.save_model(rf.post("/"), form.save(commit=False), form, change=True)

    dataset.refresh_from_db()
    assert dataset.metadata == metadata
    assert {key: type(value) for key, value in dataset.metadata.items()} == {
        key: type(value) for key, value in metadata.items()
    }
    assert dataset.description == "Original description"
    assert dataset.updated_at > before_updated_at


def test_unrelated_admin_edit_preserves_acknowledged_concurrent_metadata(
    dataset_admin, rf, auth_client, dataset
):
    stale = Dataset.no_workspace_objects.get(pk=dataset.pk)
    form = bound_form(stale, description="Admin description")
    assert form.changed_data == ["description"]
    obj = form.save(commit=False)
    assert obj.metadata == {}

    # The form was loaded before either successful settings response. Neither
    # unedited metadata nor another unedited concrete field may be replayed.
    latest_name = dataset.name + "-latest"
    save_metadata(auth_client, dataset, {"keep": False, "remove": 1})
    save_metadata(
        auth_client,
        dataset,
        {"remove": None, "zero": 0, "text": "2"},
        dataset_name=latest_name,
    )
    dataset.refresh_from_db()
    before_updated_at = dataset.updated_at
    expected = {"keep": False, "zero": 0, "text": "2"}
    assert read_metadata(auth_client, dataset) == expected

    dataset_admin.save_model(rf.post("/"), obj, form, change=True)

    dataset.refresh_from_db()
    assert dataset.description == "Admin description"
    assert dataset.name == latest_name
    for actual in (dataset.metadata, read_metadata(auth_client, dataset)):
        assert actual == expected
        assert {key: type(value) for key, value in actual.items()} == {
            key: type(value) for key, value in expected.items()
        }
    assert dataset.updated_at > before_updated_at


def test_admin_insert_retains_metadata(dataset_admin, rf, organization, workspace):
    metadata = {"enabled": False, "zero": 0, "text": "2", "ratio": 2.5}
    obj = Dataset(
        name=f"metadata-admin-{uuid.uuid4().hex[:10]}",
        organization=organization,
        workspace=workspace,
    )
    form = bound_form(obj, metadata=json.dumps(metadata))
    obj = form.save(commit=False)
    assert obj.pk is not None  # A default UUID does not make this an update.
    assert obj._state.adding

    dataset_admin.save_model(rf.post("/"), obj, form, change=False)

    obj.refresh_from_db()
    assert obj.metadata == metadata
    assert {key: type(value) for key, value in obj.metadata.items()} == {
        key: type(value) for key, value in metadata.items()
    }
    assert obj.created_at is not None
    assert obj.updated_at is not None


def test_admin_save_excludes_nonmodel_form_fields(dataset_admin, rf, dataset):
    form = bound_form(dataset, description="Changed", admin_note="Not a model field")
    assert set(form.changed_data) == {"description", "admin_note"}

    dataset_admin.save_model(rf.post("/"), form.save(commit=False), form, change=True)

    dataset.refresh_from_db()
    assert dataset.description == "Changed"
    assert dataset.metadata == {}


def test_admin_metadata_edit_retains_model_validation(dataset_admin, rf, dataset):
    form = bound_form(dataset, metadata=json.dumps({"must": "not-save"}))
    obj = form.save(commit=False)
    obj.model_type = "invalid-model-type"

    with pytest.raises(ValidationError):
        dataset_admin.save_model(rf.post("/"), obj, form, change=True)

    dataset.refresh_from_db()
    assert dataset.metadata == {}
    assert dataset.model_type != "invalid-model-type"


def test_admin_fresh_post_uses_rendered_opening_metadata_baseline(
    dataset_admin, rf, auth_client, dataset
):
    dataset.metadata = {"keep": 1}
    dataset.save(update_fields=["metadata", "updated_at"])
    opening_metadata = DatasetAdminForm(instance=dataset)["metadata"]
    assert opening_metadata.field.show_hidden_initial
    posted = {
        "name": dataset.name,
        "description": "Admin description",
        "metadata": opening_metadata.value(),
        opening_metadata.html_initial_name: opening_metadata.value(),
        "admin_note": "",
    }

    save_metadata(auth_client, dataset, {"keep": 2})
    fresh = Dataset.no_workspace_objects.get(pk=dataset.pk)
    assert fresh.metadata == {"keep": 2}
    form = DatasetAdminForm(data=posted, instance=fresh)
    assert form.is_valid(), form.errors
    assert form.changed_data == ["description"]

    dataset_admin.save_model(rf.post("/"), form.save(commit=False), form, change=True)

    dataset.refresh_from_db()
    assert dataset.description == "Admin description"
    assert dataset.metadata == {"keep": 2}
    assert read_metadata(auth_client, dataset) == {"keep": 2}
