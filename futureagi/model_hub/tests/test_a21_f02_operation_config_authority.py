"""Saved-config GET regressions; synthetic PostgreSQL VM ONLY, no local import.

These tests import only symbols present before the GET repair. Saved columns are
native rows, not response mocks; nothing outside the test's own rows is assumed.
"""

import copy
from unittest.mock import Mock
from uuid import uuid4

import pytest

from accounts.models.workspace import WorkspaceMembership
from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.a21_authority_support import (
    assert_admitted,
    grant,
    session_client,
    snapshot,
)
from model_hub.tests.test_a21_f02_credential_authority import (
    conditional,
    config,
    records,
)
from model_hub.views import dynamic_columns
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
    workspace_scope_is_empty,
)

pytest_plugins = [
    "model_hub.tests.a21_authority_support",
    "model_hub.tests.a21_f02_boundary_support",
]
pytestmark = pytest.mark.django_db(transaction=True)
SHAPES = ["vector", "if", "elif", "else"]


@pytest.fixture(autouse=True)
def read_boundaries(monkeypatch, authority_scope, transport):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("forbidden GET side effect")
        raise AssertionError("Config GET must not decode, serve, or call a provider")

    for model in (ApiKey, SecretModel):
        monkeypatch.setattr(model, "actual_key", property(forbidden))
        monkeypatch.setattr(model, "_clear", forbidden)
    monkeypatch.setattr(ApiKey, "actual_json", property(forbidden))
    monkeypatch.setattr(dynamic_columns, "serving_available", forbidden)
    monkeypatch.setattr(dynamic_columns, "require_serving", forbidden)
    monkeypatch.setattr(dynamic_columns.model_manager, "get_embeddings", forbidden)
    monkeypatch.setattr("tfc.utils.lazy_extras.load_extra", forbidden)
    logger = Mock()
    monkeypatch.setattr(dynamic_columns, "logger", logger)
    yield logger
    assert calls == []
    assert transport.captured == []
    assert logger.mock_calls == [], "GET denial must not log payloads or references"


def saved(rows, shape, cfg):
    # Matches AddVectorDBColumnView and ConditionalColumnView persisted shapes.
    metadata = copy.deepcopy(cfg)
    source = SourceChoices.VECTOR_DB.value
    if shape != "vector":
        source = SourceChoices.CONDITIONAL.value
        metadata = {"config": conditional(metadata, shape), "concurrency": 1}
        # An untaken later branch must still be checked before any metadata leaves.
        metadata["config"].insert(
            0,
            {
                "branch_type": "if",
                "condition": "True",
                "branch_node_config": {"type": "extract_json", "config": {}},
            },
        )
    return Column.objects.create(
        name=f"saved-{uuid4().hex}",
        dataset=rows.dataset,
        source=source,
        data_type=DataTypeChoices.TEXT.value,
        metadata=metadata,
    )


def get(client, column, workspace):
    response = client.get(
        f"/model-hub/columns/{column.pk}/operation-config/",
        HTTP_X_WORKSPACE_ID=str(workspace.pk),
    )
    assert get_current_workspace() is None
    assert get_current_organization() is None
    assert get_current_user() is None
    assert not workspace_scope_is_empty()
    return response


def state():
    return snapshot(Dataset, Column, Row, Cell, SecretModel, ApiKey)


def denied(response, caplog, reference):
    assert response.status_code in {400, 403, 404}
    assert "metadata" not in response.json().get("result", {})
    assert str(reference) not in response.content.decode()
    assert str(reference) not in caplog.text
    assert "synthetic-vector-only" not in response.content.decode()
    assert "synthetic-embedding-only" not in response.content.decode()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize(
    "bad",
    [
        "other_workspace",
        "foreign_org",
        "deleted_secret",
        "missing_secret",
        "malformed_secret",
        "null_outside_default",
        "embedding_other_workspace",
        "embedding_foreign_org",
        "deleted_embedding",
    ],
)
def test_get_refuses_inaccessible_saved_references(authority_scope, caplog, shape, bad):
    data = authority_scope
    grant(data, data.b)
    grant(data, data.a)  # Access elsewhere does not widen the selected workspace.
    rows = records(data, data.b, legacy=bad == "null_outside_default")
    cfg = config(rows)
    if bad == "other_workspace":
        SecretModel._base_manager.filter(pk=rows.secret.pk).update(workspace=data.a)
    elif bad == "foreign_org":
        SecretModel._base_manager.filter(pk=rows.secret.pk).update(
            organization=data.foreign_org
        )
    elif bad == "deleted_secret":
        SecretModel._base_manager.filter(pk=rows.secret.pk).update(deleted=True)
    elif bad == "missing_secret":
        cfg["api_key"] = str(uuid4())
    elif bad == "malformed_secret":
        cfg["api_key"] = "synthetic-invalid-reference"
    elif bad == "embedding_other_workspace":
        ApiKey._base_manager.filter(pk=rows.embedding.pk).update(workspace=data.a)
    elif bad == "embedding_foreign_org":
        ApiKey._base_manager.filter(pk=rows.embedding.pk).update(
            organization=data.foreign_org
        )
    elif bad == "deleted_embedding":
        ApiKey._base_manager.filter(pk=rows.embedding.pk).update(deleted=True)
    column = saved(rows, shape, cfg)
    before = state()
    response = get(session_client(data), column, data.b)
    assert_admitted(response, data, data.b, "GetOperationConfigView")
    denied(response, caplog, cfg["api_key"])
    assert state() == before


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("selection", ["b", "default", "legacy-default"])
@pytest.mark.parametrize("viewer", [False, True])
def test_get_preserves_authorized_config_for_readers(
    authority_scope, shape, selection, viewer
):
    data = authority_scope
    workspace = data.b if selection == "b" else data.default
    grant(data, workspace, viewer=viewer)
    rows = records(data, workspace, legacy=selection == "legacy-default")
    if selection == "legacy-default":
        Dataset._base_manager.filter(pk=rows.dataset.pk).update(workspace=None)
    column = saved(rows, shape, config(rows))
    before = state()
    response = get(session_client(data), column, workspace)
    assert_admitted(response, data, workspace, "GetOperationConfigView")
    assert response.status_code == 200
    assert response.json()["result"] == {
        "column_id": str(column.pk),
        "metadata": column.metadata,
    }
    assert state() == before


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("credential", ["secret", "embedding"])
def test_get_rechecks_credential_revocation_between_reads(
    authority_scope, caplog, shape, credential
):
    data = authority_scope
    grant(data, data.b)
    rows = records(data, data.b)
    column = saved(rows, shape, config(rows))
    client = session_client(data)
    assert get(client, column, data.b).status_code == 200
    row = getattr(rows, credential)
    type(row)._base_manager.filter(pk=row.pk).update(deleted=True)
    before = state()
    response = get(client, column, data.b)
    assert_admitted(response, data, data.b, "GetOperationConfigView")
    denied(response, caplog, rows.secret.pk)
    assert state() == before


