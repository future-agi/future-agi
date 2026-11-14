"""An environment's held-back cases: what a coding agent may not read, and the counts
it gets back when it verifies an agent version on them.

The set is stored on the environment's payload metadata, beside the default suite
version it was last carried to. A coding agent reads only that suite, without those
cases, cannot run them, and learns how a version did on them only as counts.

The cases are hidden from the API only. A run's sandbox holds every case of its suite,
and the agent under test runs there as the same OS user as the harness, so a hostile
candidate can read them and speak them into a transcript the key reads. The OS-user
split follow-up closes that.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from accounts.authentication import is_coding_agent_key
from accounts.models import OrgApiKey
from simulate.models import HostedHarnessExecution, HostedHarnessJob
from simulate.pydantic_schemas.agent_fix import HELD_BACK_VERIFIED_MIN
from simulate.selectors.agent_fix import FINISHED_RUN_STATES
from simulate.services.hosted_harness_gateway import (
    AUTHORING_FAILED,
    RUNTIME_VALIDATION_FAILED,
    UNKNOWN_FAILURE_CAUSE,
    closed_failure_cause,
)
from simulate.services.run_compare import trial_passed, wilson_interval

# The environment payload metadata key; never copied to a run's payload.
HELD_BACK_METADATA_KEY = "verify_held_back"
# A verify that ended in one of these may be followed by another of the same version;
# the partial unique constraint on verify runs leaves them out too.
VERIFY_RETRYABLE_STATES = (
    HostedHarnessJob.State.FAILED,
    HostedHarnessJob.State.CANCELED,
)


@dataclass(frozen=True)
class HeldBack:
    suite_version_id: UUID
    # Every case ever held back, so a suite that drops one never shows it again; a
    # verify runs those still in ``suite_version_id``.
    scenario_keys: tuple[str, ...]
    # Every other case of a suite the set was carried to: the coding agent saw them.
    seen_keys: tuple[str, ...]


def held_back(environment: HostedHarnessJob) -> HeldBack | None:
    """The environment's held-back cases, or ``None`` before any were chosen."""
    stored = ((environment.payload or {}).get("metadata") or {}).get(
        HELD_BACK_METADATA_KEY
    )
    if not isinstance(stored, Mapping):
        return None
    return HeldBack(
        suite_version_id=UUID(str(stored["suite_version_id"])),
        scenario_keys=tuple(stored["scenario_keys"]),
        seen_keys=tuple(stored.get("seen_keys") or ()),
    )


def coding_agent_key(request: Any) -> OrgApiKey | None:
    """The coding agent API key the request authenticated with, else ``None``."""
    return request.org_api_key if is_coding_agent_key(request) else None


def detail_without(detail: dict[str, Any], keys: Collection[str]) -> dict[str, Any]:
    """An environment detail read with the ``keys`` scenarios taken out: their rows,
    their authoring results, the personas only they use, the checks only they make,
    and the counts of each."""
    hidden = set(keys)
    if not hidden:
        return detail
    rows = detail["scenarios"]
    scenarios = [row for row in rows if row["scenario_key"] not in hidden]
    shown_goals = {goal["name"] for row in scenarios for goal in row["sub_goals"]}
    hidden_goals = {
        goal["name"]
        for row in rows
        if row["scenario_key"] in hidden
        for goal in row["sub_goals"]
    } - shown_goals
    overview = {
        **detail["overview"],
        "scenario_count": max(
            0, detail["overview"]["scenario_count"] - (len(rows) - len(scenarios))
        ),
    }
    shown = {**detail, "overview": overview, "scenarios": scenarios}
    evaluations = detail["evaluations"]
    shown["evaluations"] = {
        **evaluations,
        "results": [
            one for one in evaluations["results"] if one["scenario_key"] not in hidden
        ],
    }
    if detail["world"] is not None:
        personas = []
        for persona in detail["world"]["personas"]:
            kept = [key for key in persona["scenario_keys"] if key not in hidden]
            if kept:
                personas.append({**persona, "scenario_keys": kept})
        shown["world"] = {**detail["world"], "personas": personas}
        if overview.get("personas_count") is not None:
            overview["personas_count"] = len(personas) or None
    if detail["contract"] is not None and hidden_goals:
        goals = [
            goal
            for goal in detail["contract"]["sub_goals"]
            if goal["name"] not in hidden_goals
        ]
        shown["contract"] = {**detail["contract"], "sub_goals": goals}
        if overview.get("sub_goals_count") is not None:
            overview["sub_goals_count"] = len(goals)
    return shown


# What a coding agent's read of its own run keeps: the fields fi reads to follow a run
# and count its results. The rest goes, the stage outputs first: they hold every case
# of the run's suite, held-back ones included.
_RUN_FIELDS = ("status", "receipts", "consumption", "platform")
_RUN_JOB_FIELDS = ("job_id", "run_id", "run_test_id", "test_execution_id")
_RUN_METADATA_FIELDS = (
    "environment_job_id",
    "selected_scenario_keys",
    "trials",
    "rerun_execution_keys",
    "version_pins",
)
# A failure's details hold the guest's logs, and a certify on a cache miss runs every
# case of the suite there, held-back ones included; only these fields stay, and a cause
# only as a value from the closed set.
_RUN_FAILURE_FIELDS = ("domain", "stage", "code", "message")
# The message a coding agent reads instead of the stored one, which can end with what
# the guest printed: the label of its code.
_FAILURE_LABELS = {
    "authoring_runtime_validation_failed": RUNTIME_VALIDATION_FAILED,
    "authoring_failed": AUTHORING_FAILED,
}
# What each cause means and what to do next, in plain words; the stable code stays beside it.
CAUSE_WORDS = {
    "agent_exited_before_ready": (
        "Didn't start — the agent exited before it was ready. "
        "Fix the crash on startup, then push a new agent version."
    ),
    "agent_not_ready_in_time": (
        "Didn't start in time — the agent wasn't ready within the limit. "
        "Run it again; if it repeats, make it start or listen sooner."
    ),
    "chat_target_timeout": (
        "Stopped — the agent didn't reply to a message in time. "
        "Run it again. If it repeats, make the agent reply faster."
    ),
    "chat_target_refused": (
        "Stopped — the agent refused the connection. "
        "Check that it listens on the port it was given, then push a new agent version."
    ),
    # Exit 78 is deterministic, so running it again cannot help; the agent's own start, on the
    # reader's machine, shows the error. No guest text reaches the reader.
    "unknown": (
        "The agent stopped before it was ready and didn't say why. "
        "Start it on your own machine to see the error it prints."
    ),
}


