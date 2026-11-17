"""F02 routed/serialized/threaded regression group; VM-only, synthetic, no egress."""

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied

from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests.a21_authority_support import (
    assert_admitted,
    grant,
    post,
    session_client,
    snapshot,
)
from model_hub.views import develop_dataset, dynamic_columns
from tfc.constants.levels import Level

pytest_plugins = [
    "model_hub.tests.a21_authority_support",
    "model_hub.tests.a21_f02_boundary_support",
]
pytestmark = pytest.mark.django_db(transaction=True)


def records(data, workspace, *, legacy=False):
    dataset = Dataset.no_workspace_objects.create(
        name=f"synthetic-{uuid4().hex}",
        organization=data.org,
        workspace=workspace,
        user=data.user,
        column_order=[],
        column_config={},
    )
    source = Column.objects.create(
        name="input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    Cell.objects.create(
        dataset=dataset, row=row, column=source, value="synthetic-input"
    )
    credential_workspace = None if legacy else workspace
    embedding = ApiKey.no_workspace_objects.create(
        organization=data.org,
        workspace=credential_workspace,
        user=data.user,
        provider="openai",
        key="synthetic-embedding-only",
    )
    secret = SecretModel.no_workspace_objects.create(
        name="synthetic-vector",
        organization=data.org,
        workspace=credential_workspace,
        key="synthetic-vector-only",
    )
    assert SecretModel._base_manager.get(pk=secret.pk).workspace_id == getattr(
        credential_workspace, "pk", None
    )
    return SimpleNamespace(
        dataset=dataset, source=source, row=row, embedding=embedding, secret=secret
    )


def config(rows, subtype="qdrant"):
    return {
        "column_id": str(rows.source.pk),
        "api_key": str(rows.secret.pk),
        "sub_type": subtype,
        "embedding_config": {"type": "openai", "model": "text-embedding-3-small"},
        "url": "https://vector.example.invalid",
        "collection_name": "synthetic",
        "index_name": "synthetic",
        "top_k": 1,
        "key": "answer",
        "vector_length": 2,
    }


def conditional(cfg, branch="else"):
    return [
        {
            "branch_type": branch,
            "condition": "False",
            "branch_node_config": {"type": "retrieval", "config": cfg},
        }
    ]


def create(client, data, rows, operation, cfg, *, branch="else", workspace=None):
    workspace = workspace or data.b
    body = {"new_column_name": f"result-{uuid4().hex}", "concurrency": 1}
    if operation == "conditional":
        suffix = "conditional-column"
        body["config"] = conditional(cfg, branch)
        view = "ConditionalColumnView"
    else:
        suffix = "add_vector_db_column"
        body.update(cfg)
        view = "AddVectorDBColumnView"
    response = post(
        client, f"/model-hub/datasets/{rows.dataset.pk}/{suffix}/", body, workspace
    )
    assert_admitted(response, data, workspace, view)
    return response


def dataset_state():
    return snapshot(Dataset, Column, Row, Cell)


@pytest.mark.parametrize(
    "operation,branch",
    [
        ("vector_db", "else"),
        ("conditional", "if"),
        ("conditional", "elif"),
        ("conditional", "else"),
    ],
)
@pytest.mark.parametrize(
    "bad",
    [
        "null",
        "other_workspace",
        "foreign_org",
        "deleted_secret",
        "missing_secret",
        "malformed_secret",
        "foreign_source",
        "deleted_source",
        "deleted_embedding",
    ],
)
def test_routed_denial_precedes_resolution_dispatch_and_writes(
    authority_scope, transport, providers, operation, branch, bad
):
    data = authority_scope
    grant(data, data.b)
    rows = records(data, data.b, legacy=bad == "null")
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
        cfg["api_key"] = "not-a-uuid"
    elif bad == "foreign_source":
        cfg["column_id"] = str(records(data, data.a).source.pk)
    elif bad == "deleted_source":
        Column._base_manager.filter(pk=rows.source.pk).update(deleted=True)
    elif bad == "deleted_embedding":
        ApiKey._base_manager.filter(pk=rows.embedding.pk).update(deleted=True)
    client = session_client(data)
    before = dataset_state()
    response = create(client, data, rows, operation, cfg, branch=branch)
    # If an envelope was admitted when it should not have been, drain it into
    # the controlled worker/thread path before the assertions run, so a wrong
    # admission shows up as synthetic credential use or data writes.
    for payload in transport.captured:
        transport.execute(payload, develop_dataset)
    assert response.status_code in {400, 403, 404}
    assert dataset_state() == before
    assert transport.captured == providers.reveals == providers.calls == []


