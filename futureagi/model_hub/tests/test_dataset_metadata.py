"""Dataset metadata uses the existing authenticated, workspace-scoped settings API.

New tests only. Run in the model_hub fixture regime; the row-lock test needs
PostgreSQL and real transactions. No test in this file has been run locally.
"""

from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from django.db import DatabaseError, connections, transaction
from rest_framework.test import APIClient

from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.choices import DatasetSourceChoices, DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.tests.test_dsx_l4_dataset_behavior import _second_tenant
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


pytestmark = pytest.mark.django_db
LIST_URL = "/model-hub/develops/get-datasets/"
OBJECT_ERROR = "Metadata must be an object of key: value pairs."
LIMIT_ERROR = "A dataset can have up to 30 metadata keys."


def behavior_url(dataset):
    return f"/model-hub/develops/{dataset.id}/edit_dataset_behavior/"


def key_error(key):
    return (
        f"Metadata key “{key}” must start with a lowercase letter and use only "
        "lowercase letters, digits, “.”, “_” or “-” (40 characters at most)."
    )


@pytest.fixture
def dataset(organization, workspace):
    # Do not require the new field during setup: RED must reach the assertion.
    return Dataset.no_workspace_objects.create(
        name=f"metadata-{uuid.uuid4().hex[:10]}",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
        description="Original description",
        tags=["original"],
        dataset_config={"eval_recommendations": ["groundedness"]},
    )


@pytest.fixture
def question(dataset):
    column = Column.no_workspace_objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.column_config = {
        str(column.id): {"is_visible": True, "is_frozen": None, "role": "input"}
    }
    dataset.save()
    return column


@pytest.fixture
def tenant_b(db):
    org, workspace, client = _second_tenant("Metadata tenant")
    yield org, workspace, client
    client.stop_workspace_injection()


def save_metadata(client, dataset, patch, **settings):
    response = client.put(
        behavior_url(dataset), {"metadata": patch, **settings}, format="json"
    )
    assert response.status_code == 200, response.content
    return response


def read_metadata(client, dataset):
    response = client.get(behavior_url(dataset))
    assert response.status_code == 200, response.content
    return response.data["result"]["metadata"]


def listed(client, dataset):
    response = client.get(LIST_URL, {"search_text": dataset.name})
    assert response.status_code == 200, response.content
    return response.data["result"]["datasets"]


def test_metadata_defaults_are_empty_and_independent(auth_client, dataset):
    another = Dataset()
    dataset.refresh_from_db()
    assert dataset.metadata == {}
    assert another.metadata == {}
    dataset.metadata["local"] = True
    assert another.metadata == {}
    assert read_metadata(auth_client, dataset) == {}
    assert listed(auth_client, dataset)[0]["metadata"] == {}


@pytest.mark.parametrize("save_style", ["plain", "force-update", "positional"])
def test_stale_unrelated_save_preserves_acknowledged_metadata(
    auth_client, dataset, save_style
):
    # Load the unrelated writer BEFORE either acknowledged owner patch.
    stale = Dataset.no_workspace_objects.get(pk=dataset.pk)
    assert stale.metadata == {}
    save_metadata(auth_client, dataset, {"keep": False, "remove": 1})
    save_metadata(
        auth_client, dataset,
        {"remove": None, "zero": 0, "quoted": "2", "ratio": 2.5},
    )
    expected = {"keep": False, "zero": 0, "quoted": "2", "ratio": 2.5}
    before_updated_at = Dataset.no_workspace_objects.get(pk=dataset.pk).updated_at
    stale.description = "Unrelated writer's description"
    if save_style == "force-update":
        stale.save(force_update=True, using=stale._state.db)
    elif save_style == "positional":
        stale.save(False, False, stale._state.db, None)
    else:
        stale.save()
    dataset.refresh_from_db()
    assert dataset.description == "Unrelated writer's description"
    assert dataset.updated_at > before_updated_at
    for actual in (dataset.metadata, read_metadata(auth_client, dataset)):
        assert actual == expected
        assert {key: type(value) for key, value in actual.items()} == {
            key: type(value) for key, value in expected.items()
        }