# A start timeout recorded without a cause (before failures carried one, or by an older
# image) names its closed code in the failure; it reads as the slow start it was.
_START_TIMEOUT_CODE = "depends_on_timeout"


def failure_cause(failure: Mapping[str, Any]) -> str | None:
    """The cause a failure reads with: its own from the closed set, or the slow start that a
    start timeout without one names; None for a failure that has neither."""
    cause = closed_failure_cause(failure["cause"]) if "cause" in failure else None
    if cause in (None, UNKNOWN_FAILURE_CAUSE) and _START_TIMEOUT_CODE in (
        f"{failure.get('code') or ''} {failure.get('message') or ''}"
    ):
        return "agent_not_ready_in_time"
    return cause


def coding_agent_failure_message(failure: Mapping[str, Any]) -> str:
    """A failure's message as a coding agent and a call row read it: when the failure has a
    cause, what it means and what to do, with a named cause's code last; otherwise fixed text
    for its code. An unknown cause reads as words only: the machine field still says it."""
    cause = failure_cause(failure)
    if cause == UNKNOWN_FAILURE_CAUSE:
        return CAUSE_WORDS[cause]
    if cause is not None:
        return f"{CAUSE_WORDS[cause]} (cause: {cause})"
    code = str(failure.get("code") or "failed")
    return _FAILURE_LABELS.get(code, f"The run failed ({code})")


def coding_agent_run(read: Mapping[str, Any]) -> dict[str, Any]:
    """A run read as a coding agent may see it: only the fields listed above."""
    job = read.get("job") or {}
    metadata = job.get("metadata") or {}
    shown = {name: read[name] for name in _RUN_FIELDS if name in read}
    failure = (shown.get("status") or {}).get("failure")
    if isinstance(failure, Mapping):
        shown["status"] = {
            **shown["status"],
            "failure": {
                **{
                    name: failure[name]
                    for name in _RUN_FAILURE_FIELDS
                    if name in failure
                },
                **(
                    {"cause": cause}
                    if (cause := failure_cause(failure)) is not None
                    else {}
                ),
                "message": coding_agent_failure_message(failure),
            },
        }
    return {
        **shown,
        "job": {
            **{name: job.get(name) for name in _RUN_JOB_FIELDS},
            "metadata": {
                name: metadata[name]
                for name in _RUN_METADATA_FIELDS
                if name in metadata
            },
        },
    }


def members_without(
    members: list[dict[str, Any]], keys: Collection[str]
) -> list[dict[str, Any]]:
    """A suite version's members with the ``keys`` scenarios taken out."""
    hidden = set(keys)
    return [member for member in members if member["scenario_key"] not in hidden]


def verify_run(
    environment: HostedHarnessJob, verify_id: UUID
) -> HostedHarnessJob | None:
    """One verify of this environment, by its id."""
    return HostedHarnessJob.no_workspace_objects.filter(
        verify_of=environment, id=verify_id
    ).first()


def version_verify(
    environment: HostedHarnessJob, agent_version_id: UUID
) -> HostedHarnessJob | None:
    """The live or completed verify of one agent version on this environment, if it
    has one. A verify that failed or was canceled does not count: another may follow."""
    return (
        HostedHarnessJob.no_workspace_objects.filter(
            verify_of=environment, agent_version_id=agent_version_id
        )
        .exclude(state__in=VERIFY_RETRYABLE_STATES)
        .first()
    )


def verify_result(run: HostedHarnessJob) -> dict[str, Any]:
    """How an agent version did on the held-back cases, as counts only. A trial passes
    when every sub-goal held; one nothing decided counts as neither. The range is the
    95% Wilson interval of the trial pass rate, once the run has finished."""
    metadata = (run.payload or {}).get("metadata") or {}
    cases = len(metadata.get("selected_scenario_keys") or [])
    passed = failed = total = 0
    for call_metadata in HostedHarnessExecution.no_workspace_objects.filter(
        job=run
    ).values_list("call_execution__call_metadata", flat=True):
        total += 1
        receipt = (call_metadata or {}).get("hosted_harness_receipt")
        if not isinstance(receipt, Mapping):
            continue
        verdict = trial_passed(receipt.get("sub_goals"), receipt.get("status"))
        passed += verdict is True
        failed += verdict is False
    rate = None
    if run.state in FINISHED_RUN_STATES and passed + failed:
        rate = [round(bound, 4) for bound in wilson_interval(passed, passed + failed)]
    return {
        "state": run.state,
        "passed": passed,
        "failed": failed,
        "total": total,
        "trials": int(metadata.get("trials") or 0),
        "held_back_pass_rate": rate,
        "limited_validation": cases < HELD_BACK_VERIFIED_MIN,
    }
