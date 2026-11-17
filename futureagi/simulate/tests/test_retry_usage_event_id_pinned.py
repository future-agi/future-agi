"""A call's error localization and call cost usage event ids, for fixed
inputs, are these exact ids.

Every tree that bills them computes the same id for the same call, call
workflow run and charge, so a retry that lands on a newer release after a
deploy is billed once. The inputs are the made-up values of
test_eval_usage_event_id_pinned.py.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simulate.models.test_execution import CallExecution
from simulate.temporal.types.activities import DeductCostInput

pytestmark = pytest.mark.requires_ee

CALL_ID = "11111111-1111-4111-8111-111111111111"
ORG_ID = "44444444-4444-4444-8444-444444444444"
RUN = (
    "call-exec-11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
)
EVAL_CONFIG_ID = "33333333-3333-4333-8333-333333333333"


def _localization_event_id(source):
    from evaluations.usage_step import error_localizer_step, step_event_id
    from simulate.temporal.activities import xl

    call = SimpleNamespace(id=CALL_ID)
    step = xl._usage_step(call, RUN, ("simulate_eval_config", EVAL_CONFIG_ID))
    return step_event_id(error_localizer_step(step, source))


def test_a_playground_localization_event_id_is_pinned():
    """The localization of the playground task the evaluation makes:
    evaluated ("simulate_call", CALL_ID), run RUN, evaluator
    ("simulate_eval_config", EVAL_CONFIG_ID, "error_localizer", "playground").
    """
    assert (
        _localization_event_id("playground") == "6b24b42d-983c-50f9-8dff-fb01144dd1e3"
    )


def test_a_call_localization_event_id_is_pinned():
    """The localization of the call's own task: evaluated ("simulate_call",
    CALL_ID), run RUN, evaluator ("simulate_eval_config", EVAL_CONFIG_ID,
    "error_localizer", "simulate")."""
    assert _localization_event_id("simulate") == "8aec2b55-89e1-558e-8077-a67f03c83c1a"


class _TheCall:
    """Stands in for the activity's CallExecution: the one voice call it
    reads, two minutes long."""

    SimulationCallType = CallExecution.SimulationCallType
    DoesNotExist = CallExecution.DoesNotExist

    def __init__(self):
        call = SimpleNamespace(
            id=CALL_ID,
            duration_seconds=120,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            test_execution=SimpleNamespace(
                run_test=SimpleNamespace(organization_id=ORG_ID)
            ),
        )
        self.objects = MagicMock()
        self.objects.select_related.return_value.aget = AsyncMock(return_value=call)


@pytest.mark.asyncio
async def test_a_call_cost_event_id_is_pinned():
    """evaluated ("simulate_call", CALL_ID), run RUN, evaluator "call_cost"."""
    from ee.voice.temporal.activities import voice_large

    events = []
    info = SimpleNamespace(
        activity_id="1",
        activity_type="deduct_call_cost",
        attempt=1,
        heartbeat_timeout=None,
        task_queue="tasks_l",
        workflow_id=RUN[0],
        workflow_namespace="default",
        workflow_run_id=RUN[1],
        workflow_type="CallExecutionWorkflow",
    )
    with (
        patch.object(voice_large, "CallExecution", _TheCall()),
        patch.object(voice_large, "close_old_connections"),
        patch("ee.usage.services.emitter.emit", events.append),
        patch("temporalio.activity.info", return_value=info),
    ):
        await voice_large.deduct_call_cost(
            DeductCostInput(call_id=CALL_ID, org_id=ORG_ID, cost=0)
        )

    (event,) = events
    assert event.event_id == "1f237070-f209-5e06-815f-276ca9310e8c"
