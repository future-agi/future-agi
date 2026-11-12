"""Copy a dataset between workspaces of one organization, with a preview.

A move is refused (409) after the same authorization checks as a copy.
"""

import json
import threading
import uuid

import pytest
from django.db import connection, connections, transaction
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.dataset_folders import DatasetFolder
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

LIST = "/model-hub/develops/get-datasets/"
PASSWORD = "synthetic-transfer-password"
# The owner that _tenant creates signs in with this password.
OWNER_PASSWORD = "l1-test-password"


def url(dataset):
    return f"/model-hub/datasets/{dataset.id}/transfer/"


def counts():
    return (
        Dataset.no_workspace_objects.count(),
        Column.no_workspace_objects.count(),
        Row.no_workspace_objects.count(),
        Cell.no_workspace_objects.count(),
    )


def listed_ids(client):
    response = client.get(LIST, {"page_size": 100})
    assert response.status_code == 200, response.content
    return {row["id"] for row in response.data["result"]["datasets"]}


def sibling(tenant, label, **fields):
    return Workspace.no_workspace_objects.create(
        name=f"transfer-{label}-{uuid.uuid4().hex[:6]}",
        organization=tenant["org"],
        is_default=False,
        is_active=fields.pop("is_active", True),
        created_by=tenant["user"],
    )


def member(tenant, levels):
    """A non-admin organization member with the given workspace levels."""
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"transfer-{uuid.uuid4().hex}@example.test",
        password=PASSWORD,
        name="Transfer member",
        organization=tenant["org"],
        organization_role=OrganizationRoles.MEMBER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=tenant["org"],
        defaults={
            "role": OrganizationRoles.MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
        },
    )
    for workspace, level in levels:
        WorkspaceMembership.no_workspace_objects.get_or_create(
            user=user,
            workspace=workspace,
            defaults={
                "role": (
                    OrganizationRoles.WORKSPACE_VIEWER
                    if level == Level.WORKSPACE_VIEWER
                    else OrganizationRoles.WORKSPACE_MEMBER
                ),
                "level": level,
                "is_active": True,
                "organization_membership": membership,
            },
        )
    return user


def signed_in(user, tenant, password=PASSWORD):
    """Real JWT authentication, including the workspace write guard."""
    client = APIClient(HTTP_HOST="localhost")
    login = client.post(
        "/accounts/token/", {"email": user.email, "password": password}, format="json"
    )
    assert login.status_code == 200, login.content
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(tenant["org"].id),
        HTTP_X_WORKSPACE_ID=str(tenant["workspace"].id),
    )
    return client


@pytest.fixture
def actors(db, settings):
    # The copy and move contracts exercise the enabled implementation; the
    # default-OFF gate is pinned at the end of this file.
    settings.DSX_DATASET_TRANSFER_ENABLED = True
    a, b = _tenant("transfer-a"), _tenant("transfer-b")
    target = sibling(a, "target")
    generated = Column.no_workspace_objects.create(
        name="answer",
        data_type=DataTypeChoices.TEXT.value,
        dataset=a["dataset"],
        source=SourceChoices.RUN_PROMPT.value,
    )
    Cell.no_workspace_objects.create(
        dataset=a["dataset"], row=a["rows"][0], column=generated, value="generated"
    )
    Dataset.no_workspace_objects.filter(pk=a["dataset"].pk).update(
        column_order=[str(a["column"].id), str(generated.id)],
        column_config={str(a["column"].id): {"is_visible": True}},
    )
    a["dataset"].refresh_from_db()
    clients = [_client(a), _client(b)]
    yield a, b, target, clients
    for client in clients:
        client.stop_workspace_injection()
    clear_workspace_context()


def post(client, dataset, target, mode, **body):
    return client.post(
        url(dataset),
        {"to_workspace_id": str(target.id), "mode": mode, **body},
        format="json",
    )


# What every move answers once the caller may change the dataset and add
# datasets to the target.
MOVE_REFUSED = "Datasets can't be moved between workspaces. Copy it instead."


def queue_in(tenant):
    """An annotation queue of the tenant's workspace (not the dataset's own)."""
    return AnnotationQueue.objects.create(
        name=f"transfer-rows-{uuid.uuid4().hex[:6]}",
        organization=tenant["org"],
        workspace=tenant["workspace"],
        created_by=tenant["user"],
        status="active",
    )


