"""The environments a user sees, and the agent each one tests.

The Environments list, an agent's Environments tab and its count, and the environments a
production change reaches all start from these, so they agree on which builds are an
agent's.
"""

from typing import Any

from django.db.models import Case, F, Q, QuerySet, When

from simulate.models import HostedHarnessJob

# An adaptation is followed as a job; its composition is the environment. Only one in review is
# listed, until it is done. Read as paths, not keys: a path missing at any level is NULL, so a job
# without metadata is no adaptation, where a negated key lookup would drop it.
LISTED = Q(payload__metadata__adaptation__isnull=True) | (
    Q(payload__metadata__adaptation__mode="review")
    & ~Q(state=HostedHarnessJob.State.COMPLETED)
)

# A build holds its agent through the version it was made from until its scenarios are submitted
# and it is linked to a test; from then on, through the test. An environment of a deleted test is
# no agent's.
OWNER = Case(
    When(run_test__isnull=True, then=F("agent_version__agent_definition_id")),
    When(run_test__deleted=False, then=F("run_test__agent_definition_id")),
    default=None,
)


def in_scope(workspace: Any) -> Q:
    """The tenant scope a request sees: exactly its workspace, or no workspace when it has none."""
    return Q(workspace__isnull=True) if workspace is None else Q(workspace=workspace)


def owned_by(agent_id: Any) -> Q:
    """The environments that test ``agent_id``: ``OWNER`` as a filter that can use indexes."""
    return Q(run_test__isnull=True, agent_version__agent_definition_id=agent_id) | Q(
        run_test__deleted=False, run_test__agent_definition_id=agent_id
    )


def environment_jobs() -> QuerySet[HostedHarnessJob]:
    """Every live environment, adaptations included; no run of one, in any tenant."""
    return HostedHarnessJob.no_workspace_objects.filter(
        environment__isnull=True, deleted=False
    )


def listed_environment_jobs() -> QuerySet[HostedHarnessJob]:
    """The environments the Environments list shows, in any tenant; the caller scopes it."""
    return environment_jobs().filter(LISTED)
