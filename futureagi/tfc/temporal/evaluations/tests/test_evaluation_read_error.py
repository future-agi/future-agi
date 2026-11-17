"""What an SDK async evaluation's worker does when it cannot read the evaluation.

Nothing is faked: the real RunEvaluationWorkflow and
run_single_evaluation_activity run on an in-memory Temporal server the test
starts, against real rows. A database error comes from the real driver: the
worker's connection is pointed at a database that does not exist until the
workflow shows the first attempt failed. The evaluation is a model-free
function evaluation (Contains), so nothing in the run is replaced; the usage
emit is observed with a spy that still calls it.
"""

import asyncio
import uuid
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.db import close_old_connections, connections
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices

pytestmark = [pytest.mark.e2e, pytest.mark.xdist_group("temporal_evaluation_e2e")]

ABSENT_DATABASE = "fi_evaluation_read_probe_absent"


def _evaluation(user, organization, workspace):
    template = EvalTemplate.no_workspace_objects.create(
        name=f"read_retry_{uuid.uuid4().hex[:8]}",
        owner="user",
        organization=organization,
        workspace=workspace,
        config={
            "eval_type_id": "Contains",
            "function_eval": True,
            "required_keys": ["text"],
            "output": "Pass/Fail",
            "config": {"keyword": "query"},
        },
    )
    return Evaluation.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=template,
        input_data={"text": "a query"},
        status=StatusChoices.PENDING,
    )


async def _first_attempt_failed(handle):
    """True once the workflow's pending activity has a failed attempt."""
    description = await handle.describe()
    for pending in description.raw_description.pending_activities:
        if pending.attempt >= 2 or pending.HasField("last_failure"):
            return True
    return False


async def _run(evaluation_id, *, absent_until_first_failure):
    from tfc.temporal.evaluations.activities import run_single_evaluation_activity
    from tfc.temporal.evaluations.types import RunEvaluationWorkflowInput
    from tfc.temporal.evaluations.workflows import RunEvaluationWorkflow

    queue = f"evaluation-read-{uuid.uuid4().hex[:8]}"
    workflow_id = f"evaluation-{evaluation_id}"
    settings_dict = connections["default"].settings_dict
    real_name = settings_dict["NAME"]
    if absent_until_first_failure:
        settings_dict["NAME"] = ABSENT_DATABASE
    try:
        async with await WorkflowEnvironment.start_local() as env:
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[RunEvaluationWorkflow],
                activities=[run_single_evaluation_activity],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    RunEvaluationWorkflow.run,
                    RunEvaluationWorkflowInput(evaluation_id=str(evaluation_id)),
                    id=workflow_id,
                    task_queue=queue,
                )
                result_task = asyncio.ensure_future(handle.result())
                if absent_until_first_failure:
                    while not result_task.done():
                        if await _first_attempt_failed(handle):
                            settings_dict["NAME"] = real_name
                            break
                        await asyncio.sleep(0.2)
                    settings_dict["NAME"] = real_name
                try:
                    result = await asyncio.wait_for(result_task, timeout=180)
                except Exception as error:  # the workflow failed; report what it saw
                    result = error
                history = await handle.fetch_history()
    finally:
        settings_dict["NAME"] = real_name
        await sync_to_async(close_old_connections)()
    started = [
        event.activity_task_started_event_attributes.attempt
        for event in history.events
        if event.HasField("activity_task_started_event_attributes")
    ]
    return result, started


@pytest.mark.django_db(transaction=True)
async def test_a_failed_first_read_is_retried_and_the_evaluation_completes(
    user, organization, workspace
):
    from ee.usage.models.usage import APICallLog
    from ee.usage.services import emitter

    evaluation = await sync_to_async(_evaluation)(user, organization, workspace)
    with mock.patch.object(emitter, "emit", wraps=emitter.emit) as emit:
        result, started = await _run(evaluation.pk, absent_until_first_failure=True)

    row = await sync_to_async(Evaluation.no_workspace_objects.get)(pk=evaluation.pk)
    charges = await sync_to_async(
        lambda: APICallLog.objects.filter(
            source_id=str(evaluation.eval_template_id)
        ).count()
    )()
    print(
        f"RETRY result={getattr(result, 'status', result)!r} started={started} "
        f"row_status={row.status} charges={charges} usage_events={emit.call_count}"
    )
    assert getattr(result, "status", None) == StatusChoices.COMPLETED
    assert started and started[-1] >= 2
    assert row.status == StatusChoices.COMPLETED
    assert charges == 1
    assert emit.call_count == 1


@pytest.mark.django_db(transaction=True)
async def test_an_evaluation_that_does_not_exist_is_still_missing():
    result, started = await _run(uuid.uuid4(), absent_until_first_failure=False)

    print(f"MISSING result={getattr(result, 'status', result)!r} started={started}")
    assert getattr(result, "status", None) == "MISSING"
    assert started == [1]


@pytest.mark.django_db(transaction=True)
async def test_an_evaluation_id_that_is_not_a_uuid_is_missing_without_a_retry():
    # Not a database error: reading it again cannot succeed, so it is not retried.
    result, started = await _run("not-a-uuid", absent_until_first_failure=False)

    print(f"MALFORMED result={getattr(result, 'status', result)!r} started={started}")
    assert getattr(result, "status", None) == "MISSING"
    assert started == [1]