def queue_item(queue, tenant, row):
    return QueueItem.objects.create(
        queue=queue,
        organization=tenant["org"],
        workspace=tenant["workspace"],
        source_type="dataset_row",
        dataset_row=row,
    )


@pytest.mark.django_db
def test_preview_describes_a_copy_and_refuses_a_move_without_writing(actors):
    a, _b, target, (client, _) = actors
    folder = DatasetFolder.objects.create(
        path="inbox", organization=a["org"], workspace=a["workspace"]
    )
    Dataset.no_workspace_objects.filter(pk=a["dataset"].pk).update(folder=folder)
    before = counts()
    copy = post(client, a["dataset"], target, "copy", dry_run=True)
    assert copy.status_code == 200, copy.content
    assert copy.data["result"] == {
        "mode": "copy",
        "dry_run": True,
        "source_dataset_id": str(a["dataset"].id),
        "dataset_id": None,
        "dataset_name": f"Copy of {a['dataset'].name}",
        "from_workspace": {"id": str(a["workspace"].id), "name": a["workspace"].name},
        "to_workspace": {"id": str(target.id), "name": target.name},
        "plan": {
            "rows": 2,
            "columns": 1,
            "columns_left_out": 1,
            "leaves_folder": False,
        },
    }
    move = post(client, a["dataset"], target, "move", dry_run=True)
    assert move.status_code == 409, move.content
    assert move.data["result"] == MOVE_REFUSED
    assert counts() == before
    a["dataset"].refresh_from_db()
    assert a["dataset"].workspace_id == a["workspace"].id
    assert a["dataset"].folder_id == folder.id


@pytest.mark.django_db
def test_copy_persists_rows_and_input_columns_in_the_target(actors):
    a, _b, target, (client, _) = actors
    response = post(
        client, a["dataset"], target, "copy", new_dataset_name="  moved copy "
    )
    assert response.status_code == 200, response.content
    result = response.data["result"]
    assert result["dry_run"] is False and result["dataset_name"] == "moved copy"
    copy = Dataset.no_workspace_objects.get(pk=result["dataset_id"])
    assert (copy.organization_id, copy.workspace_id, copy.user_id) == (
        a["org"].id,
        target.id,
        a["user"].id,
    )
    columns = list(Column.no_workspace_objects.filter(dataset=copy))
    assert [(c.name, c.source) for c in columns] == [("question", "OTHERS")]
    assert copy.column_order == [str(columns[0].id)]
    assert copy.column_config == {str(columns[0].id): {"is_visible": True}}
    rows = list(Row.no_workspace_objects.filter(dataset=copy).order_by("order"))
    assert [r.order for r in rows] == [0, 1]
    assert sorted(
        Cell.no_workspace_objects.filter(dataset=copy).values_list("value", flat=True)
    ) == ["transfer-a-value-0", "transfer-a-value-1"]
    assert not {c.row_id for c in a["cells"]} & {r.id for r in rows}
    a["dataset"].refresh_from_db()
    assert a["dataset"].workspace_id == a["workspace"].id
    assert Row.no_workspace_objects.filter(dataset=a["dataset"]).count() == 2
    assert result["dataset_id"] not in listed_ids(client)
    client.set_workspace(target)
    assert result["dataset_id"] in listed_ids(client)
    client.set_workspace(a["workspace"])
    before = counts()
    again = post(client, a["dataset"], target, "copy", new_dataset_name="moved copy")
    assert again.status_code == 400, again.content
    assert counts() == before


@pytest.mark.django_db
@pytest.mark.parametrize("used_by", ["nothing", "experiment", "queue item"])
def test_every_move_is_refused_and_leaves_the_dataset_where_it_is(actors, used_by):
    # Nothing coordinates the writers that add references to a dataset's rows,
    # columns and cells (annotation queue items, scores, split memberships and
    # so on) with a move, so a move is refused whether or not anything uses
    # the dataset yet: a preview and a real move alike, writing nothing.
    a, _b, target, (client, _) = actors
    folder = DatasetFolder.objects.create(
        path="inbox", organization=a["org"], workspace=a["workspace"]
    )
    Dataset.no_workspace_objects.filter(pk=a["dataset"].pk).update(folder=folder)
    if used_by == "experiment":
        ExperimentsTable.no_workspace_objects.create(
            name="transfer-uses",
            dataset=a["dataset"],
            user=a["user"],
            status="Completed",
        )
    elif used_by == "queue item":
        queue_item(queue_in(a), a, a["rows"][0])
    clear_workspace_context()
    before = counts()
    for dry_run in (True, False):
        refused = post(client, a["dataset"], target, "move", dry_run=dry_run)
        assert refused.status_code == 409, refused.content
        assert refused.data["result"] == MOVE_REFUSED
        assert refused.data["code"] == "dataset_transfer_conflict"
    assert counts() == before
    a["dataset"].refresh_from_db()
    assert (a["dataset"].workspace_id, a["dataset"].folder_id) == (
        a["workspace"].id,
        folder.id,
    )
    assert str(a["dataset"].id) in listed_ids(client)
    # Into its own workspace it is the same 400 as a copy.
    same = post(client, a["dataset"], a["workspace"], "move", dry_run=True)
    assert same.status_code == 400, same.content
    copied = post(client, a["dataset"], target, "copy")
    assert copied.status_code == 200, copied.content


