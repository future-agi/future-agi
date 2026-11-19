"""Start a recorded replay: the agent runs one finished trial again, with every tool call answered from that trial's
recording."""

from typing import Any

from simulate.models import HostedHarnessExecution, HostedHarnessJob
from simulate.selectors.recorded_replay import (
    recorded_calls,
    replay_unavailable_reason,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    VersionPicks,
    create_selected_harness_run,
)


def create_recorded_replay_run(
    environment: HostedHarnessJob,
    *,
    call_execution_id: Any,
    idempotency_key: str,
    agent_version_id: Any = None,
    coding_agent_key_id: str | None = None,
) -> tuple[HostedHarnessJob, bool]:
    """One new run of the trial's scenario, its original run's pins and seed kept and ``agent_version_id`` (when
    given) run in place of its agent, whose harness answers every tool call from the trial's recording. The original
    trial is never changed."""
    execution = (
        HostedHarnessExecution.no_workspace_objects.select_related(
            "job", "source_scenario", "call_execution__test_execution"
        )
        .filter(call_execution_id=call_execution_id, job__environment=environment)
        .first()
    )
    if execution is None:
        raise HostedHarnessError(
            "replay_trial_unknown",
            "That trial is not one of this environment's.",
            status_code=404,
        )
    call = execution.call_execution
    reason = replay_unavailable_reason(call)
    if reason is not None:
        raise HostedHarnessError("replay_unavailable", reason, status_code=409)
    try:
        calls = recorded_calls(call)
    except ValueError as exc:
        raise HostedHarnessError(
            "replay_unavailable", str(exc), status_code=409
        ) from exc
    original = execution.job
    scenario_key = execution.source_scenario.scenario_key
    return create_selected_harness_run(
        environment,
        scenario_keys=[scenario_key],
        trials=1,
        idempotency_key=idempotency_key,
        rerun_of=original,
        picks=VersionPicks(agent_version_id=agent_version_id),
        seed=original.seed,
        coding_agent_key_id=coding_agent_key_id,
        recorded_replay={
            "source_call_execution_id": str(call.id),
            "source_test_execution_id": str(call.test_execution_id),
            "source_job_id": str(original.id),
            "scenario_key": scenario_key,
            "calls": calls,
        },
    )
