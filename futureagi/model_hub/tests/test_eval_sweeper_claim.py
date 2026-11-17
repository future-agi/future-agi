"""The evaluation sweeper claims a waiting evaluation only while it still waits.

The sweeper reads the waiting evaluations, then marks them running and
dispatches them. A stop that lands between the read and the claim must win:
the stopped evaluation keeps its status and is not dispatched.
"""

from unittest.mock import patch

import pytest
from django.db.models.query import QuerySet

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.tasks import user_evaluation
from model_hub.tests.test_dsx_l5_experiment_defaults import Tenant
from tfc.middleware.workspace_context import workspace_context


def sweep(stop=None):
    """Run the sweeper's tick; with ``stop``, that evaluation is stopped just
    before the sweeper's first claim."""
    dispatched = []
    original_update = QuerySet.update
    pending = {"stop": stop}

    def dispatch(args=None, kwargs=None, **options):
        dispatched.append(dict(args[0]))

    def update(queryset, **values):
        if (
            pending["stop"] is not None
            and queryset.model is UserEvalMetric
            and values.get("status") == StatusType.RUNNING.value
        ):
            stopped, pending["stop"] = pending["stop"], None
            original_update(
                UserEvalMetric.no_workspace_objects.filter(id=stopped.id),
                status=StatusType.ERROR.value,
            )
        return original_update(queryset, **values)

    with (
        patch.object(user_evaluation, "close_old_connections"),
        patch.object(
            user_evaluation.process_evaluation_single_task,
            "apply_async",
            side_effect=dispatch,
        ),
        patch.object(QuerySet, "update", update),
        workspace_context(None),
    ):
        user_evaluation.execute_evaluation._original_func()
    return dispatched


def waiting(tenant):
    evals = tenant.evals
    UserEvalMetric.no_workspace_objects.filter(id__in=[e.id for e in evals]).update(
        status=StatusType.NOT_STARTED.value
    )
    return evals


def status_of(evaluation):
    return UserEvalMetric.no_workspace_objects.get(id=evaluation.id).status


@pytest.mark.django_db
def test_waiting_evaluations_are_claimed_and_dispatched():
    first, second = waiting(Tenant("sweep-claim"))
    dispatched = sweep()
    assert sorted(d["eval_id"] for d in dispatched) == sorted(
        [str(first.id), str(second.id)]
    )
    assert {d["type"] for d in dispatched} == {"single"}
    assert status_of(first) == StatusType.RUNNING.value
    assert status_of(second) == StatusType.RUNNING.value


@pytest.mark.django_db
def test_a_stop_before_the_claim_keeps_the_evaluation_stopped():
    stopped, other = waiting(Tenant("sweep-stop"))
    dispatched = sweep(stop=stopped)
    assert [d["eval_id"] for d in dispatched] == [str(other.id)]
    assert status_of(stopped) == StatusType.ERROR.value
    assert status_of(other) == StatusType.RUNNING.value