@pytest.mark.parametrize("legacy", [False, True])
def test_get_rechecks_access_after_admission(
    authority_scope, monkeypatch, caplog, legacy
):
    data = authority_scope
    workspace = data.default if legacy else data.b
    membership = grant(data, workspace)
    rows = records(data, workspace, legacy=legacy)
    column = saved(rows, "else", config(rows))
    original = dynamic_columns._request_column_queryset

    def revoke(request):
        queryset = original(request)
        WorkspaceMembership._base_manager.filter(pk=membership.pk).update(
            is_active=False
        )
        return queryset

    monkeypatch.setattr(dynamic_columns, "_request_column_queryset", revoke)
    before = state()
    response = get(session_client(data), column, workspace)
    assert_admitted(response, data, workspace, "GetOperationConfigView")
    denied(response, caplog, rows.secret.pk)
    assert state() == before


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"config": [], "concurrency": 1},
        {"json_key": "answer", "column_id": "stale-editable-source"},
        {"code": "return input", "description": "nonsecret editing data"},
        {"config": {"params": {"api_key": {"type": "Static", "value": "label"}}}},
    ],
)
def test_get_preserves_noncredential_editing_data(authority_scope, metadata):
    data = authority_scope
    grant(data, data.b, viewer=True)
    rows = records(data, data.b)
    column = saved(rows, "vector", {})
    Column._base_manager.filter(pk=column.pk).update(
        source=SourceChoices.OTHERS.value, metadata=metadata
    )
    # Config editing is not execution validation and does not require credentials.
    ApiKey._base_manager.filter(pk=rows.embedding.pk).update(deleted=True)
    SecretModel._base_manager.filter(pk=rows.secret.pk).update(deleted=True)
    before = state()
    response = get(session_client(data), column, data.b)
    assert response.status_code == 200
    assert response.json()["result"]["metadata"] == metadata
    assert state() == before


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("malformed", ["secret_object", "embedding_list", "type_list"])
def test_get_malformed_reference_shapes_are_opaque(
    authority_scope, caplog, shape, malformed
):
    data = authority_scope
    grant(data, data.b)
    rows = records(data, data.b)
    cfg = config(rows)
    if malformed == "secret_object":
        cfg["api_key"] = {"unexpected": str(rows.secret.pk)}
    elif malformed == "embedding_list":
        cfg["embedding_config"] = [str(rows.embedding.pk)]
    else:
        cfg["embedding_config"]["type"] = [str(rows.embedding.pk)]
    column = saved(rows, shape, cfg)
    before = state()
    response = get(session_client(data), column, data.b)
    denied(response, caplog, rows.secret.pk)
    assert str(rows.embedding.pk) not in response.content.decode()
    assert str(rows.embedding.pk) not in caplog.text
    assert state() == before


@pytest.mark.parametrize("carrier", ["nested_vector", "nested_branch", "typed_secret"])
def test_get_checks_nested_references_without_trusting_metadata_authority(
    authority_scope, caplog, carrier
):
    data = authority_scope
    grant(data, data.b)
    rows = records(data, data.b)
    SecretModel._base_manager.filter(pk=rows.secret.pk).update(workspace=data.a)
    cfg = config(rows)
    if carrier == "nested_vector":
        nested = {"saved": [cfg]}
    elif carrier == "nested_branch":
        nested = {"config": conditional(cfg)}
    else:
        nested = {
            "headers": {"Authorization": {"type": "Secret", "value": cfg["api_key"]}}
        }
    metadata = {
        "config": nested,
        "authority": {"workspace_id": str(data.a.pk), "actor": "SYSTEM"},
    }
    column = saved(rows, "vector", {})
    Column._base_manager.filter(pk=column.pk).update(
        source=SourceChoices.OTHERS.value, metadata=metadata
    )
    before = state()
    response = get(session_client(data), column, data.b)
    assert_admitted(response, data, data.b, "GetOperationConfigView")
    denied(response, caplog, rows.secret.pk)
    assert state() == before


@pytest.mark.parametrize("metadata", [None, [], "synthetic-invalid-config", 7])
def test_get_empty_or_malformed_root_fails_safely(authority_scope, metadata):
    data = authority_scope
    grant(data, data.b)
    rows = records(data, data.b)
    column = saved(rows, "vector", {})
    Column._base_manager.filter(pk=column.pk).update(metadata=metadata)
    before = state()
    response = get(session_client(data), column, data.b)
    assert response.status_code in {400, 403, 404}
    assert "metadata" not in response.json().get("result", {})
    assert state() == before
