"""Config writes for one org run one after the other.

Every writer locks the org row before it reads the active version, so a
second concurrent writer waits for the first to commit and builds on its
version instead of failing on the one-active-version constraint.
`activate_config_version` takes the same lock and refuses a deleted version.
Every path takes that lock before any other row lock, and the lock does not
hold up inserts of rows that reference the org.
"""

import threading
import time
from unittest.mock import patch

import pytest
from django.db import connection, transaction

from accounts.models import Organization
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.request_log import AgentccRequestLog
from agentcc.services.org_config_writer import (
    MANAGED_MODELS_CHANGE,
    MERGE,
    ConfigVersionUnavailable,
    activate_config_version,
    lock_org,
    write_config_version,
)

# Long enough for the second writer to reach the lock while the first holds it.
HOLD_SECONDS = 1.0
JOIN_TIMEOUT = 30


def _versions(org):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=org)
        .order_by("version")
        .values_list("version", "is_active")
    )


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _run(target, results, name):
    try:
        results[name] = ("ok", target())
    except Exception as e:  # recorded for the assertion message
        results[name] = ("error", f"{type(e).__name__}: {e}")
    finally:
        connection.close()


def _start(target, results, name):
    thread = threading.Thread(target=_run, args=(target, results, name))
    thread.start()
    return thread