@pytest.mark.parametrize(
    "module", [dynamic_columns, develop_dataset], ids=["canonical", "duplicate"]
)
@pytest.mark.parametrize("operation", ["conditional", "vector_db"])
@pytest.mark.parametrize("subtype", ["qdrant", "pinecone", "weaviate"])
@pytest.mark.parametrize("selection", ["b", "default", "legacy-default"])
def test_authorized_serialized_threads_and_reruns(
    authority_scope, transport, providers, module, operation, subtype, selection
):
    data = authority_scope
    legacy = selection == "legacy-default"
    workspace = data.b if selection == "b" else data.default
    grant(data, workspace)
    rows = records(data, workspace, legacy=legacy)
    if legacy:
        Dataset._base_manager.filter(pk=rows.dataset.pk).update(workspace=None)
    client = session_client(data)
    response = create(
        client, data, rows, operation, config(rows, subtype), workspace=workspace
    )
    assert response.status_code == 200
    payload = transport.captured.pop()
    assert payload.kwargs["authority"]["workspace_id"] == str(workspace.pk)
    assert payload.kwargs["authority"]["actor_id"] == str(data.user.pk)
    transport.execute(payload, module)
    destination = response.json()["result"]["new_column_id"]
    persisted = Cell._base_manager.get(column_id=destination, row=rows.row)
    assert persisted.value == "['synthetic-result']"
    assert persisted.status == "pass"
    assert {pk for _, pk in providers.reveals} == {
        str(rows.embedding.pk),
        str(rows.secret.pk),
    }
    response = post(
        client,
        f"/model-hub/columns/{destination}/rerun-operation/",
        {"operation_type": operation},
        workspace,
    )
    assert response.status_code == 200
    transport.execute(transport.captured.pop(), module)
    assert Cell._base_manager.filter(column_id=destination, row=rows.row).count() == 1
    persisted.refresh_from_db()
    assert persisted.value == "['synthetic-result']"
    assert persisted.status == "pass"
    if legacy:
        assert Dataset._base_manager.get(pk=rows.dataset.pk).workspace_id is None


@pytest.mark.parametrize("operation", ["conditional", "vector_db"])
@pytest.mark.parametrize(
    "bad",
    [
        "old_job",
        "lost_workspace",
        "revoked",
        "viewer",
        "inactive_actor",
        "missing_actor",
        "inactive_org_membership",
        "deleted_workspace",
        "inactive_workspace",
        "foreign_workspace",
        "deleted_dataset",
        "moved_dataset",
        "deleted_source",
        "moved_destination",
        "deleted_secret",
    ],
)
def test_stale_job_refuses_before_status_or_credential_effects(
    authority_scope, transport, providers, operation, bad
):
    data = authority_scope
    membership = grant(data, data.b)
    rows = records(data, data.b)
    response = create(session_client(data), data, rows, operation, config(rows))
    assert response.status_code == 200
    payload = copy.deepcopy(transport.captured.pop())
    destination = response.json()["result"]["new_column_id"]
    if bad == "old_job":
        payload.kwargs.pop("authority", None)
    elif bad == "lost_workspace":
        payload.kwargs["authority"]["workspace_id"] = None
    elif bad == "revoked":
        WorkspaceMembership._base_manager.filter(pk=membership.pk).update(
            is_active=False
        )
    elif bad == "viewer":
        WorkspaceMembership._base_manager.filter(pk=membership.pk).update(
            level=Level.WORKSPACE_VIEWER
        )
    elif bad == "inactive_actor":
        User.objects.filter(pk=data.user.pk).update(is_active=False)
    elif bad == "missing_actor":
        payload.kwargs["authority"]["actor_id"] = str(uuid4())
    elif bad == "inactive_org_membership":
        type(data.membership)._base_manager.filter(pk=data.membership.pk).update(
            is_active=False
        )
    elif bad == "deleted_workspace":
        Workspace._base_manager.filter(pk=data.b.pk).update(deleted=True)
    elif bad == "inactive_workspace":
        Workspace._base_manager.filter(pk=data.b.pk).update(is_active=False)
    elif bad == "foreign_workspace":
        Workspace._base_manager.filter(pk=data.b.pk).update(
            organization=data.foreign_org
        )
    elif bad == "deleted_dataset":
        Dataset._base_manager.filter(pk=rows.dataset.pk).update(deleted=True)
    elif bad == "moved_dataset":
        Dataset._base_manager.filter(pk=rows.dataset.pk).update(workspace=data.a)
    elif bad == "deleted_source":
        Column._base_manager.filter(pk=rows.source.pk).update(deleted=True)
    elif bad == "moved_destination":
        Column._base_manager.filter(pk=destination).update(
            dataset=records(data, data.a).dataset
        )
    elif bad == "deleted_secret":
        SecretModel._base_manager.filter(pk=rows.secret.pk).update(deleted=True)
    before = dataset_state()
    with pytest.raises(PermissionDenied):
        transport.execute(payload, develop_dataset)
    assert dataset_state() == before
    assert providers.reveals == providers.calls == []


