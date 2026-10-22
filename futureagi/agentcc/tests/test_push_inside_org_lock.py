"""Pushes from the provider-credential and gateway views hold the org lock.

`provider_credential._push_config_to_gateway` and
`gateway._push_current_config(org)` (reload, update-provider and
remove-provider) read the org's active config and push it under the org lock, as
`write_config_version` does, so pushes reach the gateway in version order.

Each test runs a view push (thread A) against a writer (thread B) that saves
the next version as A pushes. A's push waits until B either waits on A's lock
or has pushed, then records what it pushed.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection

from accounts.models import Organization
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.org_config_writer import MERGE, write_config_version
from agentcc.views.gateway import AgentccGatewayViewSet
from agentcc.views.provider_credential import AgentccProviderCredentialViewSet

JOIN_TIMEOUT = 30
VIEW_PUSH = "view_push"
WRITER = "writer"


def _run(target, results, name):
    try:
        results[name] = ("ok", target())
    except Exception as e:  # recorded for the assertion message
        results[name] = ("error", f"{type(e).__name__}: {e}")
    finally:
        connection.close()


def _start(target, results, name):
    thread = threading.Thread(target=_run, args=(target, results, name), name=name)
    thread.start()
    return thread


def _a_backend_waits_on_this_connection():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks"
            " WHERE NOT granted AND pg_backend_pid() = ANY(pg_blocking_pids(pid))"
        )
        return cursor.fetchone()[0] > 0


class _RecordingGateway:
    """Stands in for the gateway client and records every config push.

    The view push (thread A) holds its push until the writer either waits on
    A's locks or has finished its own push, so the test needs no sleeps:
    pushing under the lock records A then B, and pushing after the lock
    records B then A."""

    def __init__(self):
        self.pushes = []
        self.view_in_push = threading.Event()
        self.writer_pushed = threading.Event()
        self._lock = threading.Lock()

    def set_org_config(self, org_id, payload):
        name = threading.current_thread().name
        if name == VIEW_PUSH:
            self.view_in_push.set()
            deadline = time.monotonic() + JOIN_TIMEOUT
            while not (
                self.writer_pushed.is_set() or _a_backend_waits_on_this_connection()
            ):
                if time.monotonic() > deadline:
                    raise AssertionError("the writer neither waited nor pushed")
                time.sleep(0.02)
        with self._lock:
            self.pushes.append(
                (
                    name,
                    payload["meta"]["version"],
                    payload.get("managed_models", {}).get("enabled"),
                )
            )
        if name == WRITER:
            self.writer_pushed.set()
        return {}


@pytest.fixture
def org(transactional_db):
    org = Organization.objects.create(name="Push inside the org lock")
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        managed_models={"enabled": True},
    )
    yield org
    AgentccOrgConfig.all_objects.filter(organization=org).delete()
    org.delete()


def _race(org, view_push):
    """Run `view_push` against a writer that opts the org out of managed
    models while the view pushes; return the recorded pushes."""
    gateway = _RecordingGateway()
    results = {}

    def opt_out():
        assert gateway.view_in_push.wait(JOIN_TIMEOUT)
        config, _ = write_config_version(
            org, {"managed_models": {"enabled": False}}, mode=MERGE
        )
        return config.version

    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=gateway),
        patch("agentcc.services.config_push._ensure_managed_account", MagicMock()),
    ):
        threads = [
            _start(view_push, results, VIEW_PUSH),
            _start(opt_out, results, WRITER),
        ]
        for thread in threads:
            thread.join(JOIN_TIMEOUT)

    assert results == {VIEW_PUSH: ("ok", True), WRITER: ("ok", 2)}, results
    return gateway.pushes


def _assert_pushes_in_version_order(pushes):
    # The stale snapshot (v1, managed on) never reaches the gateway after the
    # opt-out (v2, managed off): the last push is the newest version.
    assert pushes == [(VIEW_PUSH, 1, True), (WRITER, 2, False)], pushes
    versions = [version for _, version, _ in pushes]
    assert versions == sorted(versions)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestViewPushesHoldTheOrgLock:
    def test_provider_credential_push_holds_org_lock(self, org):
        view = AgentccProviderCredentialViewSet()

        pushes = _race(org, lambda: view._push_config_to_gateway(org))

        _assert_pushes_in_version_order(pushes)

    def test_gateway_push_current_config_holds_org_lock(self, org):
        view = AgentccGatewayViewSet()

        pushes = _race(org, lambda: view._push_current_config(org))

        _assert_pushes_in_version_order(pushes)
