"""Grading a harness run's finished calls again keeps what each slot held.

The harness run-evals route never queues a call holding a stored verdict, but
it does set a failed or skipped grading pending. That result is kept in
``SimulateEvalResult`` first, the same way the other routes keep what they replace,
and a dispatch that fails puts the slot back and forgets its row.
"""

from __future__ import annotations

import pytest

from simulate.models import SimulateEvalResult
from simulate.tests import test_harness_environment_evals as env_evals
from simulate.tests.test_harness_environment_evals import (
    _bound_config,
    _call,
    _graded,
    _run_add,
    _template,
)

# The run-evals tests' environment, client, run and dispatch spy.
environment = env_evals.environment
env_client = env_evals.env_client
finished_run = env_evals.finished_run
dispatch = env_evals.dispatch

NAME = "no_misselling"
FAILED = {
    "name": NAME,
    "status": "Failed",
    "error": "error",
    "output": None,
    "reason": "the judge timed out",
}
SKIPPED = {"name": NAME, "status": "skipped", "skipped": True, "reason": "too short"}


def _kept(call):
    return list(
        SimulateEvalResult.objects.filter(
            call_execution=call, origin=SimulateEvalResult.Origin.REPLACED_OWN_SCORE
        )
    )


def _bind(env_client, environment, finished_run, workspace, capture):
    """Bind the eval on a run with no calls yet, so nothing is queued."""
    template = _template(NAME, ["conversation"], tags=("Conversation",))
    with capture(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )
    assert response.status_code == 202, response.content
    return template, _bound_config(environment)


@pytest.mark.django_db
def test_run_evals_keeps_a_failed_or_skipped_grading_it_sets_pending(
    env_client,
    environment,
    finished_run,
    workspace,
    user,
    dispatch,
    django_capture_on_commit_callbacks,
):
    template, config = _bind(
        env_client,
        environment,
        finished_run,
        workspace,
        django_capture_on_commit_callbacks,
    )
    key = str(config.id)
    failed = _call(finished_run, metadata=_graded(), eval_outputs={key: FAILED})
    skipped = _call(finished_run, metadata=_graded(), eval_outputs={key: SKIPPED})
    empty = _call(finished_run, metadata=_graded())
    placeholder = _call(
        finished_run, metadata=_graded(), eval_outputs={key: {"status": "pending"}}
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.status_code == 202, response.content
    assert response.json()["queued"] == 4
    [failed_row] = _kept(failed)
    assert failed_row.replaced_verdict == FAILED
    assert failed_row.state == SimulateEvalResult.State.GRADING_ERROR
    assert failed_row.question_key == key
    assert failed_row.question_definition["name"] == NAME
    assert failed_row.replaced_by_id == user.id
    [skipped_row] = _kept(skipped)
    assert skipped_row.replaced_verdict == SKIPPED
    assert skipped_row.replacement_id == failed_row.replacement_id
    assert _kept(empty) == []
    assert _kept(placeholder) == []


@pytest.mark.django_db
def test_a_failed_dispatch_puts_the_slot_back_and_forgets_its_row(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    template, config = _bind(
        env_client,
        environment,
        finished_run,
        workspace,
        django_capture_on_commit_callbacks,
    )
    key = str(config.id)
    calls = sorted(
        (
            _call(finished_run, metadata=_graded(), eval_outputs={key: FAILED})
            for _ in range(2)
        ),
        key=lambda call: call.id,
    )
    reached, unreached = calls

    def _first_only(*args, **kwargs):
        if dispatch.call_count > 1:
            raise RuntimeError("broker unreachable")

    dispatch.side_effect = _first_only

    with django_capture_on_commit_callbacks(execute=True):
        response = _run_add(
            env_client, environment, finished_run, workspace, template.name
        )

    assert response.status_code == 202, response.content
    assert dispatch.call_count == 2
    unreached.refresh_from_db()
    assert unreached.eval_outputs[key] == FAILED
    assert _kept(unreached) == []
    # The call whose job reached the broker is being graded: its row stays.
    reached.refresh_from_db()
    assert reached.eval_outputs[key] == {"status": "pending"}
    [row] = _kept(reached)
    assert row.replaced_verdict == FAILED


@pytest.mark.django_db
def test_a_caller_that_does_not_keep_sets_the_slot_pending_and_keeps_no_row(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    from simulate.services.harness_run_evals import queue_evals_for_finished_calls

    _, config = _bind(
        env_client,
        environment,
        finished_run,
        workspace,
        django_capture_on_commit_callbacks,
    )
    key = str(config.id)
    failed = _call(finished_run, metadata=_graded(), eval_outputs={key: FAILED})

    with django_capture_on_commit_callbacks(execute=True):
        counts = queue_evals_for_finished_calls(
            finished_run, [config], keep_replaced=False
        )

    assert counts[key]["queued"] == 1
    failed.refresh_from_db()
    assert failed.eval_outputs[key] == {"status": "pending"}
    assert _kept(failed) == []