def hold_open(write):
    """Run ``write`` on a second database connection, keeping its transaction
    open until ``release`` is set. Returns (thread, written, release, result)."""
    written, release, result = threading.Event(), threading.Event(), {}

    def run():
        clear_workspace_context()
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    result["pid"] = cursor.fetchone()[0]
                result["value"] = write()
                written.set()
                if not release.wait(30):
                    raise TimeoutError("the open transaction was never released")
        except Exception as exc:  # reported through result, asserted by the test
            result["error"] = repr(exc)
        finally:
            written.set()
            connections.close_all()
            clear_workspace_context()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, written, release, result


@pytest.mark.django_db(transaction=True)
def test_a_move_cannot_strand_a_queue_item_written_while_it_runs(actors):
    # Two connections, interleaved the way the add-items path and a move can
    # be: add-items has inserted a QueueItem for one of the dataset's rows and
    # not committed yet. That insert never touches the dataset row
    # (QueueItem.dataset_row references Row), so no lock a move takes on the
    # dataset waits for it and no check a move runs can see it; a move would
    # re-home the dataset and the item would then commit in this workspace,
    # pointing at a row this workspace can no longer see. The move is refused.
    if connection.vendor != "postgresql":
        pytest.skip("two PostgreSQL connections are required")
    a, _b, target, (client, _) = actors
    queue = queue_in(a)
    clear_workspace_context()
    row = a["rows"][0]
    thread, written, release, result = hold_open(lambda: queue_item(queue, a, row).pk)
    try:
        assert written.wait(10), result
        assert "error" not in result, result
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            assert cursor.fetchone()[0] != result["pid"]
        # The other connection's insert is still open, so invisible here.
        assert not QueueItem._base_manager.filter(pk=result["value"]).exists()
        moved = post(client, a["dataset"], target, "move")
    finally:
        release.set()
        thread.join(20)
    assert not thread.is_alive(), result
    assert "error" not in result, result
    assert moved.status_code == 409, moved.content
    assert moved.data["result"] == MOVE_REFUSED
    item = QueueItem._base_manager.get(pk=result["value"])
    dataset = Dataset.no_workspace_objects.get(pk=a["dataset"].pk)
    assert item.dataset_row_id == row.id
    # The committed item, its queue and the row's dataset share one workspace.
    assert (dataset.workspace_id, item.workspace_id, queue.workspace_id) == (
        a["workspace"].id,
        a["workspace"].id,
        a["workspace"].id,
    )


@pytest.mark.django_db
def test_foreign_inactive_and_unwritable_targets_are_refused(actors):
    a, b, target, (client, foreign) = actors
    inactive = sibling(a, "inactive", is_active=False)
    before = counts()
    for mode in ("copy", "move"):
        # Another organization can neither see the dataset nor receive it.
        assert post(foreign, a["dataset"], b["workspace"], mode).status_code == 404
        assert post(client, a["dataset"], b["workspace"], mode).status_code == 404
        assert post(client, a["dataset"], inactive, mode).status_code == 404
        assert post(client, b["dataset"], target, mode).status_code == 404
    writer = member(
        a, [(a["workspace"], Level.WORKSPACE_MEMBER), (target, Level.WORKSPACE_VIEWER)]
    )
    viewer = member(
        a, [(a["workspace"], Level.WORKSPACE_VIEWER), (target, Level.WORKSPACE_MEMBER)]
    )
    try:
        writer_client = signed_in(writer, a)
        for mode in ("copy", "move"):
            for dry_run in (True, False):
                denied = post(
                    writer_client, a["dataset"], target, mode, dry_run=dry_run
                )
                assert denied.status_code == 403, denied.content
                denied = post(
                    signed_in(viewer, a), a["dataset"], target, mode, dry_run=dry_run
                )
                assert denied.status_code == 403, denied.content
    finally:
        clear_workspace_context()
    assert counts() == before
    a["dataset"].refresh_from_db()
    assert a["dataset"].workspace_id == a["workspace"].id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"mode": "clone"},
        {"mode": "copy", "unexpected": True},
        {"mode": "copy", "new_dataset_name": "   "},
        {"mode": "move", "new_dataset_name": "renamed"},
        {"mode": "copy", "to_workspace_id": "not-a-uuid"},
        {"mode": "copy", "to_workspace_id": None},
    ],
)
def test_invalid_requests_fail_closed(actors, body):
    a, _b, target, (client, _) = actors
    before = counts()
    response = client.post(
        url(a["dataset"]), {"to_workspace_id": str(target.id), **body}, format="json"
    )
    assert response.status_code == 400, response.content
    same = post(client, a["dataset"], a["workspace"], "copy", dry_run=True)
    assert same.status_code == 400, same.content
    assert counts() == before


