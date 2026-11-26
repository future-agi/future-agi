"""The two counts Debug failures needs from a run: connected calls and failing calls."""

from django.db.models import Q

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
        .filter(result_outcome__in=FAILING_OUTCOMES)
        .count()
    )
