"""The AgentVersion save receiver: drafts an activation elsewhere archived come back,
and nothing happens for agents no environment owns."""

from unittest.mock import patch

import pytest

from simulate.models import AgentVersion, HarnessAgentVersionRuntime
from simulate.services import harness_agent_changes as changes
from simulate.services.harness_agent_changes import on_agent_version_saved
from simulate.tests.test_harness_agent_versions import _a1, _create, _environment
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_versioning_backfill import _agent_version
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def flags(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _owned(environment):
    """Mark the environment's agent as made by and for environments."""
    runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
        agent_version_id=environment.agent_version_id
    )
    runtime.agent_payload = {
        **(runtime.agent_payload or {}),
        "definition_owner": "environment",
    }
    runtime.save(update_fields=["agent_payload"])


def _drafts(user, workspace, storage, key, *, owned=True):
    environment = _environment(user, workspace, key, storage)
    if owned:
        _owned(environment)
    a1 = _a1(environment)
    pending = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
    failed = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v3"})
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=failed).update(
        preflight_status="failed"
    )
    passed = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v4"})
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=passed).update(
        preflight_status="passed"
    )
    plain = _agent_version(a1.agent_definition, status="draft")
    return environment, a1, [pending, failed], [passed, plain]


def _statuses(versions):
    return [
        AgentVersion._base_manager.get(id=version.id).status for version in versions
    ]


def test_an_activation_elsewhere_gives_our_drafts_back(
    user, workspace, storage, scheduled, django_capture_on_commit_callbacks
):
    _, a1, ours, others = _drafts(user, workspace, storage, "recv-restore")

    with django_capture_on_commit_callbacks(execute=True):
        a1.activate()

    assert _statuses(ours) == ["draft", "draft"]
    assert _statuses(others) == ["archived", "archived"]
    assert _statuses([a1]) == ["active"]


def test_agents_no_environment_owns_are_left_alone(
    user,
    workspace,
    storage,
    scheduled,
    django_capture_on_commit_callbacks,
    django_assert_num_queries,
):
    _, a1, ours, _ = _drafts(user, workspace, storage, "recv-unowned", owned=False)

    with django_capture_on_commit_callbacks() as callbacks:
        a1.activate()
    with django_assert_num_queries(1):
        for callback in callbacks:
            callback()

    assert _statuses(ours) == ["archived", "archived"]


def test_flag_off_reads_nothing(settings, django_assert_num_queries):
    settings.HARNESS_VERSIONING_ENABLED = False
    with django_assert_num_queries(0):
        on_agent_version_saved(
            "00000000-0000-0000-0000-000000000001",
            definition_id="00000000-0000-0000-0000-000000000002",
            status="active",
            created=True,
        )


def test_saves_that_cannot_activate_schedule_nothing(
    user, workspace, storage, scheduled, django_capture_on_commit_callbacks
):
    environment = _environment(user, workspace, "recv-short", storage)
    draft = _create(
        _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v"}
    )

    with django_capture_on_commit_callbacks() as callbacks:
        draft.save(update_fields=["configuration_snapshot", "updated_at"])
        draft.commit_message = "renamed"
        draft.save()

    assert callbacks == []


def test_a_version_made_elsewhere_is_not_frozen_without_the_agent_layer(
    user, workspace, storage, scheduled, django_capture_on_commit_callbacks
):
    environment = _environment(user, workspace, "recv-elsewhere", storage)
    _owned(environment)

    with django_capture_on_commit_callbacks(execute=True):
        made = _agent_version(_a1(environment).agent_definition, status="draft")

    assert not HarnessAgentVersionRuntime._base_manager.filter(
        agent_version=made
    ).exists()
    scheduled.assert_not_called()


def test_our_own_version_gets_one_side_row_and_one_check(
    user, workspace, storage, scheduled, django_capture_on_commit_callbacks
):
    environment = _environment(user, workspace, "recv-ours", storage)
    _owned(environment)

    with django_capture_on_commit_callbacks(execute=True):
        version = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v"}
        )

    assert (
        HarnessAgentVersionRuntime._base_manager.filter(agent_version=version).count()
        == 1
    )
    assert scheduled.call_count == 1


def test_a_failing_receiver_never_reaches_the_save(
    user, workspace, storage, django_capture_on_commit_callbacks
):
    environment = _environment(user, workspace, "recv-raise", storage)
    a1 = _a1(environment)
    with patch.object(
        changes, "on_agent_version_saved", side_effect=RuntimeError("boom")
    ):
        with django_capture_on_commit_callbacks(execute=True):
            a1.activate()
            made = _agent_version(a1.agent_definition, status="draft")
    assert AgentVersion._base_manager.filter(id=made.id).exists()
