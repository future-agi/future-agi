"""Run readiness Fix through the API: one fix end to end, concurrent starts, a reused
key and another organization."""

import threading
from unittest.mock import patch

import pytest
from django.db import connection

from accounts.models import Organization
from simulate.models import (
    HostedHarnessConversationMessage,
    HostedHarnessJob,
    ReadinessFixRun,
)
from simulate.services.harness_readiness import fix_driver, fix_save
from simulate.tests.test_harness_readiness import _get
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixtures
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_readiness_fix_driver import (  # noqa: F401 - fixture
    tasks as tasks,
)
from simulate.tests.test_harness_readiness_fix_run import (
    _broken,
    _fix_get,
    _post,
    _progress,
    _refused,
    _run,
)
from simulate.tests.test_harness_readiness_fix_run import (  # noqa: F401 - fixture
    fix_on as fix_on,
)
from simulate.tests.test_harness_readiness_fix_save import (
    ROWS,
    _certify_set,
    _check,
    _write_and_settle,
)

pytestmark = pytest.mark.django_db


class TestFlow:
    def test_a_fix_runs_end_to_end_through_the_view(
        self,
        user,
        workspace,
        storage,
        tasks,
        auth_client,
        django_capture_on_commit_callbacks,
    ):
        environment = _broken(user, workspace, storage, "fa-flow")

        response = _post(auth_client, environment.id, rows=list(ROWS))

        assert response.status_code == 202, response.content
        assert response.json()["fix"]["state"] == "queued"
        assert fix_driver.dispatch(environment.id, start_runtime=False)
        message = HostedHarnessConversationMessage.no_workspace_objects.get(
            id=_run(environment).current_command_id
        )
        _write_and_settle(
            environment,
            message,
            message.conversation,
            storage,
            django_capture_on_commit_callbacks,
        )
        _check(environment)
        _certify_set(environment, storage)
        with patch.object(fix_save.reports, "recheck"):
            _check(environment, django_capture_on_commit_callbacks)

        fix = _fix_get(auth_client, environment.id).json()["fix"]
        environment.refresh_from_db()
        assert (fix["state"], fix["error"], fix["rows"], fix["draft"]) == (
            "done",
            None,
            [],
            False,
        )
        assert fix["saved"]["environment"]["id"] == str(
            environment.environment_version_id
        )
        assert fix["saved"]["suite"] is None
        assert {one["row_id"]: one["state"] for one in fix["lines"]} == {
            "state": "fixed",
            "tool_routing": "could_not_fix",
        }
        assert _progress(_get(auth_client, environment.id).json())["state"] == "fixed"

    def test_a_reused_key_with_another_body_is_409(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fa-reused")
        _post(auth_client, environment.id, key="k1", rows=["state"])

        response = _post(auth_client, environment.id, key="k1", rows=["tool_routing"])

        _refused(response, "idempotency_key_reused")
        assert len(_run(environment).commands) == 1


class TestTenancy:
    def test_a_post_from_another_organization_is_404(
        self, user, workspace, storage, auth_client
    ):
        environment = _broken(user, workspace, storage, "fa-org")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            organization=Organization.objects.create(name="Other org for fixes")
        )

        response = _post(auth_client, environment.id, rows=["state"])

        assert response.status_code == 404, response.content
        assert not ReadinessFixRun.no_workspace_objects.filter(job=environment).exists()


def _concurrent_posts(user, workspace, environment, keys):
    """POST ``rows=["state"]`` once per key, all at once; the responses."""
    from conftest import WorkspaceAwareAPIClient

    clients = []
    for _key in keys:
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        clients.append(client)
    barrier = threading.Barrier(len(keys))
    responses = []
    errors = []

    def post(client, key):
        try:
            barrier.wait(timeout=10)
            responses.append(_post(client, environment.id, key=key, rows=["state"]))
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=post, args=(client, key))
        for client, key in zip(clients, keys, strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    return responses


# Real commits: ``tasks`` and ``scheduled`` record the work queued after them.
@pytest.mark.django_db(transaction=True)
def test_concurrent_posts_with_one_key_make_one_run(
    user, workspace, storage, tasks, scheduled
):
    environment = _broken(user, workspace, storage, "fa-same-key")

    responses = _concurrent_posts(user, workspace, environment, ["same"] * 4)

    assert [one.status_code for one in responses] == [202] * 4
    assert len({one.json()["fix"]["id"] for one in responses}) == 1
    run = _run(environment)
    assert (len(run.commands), len(run.lines)) == (1, 1)


@pytest.mark.django_db(transaction=True)
def test_concurrent_posts_for_the_same_rows_make_one_run(
    user, workspace, storage, tasks, scheduled
):
    environment = _broken(user, workspace, storage, "fa-same-rows")

    responses = _concurrent_posts(user, workspace, environment, ["a", "b"])

    codes = sorted(one.status_code for one in responses)
    assert codes == [202, 409]
    refused = next(one for one in responses if one.status_code == 409)
    assert refused.json()["error"] == "fix_running"
    run = _run(environment)
    assert len(run.commands) == 1
