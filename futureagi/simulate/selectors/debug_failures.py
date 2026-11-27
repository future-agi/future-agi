"""What Debug failures reads from a run: its connected and failing call counts, and
whether it already has an analysis."""

from django.db.models import Q

from simulate.models.agent_optimiser_run import AgentOptimiserRun
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services.run_results_v3_queries import run_calls_queryset

# The run outcomes a diagnosis explains. The run page's failing count adds the same two.
FAILING_OUTCOMES = ("failed", "error")

_TEXT = CallExecution.SimulationCallType.TEXT
# A chat connected when it completed; a voice call when it ran for any time. Read per
# call: a hosted run's agent definition can carry a stale type, the call's own type is
# authoritative. Twin of the KPI view's run-level `connected_calls`.
_CONNECTED = Q(
    simulation_call_type=_TEXT, status=CallExecution.CallStatus.COMPLETED
) | (~Q(simulation_call_type=_TEXT) & Q(duration_seconds__gt=0))


def connected_call_count(execution: TestExecution) -> int:
    return CallExecution.objects.filter(_CONNECTED, test_execution=execution).count()


def failing_call_count(execution: TestExecution) -> int:
    return (
        run_calls_queryset(execution)
        # `result_outcome` is the queryset's annotation, so it is filtered through Q.
        .filter(Q(result_outcome__in=FAILING_OUTCOMES))
        .count()
    )


def has_debug_failures_analysis(execution: TestExecution) -> bool:
    """Whether the analysis read returns an analysis the run already has: any run of its
    optimiser, in any status. A refused first read leaves an optimiser with no run."""
    return (
        execution.agent_optimiser_id is not None
        and AgentOptimiserRun.objects.filter(
            agent_optimiser_id=execution.agent_optimiser_id
        ).exists()
    )