@pytest.mark.parametrize("save_style", ["create", "save", "force-insert", "positional-insert"])
def test_new_dataset_save_keeps_initial_metadata(organization, workspace, save_style):
    metadata = {"enabled": False, "zero": 0, "text": "2", "ratio": 2.5}
    fields = {
        "name": f"metadata-create-{uuid.uuid4().hex[:10]}",
        "organization": organization,
        "workspace": workspace,
        "metadata": metadata,
    }
    if save_style == "create":
        created = Dataset.no_workspace_objects.create(**fields)
    else:
        created = Dataset(**fields)
        if save_style == "positional-insert":
            created.save(True, False, None, None)
        else:
            created.save(force_insert=save_style == "force-insert")
    created.refresh_from_db()
    assert created.metadata == metadata
    assert {key: type(value) for key, value in created.metadata.items()} == {
        key: type(value) for key, value in metadata.items()
    }
    assert created.created_at is not None
    assert created.updated_at is not None


@pytest.mark.parametrize("positional", [False, True], ids=["keyword", "positional"])
def test_explicit_metadata_update_fields_still_writes_metadata(dataset, positional):
    before_description = dataset.description
    before_updated_at = dataset.updated_at
    dataset.metadata = {"enabled": False, "zero": 0, "text": "2"}
    dataset.description = "Not named in update_fields"
    if positional:
        dataset.save(False, False, dataset._state.db, ["metadata"])
    else:
        dataset.save(update_fields=["metadata"], using=dataset._state.db)
    dataset.refresh_from_db()
    assert dataset.metadata == {"enabled": False, "zero": 0, "text": "2"}
    assert type(dataset.metadata["enabled"]) is bool
    assert type(dataset.metadata["zero"]) is int
    assert type(dataset.metadata["text"]) is str
    assert dataset.description == before_description
    # Explicit field lists retain Django's normal auto_now semantics.
    assert dataset.updated_at == before_updated_at
    dataset.metadata = {}
    dataset.save(update_fields=["metadata", "updated_at"])
    dataset.refresh_from_db()
    assert dataset.metadata == {}
    assert dataset.updated_at > before_updated_at


@pytest.mark.parametrize("update_fields", [["description"], []], ids=["partial", "empty"])
def test_stale_explicit_nonmetadata_save_respects_field_list(
    auth_client, dataset, update_fields
):
    stale = Dataset.no_workspace_objects.get(pk=dataset.pk)
    save_metadata(auth_client, dataset, {"keep": False})
    dataset.refresh_from_db()
    before_updated_at = dataset.updated_at
    stale.description = "Partial update"
    stale.save(update_fields=update_fields)
    dataset.refresh_from_db()
    assert dataset.metadata == {"keep": False}
    assert dataset.description == (
        "Partial update" if update_fields else "Original description"
    )
    assert dataset.updated_at == before_updated_at


@pytest.mark.parametrize("load", ["only", "defer"], ids=["only-description", "defer-metadata"])
def test_deferred_unrelated_save_keeps_metadata(auth_client, dataset, load):
    rows = Dataset.no_workspace_objects
    if load == "only":
        stale = rows.only("id", "description").get(pk=dataset.pk)
    else:
        stale = rows.defer("metadata").get(pk=dataset.pk)
    assert "metadata" in stale.get_deferred_fields()
    save_metadata(auth_client, dataset, {"keep": False, "zero": 0})
    # Even explicitly loading the old metadata before a later patch cannot
    # turn an unrelated save into an implicit metadata write.
    assert stale.metadata == {"keep": False, "zero": 0}
    save_metadata(auth_client, dataset, {"zero": None, "text": "2"})
    stale.description = "Deferred update"
    stale.save()
    dataset.refresh_from_db()
    assert dataset.metadata == {"keep": False, "text": "2"}
    assert dataset.description == "Deferred update"


def test_validation_does_not_promote_deferred_fields_to_writes(dataset, monkeypatch):
    stale = Dataset.no_workspace_objects.only("id", "description").get(pk=dataset.pk)
    assert "tags" in stale.get_deferred_fields()
    original_clean = stale.full_clean

    def clean_then_concurrent_update(*args, **kwargs):
        original_clean(*args, **kwargs)
        # full_clean reads deferred values; another writer can change them
        # before the eventual UPDATE. They were not this writer's fields.
        Dataset.no_workspace_objects.filter(pk=dataset.pk).update(tags=["latest"])

    monkeypatch.setattr(stale, "full_clean", clean_then_concurrent_update)
    stale.description = "Deferred writer"
    stale.save()
    dataset.refresh_from_db()
    assert dataset.description == "Deferred writer"
    assert dataset.tags == ["latest"]