# Default OFF: the route answers 404 to every caller, method and body, exactly
# like a route that does not exist. The gate is the view's first step, before
# DRF authenticates (APIKeyAuthentication refuses a viewer's POST there, before
# any permission check), before the validated_request body checks and before
# the transaction around post().

ABSENT = object()
# "default" keeps the value settings.py loaded, so it must come first; only an
# actual True turns the route on (the gate compares with ``is True``).
OFF_VALUES = ["default", ABSENT, False, None, 0, 1, "true", "True", "1"]
OFF_IDS = ["default", "absent", "false", "none", "zero", "one", "str-true"]
OFF_IDS += ["str-True", "str-1"]


def set_switch(settings, value):
    if value is ABSENT:
        if hasattr(settings, "DSX_DATASET_TRANSFER_ENABLED"):
            del settings.DSX_DATASET_TRANSFER_ENABLED
    elif value != "default":
        settings.DSX_DATASET_TRANSFER_ENABLED = value


def charged():
    try:
        from ee.usage.models.usage import APICallLog
    except ImportError:
        return None
    return APICallLog.no_workspace_objects.count()


def stored(tenant):
    tenant["dataset"].refresh_from_db()
    return (
        counts(),
        charged(),
        tenant["dataset"].workspace_id,
        tenant["dataset"].folder_id,
        tenant["dataset"].name,
    )


@pytest.fixture
def callers(db):
    """Every kind of caller, signed in for real (no force_authenticate)."""
    a, b = _tenant("transfer-off-a"), _tenant("transfer-off-b")
    target = sibling(a, "off-target")
    writer = member(
        a, [(a["workspace"], Level.WORKSPACE_MEMBER), (target, Level.WORKSPACE_MEMBER)]
    )
    viewer = member(
        a, [(a["workspace"], Level.WORKSPACE_VIEWER), (target, Level.WORKSPACE_MEMBER)]
    )
    try:
        clients = {
            "owner": signed_in(a["user"], a, password=OWNER_PASSWORD),
            "writer": signed_in(writer, a),
            "viewer": signed_in(viewer, a),
            "foreign": signed_in(b["user"], b, password=OWNER_PASSWORD),
            "anonymous": APIClient(HTTP_HOST="localhost"),
            "invalid-token": APIClient(HTTP_HOST="localhost"),
        }
        clients["invalid-token"].credentials(
            HTTP_AUTHORIZATION="Bearer not-a-valid-token",
            HTTP_X_ORGANIZATION_ID=str(a["org"].id),
            HTTP_X_WORKSPACE_ID=str(a["workspace"].id),
        )
        yield {"a": a, "b": b, "target": target, "clients": clients}
    finally:
        clear_workspace_context()