def _wait_until_a_backend_waits_on_this_one():
    """Return once another Postgres backend is blocked by this connection's
    locks, so the test knows the other side has reached its lock wait."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        (pid,) = cursor.fetchone()
        deadline = time.monotonic() + JOIN_TIMEOUT
        while time.monotonic() < deadline:
            # pg_locks is read live; pg_stat_activity would be a snapshot
            # fixed for the rest of this transaction.
            cursor.execute(
                "SELECT count(*) FROM pg_locks"
                " WHERE NOT granted AND %s = ANY(pg_blocking_pids(pid))",
                [pid],
            )
            if cursor.fetchone()[0]:
                return
            time.sleep(0.05)
    raise AssertionError("no backend waited on this connection's locks")


@pytest.fixture
def org(transactional_db):
    org = Organization.objects.create(name="Concurrent writers org")
    yield org
    AgentccOrgConfig.all_objects.filter(organization=org).delete()
    org.delete()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestConcurrentWriters:
    def test_concurrent_writers_serialize(self, org):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            managed_models={"enabled": True},
            routing={"strategy": "round_robin"},
        )
        first_in_push = threading.Event()
        results = {}

        def slow_push(config):
            # The first writer holds its transaction (and its locks) here.
            first_in_push.set()
            time.sleep(HOLD_SECONDS)
            return True

        def first():
            config, _ = write_config_version(
                org, {"cache": {"enabled": True}}, mode=MERGE, push=slow_push
            )
            return config.version

        def second():
            assert first_in_push.wait(JOIN_TIMEOUT)
            config, _ = write_config_version(
                org, {"budgets": {"enabled": True}}, mode=MERGE, push=lambda c: True
            )
            return config.version

        threads = [_start(first, results, "first"), _start(second, results, "second")]
        for thread in threads:
            thread.join(JOIN_TIMEOUT)

        assert results == {"first": ("ok", 2), "second": ("ok", 3)}
        assert _versions(org) == [(1, False), (2, False), (3, True)]
        active = _active(org)
        # The second writer built on the first one's version.
        assert active.cache["enabled"] is True
        assert active.budgets == {"enabled": True}
        assert active.managed_models == {"enabled": True}

    def test_activate_waits_for_the_org_lock(self, org):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=1, is_active=True
        )
        old = AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=2, is_active=False, cache={"enabled": True}
        )
        locked = threading.Event()
        results = {}
        released_at = {}

        def hold_org_lock():
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=org.pk)
                locked.set()
                time.sleep(HOLD_SECONDS)
                released_at["t"] = time.monotonic()
            return True

        def activate():
            assert locked.wait(JOIN_TIMEOUT)
            activate_config_version(old, push=lambda c: True)
            return time.monotonic()

        threads = [
            _start(hold_org_lock, results, "holder"),
            _start(activate, results, "activate"),
        ]
        for thread in threads:
            thread.join(JOIN_TIMEOUT)

        assert results["holder"] == ("ok", True)
        assert results["activate"][0] == "ok", results["activate"]
        assert results["activate"][1] >= released_at["t"]
        assert _versions(org) == [(1, False), (2, True)]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
class TestOrgLockComesFirst:
    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_post_org_configs_takes_the_org_lock_before_the_console_policy(
        self, _push, auth_client, organization
    ):
        # update-config locks the org (the writer), then the org's console guardrail
        # policy, where it moves a guardrail credential. POST org-configs/ upserts that
        # policy and takes the org lock first too, so the two never deadlock.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        console = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization, name="__ui_guardrails__", checks=[]
        )
        org_locked = threading.Event()
        results = {}

        def org_then_console_policy():
            with transaction.atomic():
                lock_org(organization.pk)
                org_locked.set()
                # POST now waits for the org lock, holding whatever it took
                # before asking for it.
                _wait_until_a_backend_waits_on_this_one()
                AgentccGuardrailPolicy.no_workspace_objects.select_for_update().get(
                    pk=console.pk
                )
            return True

        def post_org_config():
            assert org_locked.wait(JOIN_TIMEOUT)
            response = auth_client.post(
                "/agentcc/org-configs/",
                {"guardrails": {"checks": {"pii-detector": {"enabled": True}}}},
                format="json",
            )
            return response.status_code, response.json().get("message")

        threads = [
            _start(org_then_console_policy, results, "update_config_order"),
            _start(post_org_config, results, "post_org_configs"),
        ]
        for thread in threads:
            thread.join(JOIN_TIMEOUT)

        assert results["update_config_order"] == ("ok", True), results
        assert results["post_org_configs"] == ("ok", (200, None)), results
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        assert active.version == 2
        assert active.guardrails["checks"]["pii-detector"]["_policy"] == str(console.pk)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestOrgLockLetsOrgRowsIn:
    def test_request_log_insert_commits_while_a_write_pushes(self, org):
        # The writer holds the org lock across the gateway push. Inserting a
        # row with a foreign key to the org takes FOR KEY SHARE on the org row
        # at commit; the writer's lock must not conflict with it, or every
        # request log for the org waits for the push.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org, version=1, is_active=True
        )
        in_push = threading.Event()
        insert_done = threading.Event()
        results = {}

        def push_until_the_insert_is_done(config):
            in_push.set()
            results["insert_done_during_push"] = insert_done.wait(JOIN_TIMEOUT)
            return True

        def write():
            config, _ = write_config_version(
                org,
                {"cache": {"enabled": True}},
                mode=MERGE,
                push=push_until_the_insert_is_done,
            )
            return config.version

        def insert_request_log():
            assert in_push.wait(JOIN_TIMEOUT)
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        # Fail fast instead of waiting out the push.
                        cursor.execute("SET LOCAL lock_timeout = '3s'")
                    AgentccRequestLog.no_workspace_objects.create(
                        organization=org, request_id="during-config-write"
                    )
            finally:
                insert_done.set()
            return True

        threads = [
            _start(write, results, "write"),
            _start(insert_request_log, results, "insert"),
        ]
        for thread in threads:
            thread.join(JOIN_TIMEOUT)

        assert results["insert"] == ("ok", True), results
        assert results["insert_done_during_push"] is True
        assert results["write"] == ("ok", 2), results
        assert AgentccRequestLog.no_workspace_objects.filter(
            organization=org, request_id="during-config-write"
        ).exists()


@pytest.mark.integration
class TestActivateConfigVersion:
    def test_activate_refuses_deleted_target(self, organization):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        stale = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=2, is_active=False
        )
        # Deleted after the caller loaded it.
        AgentccOrgConfig.no_workspace_objects.filter(pk=stale.pk).update(deleted=True)
        pushed = []

        with pytest.raises(ConfigVersionUnavailable):
            activate_config_version(stale, push=pushed.append)

        assert pushed == []
        assert list(
            AgentccOrgConfig.all_objects.filter(organization=organization)
            .order_by("version")
            .values_list("version", "is_active", "deleted")
        ) == [(1, True, False), (2, False, True)]

    def test_activate_without_previous_version_warns_safely(self, organization):
        target = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=False,
            managed_models={"enabled": True},
        )

        config, warnings = activate_config_version(target, push=lambda c: True)

        assert config.pk == target.pk
        assert config.is_active is True
        assert warnings == [{"code": MANAGED_MODELS_CHANGE, "from": False, "to": True}]

    def test_activate_without_previous_version_and_managed_off_warns_nothing(
        self, organization
    ):
        target = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=False
        )

        config, warnings = activate_config_version(target, push=lambda c: True)

        assert config.is_active is True
        assert warnings == []