@pytest.mark.parametrize("update_fields", [None, ["metadata"]], ids=["normal", "metadata"])
def test_metadata_save_boundary_retains_model_validation(dataset, update_fields):
    from django.core.exceptions import ValidationError

    dataset.model_type = "invalid-model-type"
    dataset.metadata = {"must": "not-save"}
    with pytest.raises(ValidationError):
        dataset.save(update_fields=update_fields)
    dataset.refresh_from_db()
    assert dataset.metadata == {}
    assert dataset.model_type != "invalid-model-type"


def test_owner_saves_reads_and_lists_typed_metadata(auth_client, dataset):
    metadata = {
        "domain": "billing",
        "enabled": False,
        "zero": 0,
        "quoted": "2",
        "ratio": 2.5,
        "negative": -2,
        "empty": "",
        "active": True,
    }
    save_metadata(auth_client, dataset, metadata)
    dataset.refresh_from_db()
    for actual in (
        dataset.metadata,
        read_metadata(auth_client, dataset),
        listed(auth_client, dataset)[0]["metadata"],
    ):
        assert actual == metadata
        assert {key: type(value) for key, value in actual.items()} == {
            key: type(value) for key, value in metadata.items()
        }
    assert dataset.description == "Original description"
    assert dataset.tags == ["original"]
    assert dataset.dataset_config == {"eval_recommendations": ["groundedness"]}


def test_metadata_patch_merges_and_null_deletes_without_replacing_other_keys(
    auth_client, dataset
):
    save_metadata(auth_client, dataset, {"keep": False, "change": 1, "remove": "old"})
    save_metadata(
        auth_client, dataset, {"change": "2", "remove": None, "absent": None}
    )
    expected = {"keep": False, "change": "2"}
    dataset.refresh_from_db()
    assert dataset.metadata == expected
    assert read_metadata(auth_client, dataset) == expected
    assert listed(auth_client, dataset)[0]["metadata"] == expected
    save_metadata(auth_client, dataset, {"keep": None, "change": None})
    assert read_metadata(auth_client, dataset) == {}


@pytest.mark.parametrize("body", [{}, {"metadata": {}}], ids=["omitted", "empty"])
def test_omitted_or_empty_metadata_leaves_saved_keys_untouched(
    auth_client, dataset, body
):
    save_metadata(auth_client, dataset, {"keep": False, "zero": 0})
    response = auth_client.put(
        behavior_url(dataset), {"description": "Changed", **body}, format="json"
    )
    assert response.status_code == 200, response.content
    dataset.refresh_from_db()
    assert dataset.metadata == {"keep": False, "zero": 0}
    assert dataset.description == "Changed"


