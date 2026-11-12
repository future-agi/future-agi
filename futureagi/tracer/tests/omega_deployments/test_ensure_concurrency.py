"""Two ensure calls at the same moment (the Deployments tab after a deploy, and the Overview
opening) still give one link, one project and two monitors; two workspaces racing for one
deployment id leave exactly one link and nothing in the loser's workspace; two deployments
of one name racing share one pair of monitors; ensures racing for the plan's last pair of
monitors create it once.

Real commits on real connections (``transaction=True``), one thread per caller, released
together by a barrier.
"""

import threading

import pytest
from django.db import connection

from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.services.omega_deployments.ensure import (
    DeploymentNotInWorkspace,
    ensure_observe_link,
)
from tracer.tests.omega_deployments.conftest import cloud_plan, new_deployment_id
from tracer.utils.otel import get_or_create_project

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

ROUNDS = 5


def _race(calls):
    """Run each call in its own thread at once; return what each returned or raised."""
    barrier = threading.Barrier(len(calls))
    outcomes = [None] * len(calls)

    def run(index, call):
        try:
            barrier.wait(timeout=30)
            outcomes[index] = ("ok", call())
        except Exception as exc:  # recorded and asserted by the test
            outcomes[index] = ("error", exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=run, args=(index, call))
        for index, call in enumerate(calls)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads)
    return outcomes


def _call(organization, workspace, user, deployment_id, name):
    return lambda: ensure_observe_link(
        organization=organization,
        workspace=workspace,
        user=user,
        ee_deployment_id=deployment_id,
        deployment_name=name,
    )


def test_two_calls_at_once_give_one_link_one_project_two_monitors(
    organization, workspace, user
):
    for round_number in range(ROUNDS):
        name = f"race-{round_number}"
        deployment_id = new_deployment_id()
        call = _call(organization, workspace, user, deployment_id, name)

        outcomes = _race([call, call])

        assert [kind for kind, _ in outcomes] == ["ok", "ok"], outcomes
        (_, first), (_, second) = outcomes
        assert first.project.id == second.project.id
        assert [m.id for _, m in first.alerts] == [m.id for _, m in second.alerts]
        assert (
            OmegaDeploymentObserveLink.no_workspace_objects.filter(
                ee_deployment_id=deployment_id
            ).count()
            == 1
        )
        assert (
            Project.no_workspace_objects.filter(
                workspace=workspace, name=f"deploy/{name}"
            ).count()
            == 1
        )
        assert (
            UserAlertMonitor.no_workspace_objects.filter(
                project_id=first.project.id
            ).count()
            == 2
        )


def test_two_workspaces_racing_for_one_id_leave_one_link(
    organization, workspace, user, second_workspace
):
    for round_number in range(ROUNDS):
        name = f"contest-{round_number}"
        deployment_id = new_deployment_id()

        outcomes = _race(
            [
                _call(organization, workspace, user, deployment_id, name),
                _call(organization, second_workspace, user, deployment_id, name),
            ]
        )

        kinds = sorted(kind for kind, _ in outcomes)
        assert kinds == ["error", "ok"], outcomes
        (winner,) = [value for kind, value in outcomes if kind == "ok"]
        (refusal,) = [value for kind, value in outcomes if kind == "error"]
        assert isinstance(refusal, DeploymentNotInWorkspace), refusal
        link = OmegaDeploymentObserveLink.no_workspace_objects.get(
            ee_deployment_id=deployment_id
        )
        assert link.workspace_id == winner.link.workspace_id
        loser = second_workspace if link.workspace_id == workspace.id else workspace
        assert not Project.no_workspace_objects.filter(
            workspace=loser, name=f"deploy/{name}"
        ).exists()


@pytest.mark.parametrize("project_exists", [False, True])
def test_two_deployments_of_one_name_at_once_share_one_pair_of_monitors(
    organization, workspace, user, project_exists
):
    """A reused name: two new ids, one ``deploy/<name>``. With the project already there (the
    relay's first trace made it), both calls reach the monitors at once; the project row lock
    makes the second adopt the first's pair instead of making its own.
    """
    for round_number in range(ROUNDS):
        name = f"shared-{int(project_exists)}-{round_number}"
        if project_exists:
            get_or_create_project(
                project_name=f"deploy/{name}",
                organization_id=str(organization.id),
                project_type="observe",
                user_id=str(user.id),
                workspace_id=str(workspace.id),
            )

        outcomes = _race(
            [
                _call(organization, workspace, user, new_deployment_id(), name),
                _call(organization, workspace, user, new_deployment_id(), name),
            ]
        )

        assert [kind for kind, _ in outcomes] == ["ok", "ok"], outcomes
        (_, first), (_, second) = outcomes
        assert first.project.id == second.project.id
        assert [m.id for _, m in first.alerts] == [m.id for _, m in second.alerts]
        assert (
            UserAlertMonitor.no_workspace_objects.filter(
                project_id=first.project.id
            ).count()
            == 2
        )


def test_ensures_racing_for_the_plans_last_pair_create_it_once(
    organization, workspace, user
):
    """Four new deployments at once with room on the plan for one pair: the per-org lock makes
    three of them count the first one's monitors and defer, instead of all four seeing room."""
    racers = 4
    with cloud_plan(0) as plan:
        for round_number in range(ROUNDS):
            org_monitors = UserAlertMonitor.no_workspace_objects.filter(
                organization=organization
            )
            before = org_monitors.count()
            plan.limit = before + 2

            outcomes = _race(
                [
                    _call(
                        organization,
                        workspace,
                        user,
                        new_deployment_id(),
                        f"quota-{round_number}-{index}",
                    )
                    for index in range(racers)
                ]
            )

            assert [kind for kind, _ in outcomes] == ["ok"] * racers, outcomes
            targets = [target for _, target in outcomes]
            created = [target for target in targets if target.alerts]
            deferred = [target for target in targets if target.alerts_deferred]
            assert (len(created), len(deferred)) == (1, racers - 1), targets
            assert all(
                target.alerts_deferred.current_usage == before + 2
                for target in deferred
            )
            assert org_monitors.count() == before + 2