@pytest.mark.parametrize("operation", ["conditional", "vector_db"])
@pytest.mark.parametrize("entry", ["preview", "rerun"])
def test_preview_and_rerun_override_validate_all_nested_references(
    authority_scope, transport, providers, operation, entry
):
    data = authority_scope
    grant(data, data.b)
    rows = records(data, data.b)
    client = session_client(data)
    cfg = config(rows)
    if entry == "rerun":
        response = create(client, data, rows, operation, cfg)
        assert response.status_code == 200
        transport.captured.clear()
        destination = response.json()["result"]["new_column_id"]
        path = f"/model-hub/columns/{destination}/rerun-operation/"
    else:
        path = f"/model-hub/datasets/{rows.dataset.pk}/preview/{operation}/"
    cfg["api_key"] = str(records(data, data.a).secret.pk)
    effective = {"config": conditional(cfg)} if operation == "conditional" else cfg
    body = (
        {"operation_type": operation, "config": effective}
        if entry == "rerun"
        else effective
    )
    before = dataset_state()
    response = post(client, path, body, data.b)
    assert response.status_code in {400, 403, 404}
    assert dataset_state() == before
    assert transport.captured == providers.reveals == providers.calls == []


@pytest.mark.parametrize(
    "name", ["conditional_column_async", "add_vector_db_column_async"]
)
def test_duplicate_activity_envelopes_preserve_old_argument_binding(name):
    import inspect

    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

    canonical = getattr(dynamic_columns, name)
    duplicate = getattr(develop_dataset, name)
    signature = inspect.signature(canonical._original_func)
    assert signature == inspect.signature(duplicate._original_func)
    assert signature.parameters["authority"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["authority"].default is None
    old_count = 5 if name == "conditional_column_async" else 8
    # Old positional envelopes bind, then fail closed for absent authority;
    # they must not reinterpret is_rerun as an actor/workspace argument.
    bound = signature.bind(*([None] * (old_count - 1)), True)
    bound.apply_defaults()
    assert bound.arguments["is_rerun"] is True
    assert bound.arguments["authority"] is None
    assert _ACTIVITY_REGISTRY[name]["func"] in {
        canonical._original_func,
        duplicate._original_func,
    }


@pytest.mark.parametrize("module", [dynamic_columns, develop_dataset])
def test_direct_helper_cannot_borrow_ambient_or_null_scope(
    authority_scope, providers, module
):
    data = authority_scope
    grant(data, data.b)
    rows = records(data, data.b, legacy=True)
    with pytest.raises(PermissionDenied):
        module.AddVectorDBColumnView()._query_vector_db(
            "synthetic", config(rows), data.org.pk
        )
    assert providers.reveals == providers.calls == []