@pytest.mark.parametrize(
    ("patch", "sentence"),
    [
        (None, OBJECT_ERROR),
        ([], OBJECT_ERROR),
        ([{"a": 1}], OBJECT_ERROR),
        ("text", OBJECT_ERROR),
        (0, OBJECT_ERROR),
        (False, OBJECT_ERROR),
        ({"": 1}, key_error("")),
        ({"Upper": 1}, key_error("Upper")),
        ({"1first": 1}, key_error("1first")),
        ({"has space": 1}, key_error("has space")),
        ({"a" * 41: 1}, key_error("a" * 41)),
        ({"trailing\n": 1}, key_error("trailing\n")),
        ({"items": []}, "“items” must be text, a number or true/false, not a list."),
        ({"nested": {}}, "“nested” must be text, a number or true/false, not an object."),
        ({"text": "x" * 201}, "The value of “text” is longer than 200 characters."),
        (
            {"text": "\U00010400" * 101},
            "The value of “text” is longer than 200 characters.",
        ),
        ({f"key{i}": i for i in range(31)}, LIMIT_ERROR),
    ],
    ids=[
        "null-map", "array-map", "object-array-map", "string-map", "number-map",
        "boolean-map", "empty-key", "uppercase-key", "digit-first-key", "space-key",
        "long-key", "newline-key", "array-value", "object-value", "long-text",
        "long-utf16-text", "too-many-keys",
    ],
)
def test_invalid_metadata_returns_frontend_sentence_and_saves_nothing(
    auth_client, dataset, question, patch, sentence
):
    save_metadata(auth_client, dataset, {"keep": "original"})
    before_name = dataset.name
    before_column_config = dict(dataset.column_config)
    response = auth_client.put(
        behavior_url(dataset),
        {
            "metadata": patch,
            "dataset_name": "must-not-save",
            "description": "must-not-save",
            "tags": ["must-not-save"],
            "dataset_config": {"dismiss_banner": True},
            "column_config": {str(question.id): {"is_visible": False}},
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert response.data["result"] == sentence
    dataset.refresh_from_db()
    assert dataset.metadata == {"keep": "original"}
    assert dataset.name == before_name
    assert dataset.description == "Original description"
    assert dataset.tags == ["original"]
    assert dataset.dataset_config == {"eval_recommendations": ["groundedness"]}
    assert dataset.column_config == before_column_config


def test_invalid_metadata_does_not_write_experiment_defaults(
    auth_client, dataset, monkeypatch
):
    from model_hub.services import experiment_defaults

    calls = []
    monkeypatch.setattr(
        experiment_defaults, "set_experiment_defaults", lambda **kwargs: calls.append(kwargs)
    )
    response = auth_client.put(
        behavior_url(dataset),
        {"metadata": {"invalid": []}, "experiment_default_eval_ids": []},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert calls == []


def test_invalid_later_key_does_not_partially_merge(auth_client, dataset):
    save_metadata(auth_client, dataset, {"keep": "original", "remove": 1})
    response = auth_client.put(
        behavior_url(dataset),
        {"metadata": {"keep": "changed", "remove": None, "bad": []}},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert read_metadata(auth_client, dataset) == {"keep": "original", "remove": 1}


def test_key_validation_precedes_value_validation(auth_client, dataset):
    response = auth_client.put(
        behavior_url(dataset),
        {"metadata": {"valid": [], "Invalid": True}},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert response.data["result"] == key_error("Invalid")


def test_metadata_key_and_value_boundaries_match_frontend(auth_client, dataset):
    metadata = {
        "a" * 40: "x" * 200,
        "a.b_c-9": "é" * 200,
        "unicode": "\U00010400" * 100,
    }
    save_metadata(auth_client, dataset, metadata)
    assert read_metadata(auth_client, dataset) == metadata


def test_merged_key_limit_counts_retained_keys_and_permits_delete_then_add(
    auth_client, dataset
):
    metadata = {f"key{i}": i for i in range(30)}
    save_metadata(auth_client, dataset, metadata)
    refused = auth_client.put(
        behavior_url(dataset), {"metadata": {"new": True}}, format="json"
    )
    assert refused.status_code == 400, refused.content
    assert refused.data["result"] == LIMIT_ERROR
    assert read_metadata(auth_client, dataset) == metadata
    # Check the RESULT, not patch size or insertion order: add comes first.
    save_metadata(auth_client, dataset, {"new": True, "key0": None})
    metadata.pop("key0")
    metadata["new"] = True
    assert read_metadata(auth_client, dataset) == metadata
    save_metadata(auth_client, dataset, {"new": False, "missing": None})
    metadata["new"] = False
    assert read_metadata(auth_client, dataset) == metadata


def test_patch_may_have_more_than_thirty_entries_if_result_fits(auth_client, dataset):
    save_metadata(auth_client, dataset, {f"key{i}": i for i in range(30)})
    save_metadata(
        auth_client, dataset, {**{f"key{i}": None for i in range(30)}, "only": 0}
    )
    assert read_metadata(auth_client, dataset) == {"only": 0}


def test_metadata_save_preserves_existing_column_and_settings_semantics(
    auth_client, dataset, question
):
    save_metadata(
        auth_client,
        dataset,
        {"domain": "support"},
        dataset_name=dataset.name + "-renamed",
        description="New description",
        tags=["new"],
        dataset_config={"dismiss_banner": True},
        column_config={str(question.id): {"is_visible": False}},
        column_order=[str(question.id)],
    )
    dataset.refresh_from_db()
    assert dataset.metadata == {"domain": "support"}
    assert dataset.name.endswith("-renamed")
    assert dataset.description == "New description"
    assert dataset.tags == ["new"]
    assert dataset.dataset_config == {
        "eval_recommendations": ["groundedness"], "dismiss_banner": True
    }
    assert dataset.column_order == [str(question.id)]
    assert dataset.column_config[str(question.id)] == {
        "is_visible": False, "is_frozen": None, "role": "input"
    }


def test_invalid_column_does_not_persist_valid_metadata(auth_client, dataset):
    response = auth_client.put(
        behavior_url(dataset),
        {"metadata": {"must": "not-save"}, "column_order": [str(uuid.uuid4())]},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert read_metadata(auth_client, dataset) == {}


def assert_hidden(client, dataset):
    assert listed(client, dataset) == []
    missing_url = f"/model-hub/develops/{uuid.uuid4()}/edit_dataset_behavior/"
    for url in (behavior_url(dataset), missing_url):
        read = client.get(url)
        write = client.put(url, {"metadata": {"foreign": True}}, format="json")
        assert read.status_code == 404, read.content
        assert write.status_code == 404, write.content
        assert read.data["result"] == "Dataset not found"
        assert write.data["result"] == "Dataset not found"
        assert "private-value" not in read.content.decode()


def test_other_organization_cannot_read_list_or_mutate_metadata(
    auth_client, dataset, tenant_b
):
    save_metadata(auth_client, dataset, {"private": "private-value"})
    _org, _workspace, other = tenant_b
    assert_hidden(other, dataset)
    assert read_metadata(auth_client, dataset) == {"private": "private-value"}


def test_sibling_workspace_cannot_read_list_or_mutate_metadata(
    auth_client, dataset, user, organization
):
    from conftest import WorkspaceAwareAPIClient

    save_metadata(auth_client, dataset, {"private": "private-value"})
    sibling = Workspace.no_workspace_objects.create(
        name=f"metadata-sibling-{uuid.uuid4().hex[:8]}",
        organization=organization, created_by=user, is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user, workspace=sibling, role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN, is_active=True,
    )
    other = WorkspaceAwareAPIClient()
    other.force_authenticate(user=user)
    other.set_workspace(sibling)
    try:
        assert_hidden(other, dataset)
        assert read_metadata(auth_client, dataset) == {"private": "private-value"}
    finally:
        other.stop_workspace_injection()


def test_deleted_dataset_cannot_be_read_listed_or_mutated(auth_client, dataset):
    save_metadata(auth_client, dataset, {"private": "private-value"})
    Dataset.no_workspace_objects.filter(pk=dataset.pk).update(deleted=True)
    assert_hidden(auth_client, dataset)
    dataset.refresh_from_db()
    assert dataset.metadata == {"private": "private-value"}


def test_unauthenticated_requests_cannot_read_list_or_write_metadata(
    auth_client, dataset
):
    save_metadata(auth_client, dataset, {"private": "private-value"})
    anonymous = APIClient()
    for response in (
        anonymous.get(behavior_url(dataset)),
        anonymous.get(LIST_URL),
        anonymous.put(behavior_url(dataset), {"metadata": {"foreign": True}}, format="json"),
    ):
        assert response.status_code in (401, 403), response.content
        assert "private-value" not in response.content.decode()
    assert read_metadata(auth_client, dataset) == {"private": "private-value"}


@pytest.mark.parametrize("columns_on", [True, False], ids=["columns-on", "columns-off"])
def test_metadata_list_readback_is_not_a_list_columns_switch(
    auth_client, dataset, settings, columns_on
):
    settings.DATASET_LIST_COLUMNS_ENABLED = columns_on
    save_metadata(auth_client, dataset, {"enabled": False})
    assert listed(auth_client, dataset)[0]["metadata"] == {"enabled": False}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "negative-inf"])
def test_metadata_merge_rejects_nonfinite_numbers_without_mutation(value):
    # JSON parsers may reject these before a view runs; exercise the validator too.
    from model_hub.services.dataset_metadata import DatasetMetadataError, merge_metadata

    current = {"keep": 0}
    with pytest.raises(DatasetMetadataError) as error:
        merge_metadata(current, {"valid": True, "number": value})
    assert str(error.value) == "“number” must be text, a number or true/false."
    assert current == {"keep": 0}


def test_metadata_merge_is_pure_and_preserves_types():
    from model_hub.services.dataset_metadata import merge_metadata

    current = {"keep": False, "remove": 1}
    patch = {"remove": None, "zero": 0, "text": "2"}
    assert merge_metadata(current, patch) == {"keep": False, "zero": 0, "text": "2"}
    assert current == {"keep": False, "remove": 1}
    assert patch == {"remove": None, "zero": 0, "text": "2"}


@pytest.mark.django_db(transaction=True)
def test_save_holds_dataset_row_lock_through_persistence(
    auth_client, dataset, monkeypatch
):
    alias = dataset._state.db
    if connections[alias].vendor != "postgresql":
        pytest.skip("Row-lock exclusion requires PostgreSQL")
    original_save = Dataset.save
    probed = []

    def competing_lock():
        connection = connections[alias]
        try:
            with transaction.atomic(using=alias), connection.cursor() as cursor:
                table = connection.ops.quote_name(Dataset._meta.db_table)
                cursor.execute(
                    f"SELECT id FROM {table} WHERE id = %s FOR UPDATE NOWAIT",
                    [dataset.pk],
                )
            return None
        except DatabaseError as error:
            cause = error.__cause__
            return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        finally:
            connection.close()

    def save_while_locked(instance, *args, **kwargs):
        if instance.pk == dataset.pk:
            assert connections[alias].in_atomic_block
            with ThreadPoolExecutor(max_workers=1) as pool:
                # A different connection must be refused until this save commits.
                lock_error = pool.submit(competing_lock).result(timeout=10)
            assert lock_error == "55P03", lock_error
            probed.append(instance.pk)
        return original_save(instance, *args, **kwargs)

    monkeypatch.setattr(Dataset, "save", save_while_locked)
    save_metadata(auth_client, dataset, {"first": False})
    save_metadata(auth_client, dataset, {"second": 0})
    assert probed == [dataset.pk, dataset.pk]
    assert read_metadata(auth_client, dataset) == {"first": False, "second": 0}


def test_failed_dataset_save_rolls_back_other_settings_writes(
    auth_client, dataset, monkeypatch
):
    from model_hub.services import experiment_defaults

    def write_defaults(**kwargs):
        # Stand in for the real service's related-row writes before dataset.save.
        Dataset.no_workspace_objects.filter(pk=dataset.pk).update(description="partial")

    def fail_save(*args, **kwargs):
        raise ValueError("simulated save failure")

    monkeypatch.setattr(experiment_defaults, "set_experiment_defaults", write_defaults)
    monkeypatch.setattr(Dataset, "save", fail_save)
    response = auth_client.put(
        behavior_url(dataset),
        {"metadata": {"new": True}, "experiment_default_eval_ids": []},
        format="json",
    )
    assert response.status_code == 500, response.content
    dataset.refresh_from_db()
    assert dataset.description == "Original description"
    assert dataset.metadata == {}


@pytest.mark.parametrize("flavor", ["oss", "cloud"], ids=["oss", "cloud"])
def test_dataset_metadata_capability_is_available_without_a_license(
    auth_client, monkeypatch, flavor
):
    from tfc.capabilities import views
    from tfc.licensing.types import DeploymentFlavor, DeploymentLocation, LicenseState

    monkeypatch.setattr(views, "FEATURE_REGISTRY", {})
    monkeypatch.setattr(views.CapabilitiesView, "_rollout_admissions", lambda *args: {})
    monkeypatch.setattr(
        views.service, "get_deployment_flavor",
        lambda: DeploymentFlavor.OSS if flavor == "oss" else DeploymentFlavor.CLOUD,
    )
    monkeypatch.setattr(
        views.service, "get_deployment_location", lambda: DeploymentLocation.SELF_HOSTED
    )
    monkeypatch.setattr(
        views.CapabilitiesView, "_get_license_state", lambda self: LicenseState.MISSING
    )
    monkeypatch.setattr(views.CapabilitiesView, "_is_admin", lambda *args: False)
    response = auth_client.get("/api/capabilities/")
    assert response.status_code == 200, response.content
    assert response.data["features"]["dataset_metadata"] == {
        "kind": "service",
        "display_name": "Dataset metadata",
        "allowed": True,
        "reason_code": None,
        "requires_network": False,
        "oss_baseline": True,
    }


def test_declared_dataset_response_contracts_include_metadata():
    from model_hub.serializers.develop_dataset_contracts import (
        DatasetDetailsSerializer, DatasetListItemSerializer, DatasetSettingsRequestSerializer,
    )

    for serializer in (DatasetDetailsSerializer, DatasetListItemSerializer):
        assert "metadata" in serializer().fields
    field = DatasetSettingsRequestSerializer().fields["metadata"]
    assert field.required is False
