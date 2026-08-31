"""Deciding the harness sub-goals that no code could settle.

The sandbox that ran the scenario holds no platform credentials by design, so it seals the
evidence and stops there. This is the other half: the run's organization and workspace are known
here, so the eval template is created in the right tenant rather than in whichever account the
runner happened to be configured with.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from django.db.models import Q

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import CallExecution, HostedHarnessArtifact
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_storage_client

logger = structlog.get_logger(__name__)

_JUDGE_MODEL = "turing_large"
_JUDGE_CHOICES = ["pass", "fail"]
_HARNESS_EVAL_NAMESPACE = uuid.UUID("6b1f3a52-0e2a-4a0e-8a6f-6f5b6f6f5e21")


def load_scenario_evidence(call: CallExecution) -> dict[str, Any] | None:
    """The evidence bundle the guest sealed for this call's scenario."""
    registration = getattr(call, "hosted_registration", None)
    if registration is None:
        return None
    artifact = (
        HostedHarnessArtifact.no_workspace_objects.filter(
            job=registration.job,
            scenario_key=registration.scenario_key,
            kind="evidence",
        )
        .order_by("created_at")
        .last()
    )
    if artifact is None:
        return None
    response = None
    try:
        response = get_storage_client().get_object(
            UPLOAD_BUCKET_NAME, artifact.object_key
        )
        body = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - a missing or corrupt bundle is not a failed sub-goal
        logger.exception("harness_evidence_unreadable", artifact_id=str(artifact.id))
        return None
    finally:
        if response is not None:
            response.close()
            response.release_conn()
    return body if isinstance(body, dict) else None


def _instructions(claim: dict[str, Any], agent: str) -> str:
    """What the judge decides, and from what.

    The record carries the world as it finished, not only what was said: whether an answer was
    correct is settled by what the tools returned and what the tables hold afterwards, and a
    judge given the transcript alone can only report that it cannot tell.
    """
    question = str(claim.get("judged") or claim.get("what") or claim.get("name") or "")
    return (
        f"You are judging one run of {agent}.\n\n"
        f"Decide strictly: {question}\n\n"
        "You are given a JSON record of the run: every tool call the agent made with its "
        "arguments and what came back, and the state of its world afterwards. Row counts are "
        "reported alongside the rows actually shown, so treat a truncated table as evidence "
        "about what is present, never as evidence about what is absent.\n\n"
        "The tool calls are the truth about what happened. An agent that says it did something "
        "no call performed has not done it, however convincing it sounds, and an answer is "
        "correct when it matches what the calls returned. A refused call did not happen: judge "
        "what the agent ended up doing, not what it tried on the way. Something merely not "
        "contradicted does not hold. Where the claim is that something must not have happened, "
        "it holds when the thing did not happen. Declining something holds only if the agent "
        "both declined it and gave a true reason.\n\n"
        "Answer 'pass' when the claim holds and 'fail' when it does not.\n\n"
        "The run:\n<output>{{output}}</output>"
    )


def _agent_label(call: CallExecution) -> str:
    run_test = call.test_execution.run_test
    return getattr(getattr(run_test, "agent_definition", None), "name", "") or "agent"


def _template_name(call: CallExecution, sub_goal: str) -> str:
    """A name that cannot collide across the workspaces of one organization.

    Template uniqueness is per organization while the lists that check for an existing one are
    per workspace, so two workspaces testing agents that share a name would otherwise contend
    for a single row and the loser would silently reuse the winner's instructions.
    """
    workspace_id = getattr(call.test_execution.run_test, "workspace_id", None)
    scope = str(workspace_id)[:8] if workspace_id else "org"
    return f"harness-{_agent_label(call)}-{scope}-{sub_goal}"[:255]


def _ensure_template(
    call: CallExecution, claim: dict[str, Any], instructions: str
) -> EvalTemplate | None:
    run_test = call.test_execution.run_test
    name = _template_name(call, str(claim.get("name") or ""))
    existing = (
        EvalTemplate.no_workspace_objects.filter(
            Q(organization=run_test.organization),
            Q(workspace=run_test.workspace) | Q(workspace__isnull=True),
            name=name,
            deleted=False,
        )
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        if existing.criteria != instructions:
            existing.criteria = instructions
            existing.save(update_fields=["criteria"])
        return existing
    try:
        return EvalTemplate.objects.create(
            name=name,
            description=str(claim.get("what") or "")[:1000],
            organization=run_test.organization,
            workspace=run_test.workspace,
            criteria=instructions,
            choices=list(_JUDGE_CHOICES),
            model=_JUDGE_MODEL,
            eval_tags=["harness", "sub-goal"],
        )
    except Exception:  # noqa: BLE001 - a template we cannot store still gets a verdict below
        logger.exception("harness_judge_template_create_failed", template_name=name)
        return None


def _run_judge(instructions: str, record: str) -> tuple[bool, str] | None:
    """The verdict, or None when the judge did not run.

    None rather than a failure: an evaluator that could not answer says nothing about the agent,
    and recording it as a failed sub-goal invents a finding.
    """
    try:
        from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

        evaluator = AgentEvaluator(
            rule_prompt=instructions,
            model=_JUDGE_MODEL,
            output_type="choices",
            choices=list(_JUDGE_CHOICES),
            agent_mode="agent",
        )
        batch_result = evaluator.run(output=record, required_keys=["output"])
        data = batch_result.eval_results[0]["data"]
    except Exception:  # noqa: BLE001
        logger.exception("harness_judge_evaluator_failed")
        return None
    verdict = str(data.get("result") or "").strip().lower()
    if verdict not in _JUDGE_CHOICES:
        logger.warning("harness_judge_verdict_unrecognized", verdict=verdict[:120])
        return None
    return verdict == "pass", str(data.get("reason") or "")


def judge_sub_goal(
    call: CallExecution, claim: dict[str, Any], evidence: dict[str, Any]
) -> tuple[str, dict[str, Any] | None]:
    """One judged sub-goal, decided and shaped for ``CallExecution.eval_outputs``."""
    name = str(claim.get("name") or "")
    run_test = call.test_execution.run_test
    # One instruction text, used both to judge and to store, so the template on the platform is
    # always the question that actually produced the verdict.
    instructions = _instructions(claim, _agent_label(call))
    template = _ensure_template(call, claim, instructions)
    output_id = (
        str(template.id)
        if template is not None
        else str(uuid.uuid5(_HARNESS_EVAL_NAMESPACE, f"{run_test.id}:judge:{name}"))
    )
    record = json.dumps(
        {
            "calls": evidence.get("calls") or [],
            "world_afterwards": evidence.get("world") or {},
        },
        ensure_ascii=False,
        default=str,
    )
    decided = _run_judge(instructions, record)
    if decided is None:
        return output_id, None
    held, reason = decided
    return output_id, {
        "name": name,
        "output": "Passed" if held else "Failed",
        "output_type": "Pass/Fail",
        "reason": reason[:10000],
        "status": "completed",
        "source": "harness",
        "kind": "judge",
        "platform_template": template.name if template is not None else "",
    }