def off_requests(dataset, target):
    body = {"to_workspace_id": str(target.id)}
    yield "POST", {**body, "mode": "copy", "dry_run": True}
    yield "POST", {**body, "mode": "copy"}
    yield "POST", {**body, "mode": "copy", "new_dataset_name": "off copy"}
    yield "POST", {**body, "mode": "move", "dry_run": True}
    yield "POST", {**body, "mode": "move"}
    yield "POST", {**body, "mode": "clone", "unexpected": True}
    yield "POST", "{"
    yield "POST", ""
    for method in ("GET", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
        yield method, {**body, "mode": "move"}


def send(client, method, dataset, body):
    raw = body if isinstance(body, str) else json.dumps(body)
    return client.generic(method, url(dataset), raw, content_type="application/json")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role", ["owner", "writer", "viewer", "foreign", "anonymous", "invalid-token"]
)
def test_off_refuses_every_caller_and_body_like_an_absent_route(
    callers, settings, role
):
    a, target = callers["a"], callers["target"]
    client = callers["clients"][role]
    before = stored(a)
    for value in OFF_VALUES:
        set_switch(settings, value)
        if value == "default":
            # The shipped default, read from the deployment environment.
            assert settings.DSX_DATASET_TRANSFER_ENABLED is False
        for method, body in off_requests(a["dataset"], target):
            response = send(client, method, a["dataset"], body)
            where = (role, value, method, body)
            assert response.status_code == 404, (where, response.content)
            assert a["dataset"].name.encode() not in response.content, where
            assert target.name.encode() not in response.content, where
            assert stored(a) == before, where


@pytest.mark.django_db
def test_switch_is_read_per_request_and_on_keeps_every_guard(callers, settings):
    a, b, target = callers["a"], callers["b"], callers["target"]
    clients = callers["clients"]
    before = stored(a)
    preview = {"to_workspace_id": str(target.id), "mode": "copy", "dry_run": True}
    for enabled in (False, True, False, True):
        settings.DSX_DATASET_TRANSFER_ENABLED = enabled
        owner = send(clients["owner"], "POST", a["dataset"], preview)
        assert owner.status_code == (200 if enabled else 404), owner.content
        writer = send(clients["writer"], "POST", a["dataset"], preview)
        assert writer.status_code == (200 if enabled else 404), writer.content
        # The same viewer request is 404 while OFF and the authentication
        # write guard's 403 while ON: the gate runs before authentication,
        # and ON adds no write exemption.
        viewer = send(clients["viewer"], "POST", a["dataset"], preview)
        assert viewer.status_code == (403 if enabled else 404), viewer.content
        foreign = send(
            clients["foreign"],
            "POST",
            a["dataset"],
            {**preview, "to_workspace_id": str(b["workspace"].id)},
        )
        assert foreign.status_code == 404, foreign.content
        for anonymous in ("anonymous", "invalid-token"):
            refused = send(clients[anonymous], "POST", a["dataset"], preview)
            assert refused.status_code == (401 if enabled else 404), refused.content
        assert stored(a) == before


@pytest.mark.unit
@pytest.mark.parametrize("value", OFF_VALUES, ids=OFF_IDS)
def test_off_gate_precedes_authentication_validation_and_transaction(
    settings, monkeypatch, value
):
    from model_hub.serializers.dataset_transfer import (
        DatasetTransferRequestSerializer,
    )
    from model_hub.services import dataset_transfer as transfers
    from model_hub.views.dataset_transfer import DatasetTransferView

    set_switch(settings, value)
    if value == "default":
        assert settings.DSX_DATASET_TRANSFER_ENABLED is False

    def unexpected(*args, **kwargs):
        raise AssertionError(
            "OFF must not authenticate, negotiate, validate, open a transaction "
            "or prepare a transfer"
        )

    monkeypatch.setattr(APIView, "initial", unexpected)
    monkeypatch.setattr(DatasetTransferRequestSerializer, "__init__", unexpected)
    monkeypatch.setattr(transaction.Atomic, "__enter__", unexpected)
    monkeypatch.setattr(transfers, "prepare", unexpected)
    dataset_id = uuid.uuid4()
    request = APIRequestFactory().post(
        f"/model-hub/datasets/{dataset_id}/transfer/",
        "{",
        content_type="application/json",
        HTTP_ACCEPT="application/unsupported",
        HTTP_AUTHORIZATION="Bearer not-a-valid-token",
    )
    response = DatasetTransferView.as_view()(request, dataset_id=dataset_id)
    assert response.status_code == 404


@pytest.mark.unit
@pytest.mark.parametrize(
    "method", ["POST", "GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
)
def test_absent_switch_refuses_every_method(settings, method):
    from model_hub.views.dataset_transfer import DatasetTransferView

    set_switch(settings, ABSENT)
    dataset_id = uuid.uuid4()
    request = APIRequestFactory().generic(
        method, f"/model-hub/datasets/{dataset_id}/transfer/"
    )
    response = DatasetTransferView.as_view()(request, dataset_id=dataset_id)
    assert response.status_code == 404
