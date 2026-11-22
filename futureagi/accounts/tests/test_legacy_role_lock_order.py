"""Concurrent legacy role grants in two organizations must not deadlock.

The same actor A manages the same target B in organizations X and Y. Their
membership primary keys sort A-before-B in X and B-before-A in Y, so ordering
the locked rows by membership pk alone gives the shared User rows opposite
orders. A third transaction pins B's membership in X so that X's locking query
is provably mid-way (User A held) before Y starts; releasing it then closes the
cycle unless every shared row is locked in one global order.
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from socketserver import ThreadingMixIn
from threading import Event, Thread
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest
from django.db import close_old_connections, connection, connections, transaction

from accounts.models import User
from accounts.models.auth_token import AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.legacy_role_assignments import assign_role
from accounts.tests.test_workspace_role_assignments import credential
from ai_tools.base import ToolContext
from ai_tools.registry import registry
from tfc.constants.levels import Level

pytestmark = [pytest.mark.integration]


def _membership(pk, user, org, ws, level):
    membership = OrganizationMembership.no_workspace_objects.create(
        id=pk,
        user=user,
        organization=org,
        role=Level.to_org_string(level),
        level=level,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=ws,
        organization_membership=membership,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    return membership


@pytest.fixture
def crossed_orgs(db):
    x = Organization.objects.create(name="lock-order-x")
    y = Organization.objects.create(name="lock-order-y")
    actor = User.objects.create_user(
        email=f"lock-actor-{uuid.uuid4().hex}@example.test",
        password="Unique-Lock-Pass-978!",
        organization=x,
        organization_role="Admin",
    )
    target = User.objects.create_user(
        email=f"lock-target-{uuid.uuid4().hex}@example.test",
        password="Unique-Lock-Pass-978!",
        organization=x,
        organization_role="Member",
    )
    wx = Workspace.no_workspace_objects.create(
        name="lock-wx", organization=x, created_by=actor
    )
    wy = Workspace.no_workspace_objects.create(
        name="lock-wy", organization=y, created_by=actor
    )
    pks = [uuid.UUID(int=n) for n in (1, 2, 3, 4)]
    # X: actor's membership sorts first. Y: target's membership sorts first.
    _membership(pks[0], actor, x, wx, Level.ADMIN)
    pinned = _membership(pks[1], target, x, wx, Level.MEMBER)
    _membership(pks[2], target, y, wy, Level.MEMBER)
    _membership(pks[3], actor, y, wy, Level.ADMIN)
    return {
        "actor": actor,
        "target": target,
        "x": (x, wx),
        "y": (y, wy),
        "pinned": pinned.pk,
    }


def _lock_waiters():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        return cursor.fetchone()[0]


def _wait_for_waiters(count, timeout=10, done=lambda: False):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _lock_waiters() >= count or done():
            return
        time.sleep(0.05)
    raise AssertionError(f"expected {count} lock waiters, saw {_lock_waiters()}")


def _in_thread(fn):
    def run():
        try:
            return fn()
        finally:
            connections.close_all()

    return run


def _crossed_grants(crossed_orgs, grant):
    """Run grant(org, ws) for X then Y with X provably holding User A first."""
    locked, release = Event(), Event()

    def pin_target_membership_in_x():
        with transaction.atomic():
            OrganizationMembership.no_workspace_objects.select_for_update().get(
                pk=crossed_orgs["pinned"]
            )
            locked.set()
            assert release.wait(30)

    with ThreadPoolExecutor(max_workers=3) as pool:
        blocker = pool.submit(_in_thread(pin_target_membership_in_x))
        assert locked.wait(10)
        in_x = pool.submit(_in_thread(lambda: grant(*crossed_orgs["x"])))
        _wait_for_waiters(1)  # X holds its first rows and waits on the pin
        in_y = pool.submit(_in_thread(lambda: grant(*crossed_orgs["y"])))
        # Y either waits on a row X holds or, when no row is shared, finishes.
        _wait_for_waiters(2, done=in_y.done)
        release.set()
        blocker.result(30)
        return in_x.result(30), in_y.result(30)


def _assert_both_granted(crossed_orgs, outcomes):
    assert outcomes == ("ok", "ok"), outcomes
    for org_key in ("x", "y"):
        assert (
            WorkspaceMembership.no_workspace_objects.get(
                user=crossed_orgs["target"], workspace=crossed_orgs[org_key][1]
            ).level
            == Level.WORKSPACE_ADMIN
        )


@pytest.mark.django_db(transaction=True)
def test_service_grants_in_two_orgs_with_crossed_membership_order(crossed_orgs):
    def grant(org, ws):
        try:
            assign_role(
                actor=crossed_orgs["actor"],
                organization=org,
                target_user_id=crossed_orgs["target"].pk,
                role="workspace_admin",
                workspace_id=ws.pk,
            )
            return "ok"
        except Exception as exc:  # noqa: BLE001 - the failure mode under test
            return f"{type(exc).__name__}: {exc}".splitlines()[0]

    _assert_both_granted(crossed_orgs, _crossed_grants(crossed_orgs, grant))


@pytest.mark.django_db(transaction=True)
def test_registered_tool_grants_in_two_orgs_with_crossed_membership_order(
    crossed_orgs,
):
    tool = registry.get("add_workspace_member")

    def grant(org, ws):
        result = tool.run(
            {
                "workspace_id": str(ws.pk),
                "user_id": str(crossed_orgs["target"].pk),
                "role": "workspace_admin",
            },
            ToolContext(user=crossed_orgs["actor"], organization=org, workspace=ws),
        )
        return f"error: {result.content}".splitlines()[0] if result.is_error else "ok"

    _assert_both_granted(crossed_orgs, _crossed_grants(crossed_orgs, grant))


class _ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


@pytest.fixture
def threaded_http(settings):
    from django.core.handlers.wsgi import WSGIHandler

    settings.ALLOWED_HOSTS = ["testserver", "127.0.0.1"]

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    application = WSGIHandler()

    def app(environ, start_response):
        try:
            return application(environ, start_response)
        finally:
            close_old_connections()
            connections.close_all()

    server = make_server(
        "127.0.0.1", 0, app, server_class=_ThreadingServer, handler_class=QuietHandler
    )
    worker = Thread(target=server.serve_forever)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(10)


@pytest.mark.django_db(transaction=True)
def test_actual_http_grants_in_two_orgs_with_crossed_membership_order(
    crossed_orgs, threaded_http
):
    import httpx

    _, token = credential(crossed_orgs["actor"], AuthTokenType.ACCESS)

    def grant(org, ws):
        with httpx.Client(
            base_url=threaded_http, trust_env=False, timeout=30
        ) as client:
            response = client.post(
                "/accounts/user/role/update/",
                headers={
                    "Authorization": "Bearer " + token,
                    "X-Organization-Id": str(org.pk),
                    "X-Workspace-Id": str(ws.pk),
                },
                json={
                    "user_id": str(crossed_orgs["target"].pk),
                    "workspace_id": str(ws.pk),
                    "new_role": "workspace_admin",
                },
            )
        return (
            "ok"
            if response.status_code == 200
            else f"{response.status_code}: {response.text[:200]}"
        )

    _assert_both_granted(crossed_orgs, _crossed_grants(crossed_orgs, grant))


@pytest.fixture
def one_org(db):
    org = Organization.objects.create(name="lock-order-same-org")
    users = []
    for role in ("Admin", "Member"):
        users.append(
            User.objects.create_user(
                email=f"lock-{role.lower()}-{uuid.uuid4().hex}@example.test",
                password="Unique-Lock-Pass-978!",
                organization=org,
                organization_role=role,
            )
        )
    ws = Workspace.no_workspace_objects.create(
        name="lock-same-ws", organization=org, created_by=users[0]
    )
    _membership(uuid.UUID(int=11), users[0], org, ws, Level.ADMIN)
    target_m = _membership(uuid.UUID(int=12), users[1], org, ws, Level.MEMBER)
    return {
        "org": org,
        "ws": ws,
        "actor": users[0],
        "target": users[1],
        "target_m": target_m,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("canonical", ["role_update", "deactivation"])
def test_same_org_grant_while_canonical_writer_holds_target_membership(
    one_org, canonical
):
    """update_member_role and member deactivation write the target's
    membership first and its User row afterwards. A concurrent legacy grant in
    the same organization must not hold that User row while it waits."""
    locked, proceed = Event(), Event()
    target = one_org["target"]

    def canonical_writer():
        with transaction.atomic():
            # The canonical writer's order: membership row, then User row.
            fields = {"level": Level.ADMIN, "role": "Admin"}
            if canonical == "deactivation":
                fields = {"is_active": True}
            OrganizationMembership.no_workspace_objects.filter(
                pk=one_org["target_m"].pk
            ).update(**fields)
            locked.set()
            assert proceed.wait(30)
            User.objects.filter(pk=target.pk).update(
                organization_role="Admin" if canonical == "role_update" else "Member"
            )
        return "ok"

    def grant():
        try:
            assign_role(
                actor=one_org["actor"],
                organization=one_org["org"],
                target_user_id=target.pk,
                role="workspace_admin",
                workspace_id=one_org["ws"].pk,
            )
            return "ok"
        except Exception as exc:  # noqa: BLE001 - the failure mode under test
            return f"{type(exc).__name__}: {exc}".splitlines()[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(_in_thread(canonical_writer))
        assert locked.wait(10)
        helper = pool.submit(_in_thread(grant))
        _wait_for_waiters(1)  # the grant waits on the target membership
        proceed.set()
        outcomes = []
        for future in (writer, helper):
            try:
                outcomes.append(future.result(30))
            except Exception as exc:  # noqa: BLE001
                outcomes.append(f"{type(exc).__name__}: {exc}".splitlines()[0])
    # The writer promoted the target to Admin, so the Admin actor may then be
    # refused by the hierarchy; what must never happen is a deadlock abort.
    assert not any("deadlock" in str(outcome) for outcome in outcomes), outcomes
    assert outcomes[0] == "ok", outcomes


@pytest.mark.django_db(transaction=True)
def test_refused_grant_does_not_lock_a_foreign_user_row(crossed_orgs):
    """A grant naming a user outside the organization is refused without ever
    taking a row lock on that user's User row."""
    from rest_framework.exceptions import NotFound

    outsider = User.objects.create_user(
        email=f"lock-outsider-{uuid.uuid4().hex}@example.test",
        password="Unique-Lock-Pass-978!",
        organization=Organization.objects.create(name="lock-order-foreign"),
        organization_role="Owner",
    )
    org, ws = crossed_orgs["x"]
    observed = {}
    real = OrganizationMembership.no_workspace_objects.select_for_update

    def membership_query(*args, **kwargs):
        # Runs on the grant's connection inside its transaction: is the
        # outsider's User row locked by now (from another connection)?
        def probe():
            try:
                with transaction.atomic():
                    User.objects.select_for_update(nowait=True).get(pk=outsider.pk)
                return False
            except Exception:  # noqa: BLE001 - lock not available
                return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            observed["locked"] = pool.submit(_in_thread(probe)).result(10)
        return real(*args, **kwargs)

    from unittest import mock

    with mock.patch.object(
        OrganizationMembership.no_workspace_objects,
        "select_for_update",
        side_effect=membership_query,
    ):
        with pytest.raises(NotFound):
            assign_role(
                actor=crossed_orgs["actor"],
                organization=org,
                target_user_id=outsider.pk,
                role="workspace_admin",
                workspace_id=ws.pk,
            )
    assert observed == {"locked": False}
