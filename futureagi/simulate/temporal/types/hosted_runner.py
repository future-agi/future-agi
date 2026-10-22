"""Data classes for SimulationRunnerWorkflow (hosted runner, plan §9).

The platform dispatches a hosted job that runs the released SDK in a child
process. These are the workflow/activity I/O shapes; the actual ``StartRunnerJob``
payload is a JSON string built by ``simulate.services.hosted_runner`` (the backend
never imports the SDK, so it emits the job as a dict the child validates).
"""

from dataclasses import dataclass, field


@dataclass
class SimulationRunnerInput:
    test_execution_id: str
    run_test_id: str
    org_id: str
    scenario_ids: list[str] = field(default_factory=list)
    mode: str = "chat"
    simulator_id: str | None = None
    # Rerun scope: when non-empty, the job builds only the cases for these
    # CallExecution ids (single/partial rerun). Empty ⇒ the whole execution.
    call_execution_ids: list[str] = field(default_factory=list)


@dataclass
class SimulationRunnerOutput:
    status: str
    job_phase: str | None = None
    submission_status: str | None = None
    report_hash: str | None = None
    error: str | None = None


@dataclass
class BuildRunnerJobInput:
    test_execution_id: str
    run_test_id: str
    scenario_ids: list[str]
    mode: str
    simulator_id: str | None = None
    call_execution_ids: list[str] = field(default_factory=list)


@dataclass
class BuildRunnerJobOutput:
    job_id: str
    run_id: str
    mode: str
    job_json: str
    # The child's own deadline (D15); the parent derives its timeout from
    # this instead of imposing a separate cap. Optional with a None
    # default so a payload recorded by an older (pre-field) worker still
    # decodes during replay; the loud-failure guard moved to the
    # workflow/activity call sites instead of living in the shape itself.
    run_seconds: int | None = None


@dataclass
class RunHostedJobInput:
    job_id: str
    run_id: str
    mode: str
    job_json: str
    # See BuildRunnerJobOutput.run_seconds — same replay-decode reason.
    run_seconds: int | None = None


@dataclass
class RunHostedJobOutput:
    phase: str
    return_code: int
    report_hash: str | None = None
    submission_status: str | None = None
    detail: str | None = None


@dataclass
class FinalizeRunnerInput:
    test_execution_id: str
    job_phase: str
