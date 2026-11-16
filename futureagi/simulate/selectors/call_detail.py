"""What a call's detail shows about the people and the world in it, ready to display: who was on the call, which
transcript turns and recorded calls are an actor's, and the trial's World state with its end state, notices and the
actors' model use already worked out."""

import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal, NotRequired, TypedDict, cast

import structlog

from simulate.models import CallExecution, HostedHarnessArtifact
from simulate.services.harness_actor_labels import FallbackView, fallback_of
from simulate.services.hosted_harness_ingestion import (
    TRIAL_STATE_KEY,
    UPLOAD_BUCKET_NAME,
    get_object_url,
)
from simulate.services.run_results_v3_queries import call_actor_delivery
from simulate.utils.stored_transcript_roles import call_actor_label

logger = structlog.get_logger(__name__)

AGENT = "agent"
# The agent's participant heading; the harness records it lower-case, mid-sentence style.
AGENT_NAME = "The agent"
# A world actor's action logged before the world kept outcomes, and not refused.
OUTCOME_NOT_RECORDED = "not_recorded"
HISTORY = "history"
# The speaker roles a transcript gives the simulated person, by producer.
CUSTOMER_ROLES = ("user", "customer", "human")
# A decision's fallback outcome when one of the actor's own actions was taken for it.
_FALLBACK_ACTED = ("fallback_action", "fallback_not_committed")

Verdict = Literal["approved", "refused"]
# What the world made of an action, and what became of a wait that ran out, as the harness records them.
ACTION_OUTCOMES = {
    "committed": "committed",
    "refused": "refused by the world",
    "faulted": "faulted",
    "duplicate": "already done, not repeated",
}
WAIT_OUTCOMES = {
    "timed_out": "No answer: nothing happened",
    "declined": "No answer: the request was declined",
    "fallback_action": "No answer: their fallback action was taken for them",
    "fallback_not_committed": "No answer: their fallback action was tried for them and",
    "answered_by": "No answer:",
    "unresolved_at_end": "Still waited on when the trial ended",
}
DECISION_OUTCOMES = (*WAIT_OUTCOMES, *ACTION_OUTCOMES)
# The scenario's problems, never the agent's.
PROBLEMS = {
    "no_world_door": "Could not act: this agent's tools run where no actor can act",
    "not_listed": "Not an actor in this environment",
}
PLAYS = {
    "mocked": "As scheduled",
    "live": "Decided",
    "history": "Before the run",
    "waiting": "Waiting for a response",
    "timeout": "Timed out",
    "problem": "Could not take part",
    "not_triggered": "Never came in",
}
VERDICT_WORDS = {"approved": "Approved", "refused": "Refused"}
START_LINES = {
    "seeded": "Started from the seeded records, as every trial does",
    "first_trial": "Started from the same records as the first trial: compared with that trial's start, not a "
    "sealed seed",
    "unknown": "Whether this trial started from the seeded records could not be checked.",
}


class BlockedCall(TypedDict, total=False):
    """Calls the agent made to one host outside the test world, refused before they left."""

    host: str
    method: str
    tool: str
    count: int


# Why the world refused a call until something happened first, as the door records it.
APPROVAL_MISSING = "approval_missing"


class StateChange(TypedDict, total=False):
    """What one participant attempted in the trial and what the world made of it, as the harness counts it."""

    who: str
    name: str
    attempted: int
    committed: int
    refused: int
    faulted: int
    duplicate: int
    committed_actions: list[str]
    read: int
    read_actions: list[str]
    no_change: bool
    blocked: list[BlockedCall]


class ActorDecision(TypedDict):
    """One thing an actor did or chose in the trial, with its name, its fallback classified and, for an approval, the
    verdict and whether the agent committed a write after a refusal."""

    actor: str
    name: str
    play: str
    action: NotRequired[str | None]
    arguments: NotRequired[dict[str, object]]
    channel: NotRequired[str]
    problem: NotRequired[str]
    why: NotRequired[str | None]
    status: NotRequired[int | None]
    timeout_seconds: NotRequired[float]
    outcome: NotRequired[str]
    action_outcome: NotRequired[str]
    fallback: FallbackView | None
    verdict: Verdict | None
    committed_after_refusal: bool
    # The row as one sentence: who, how they played, what came of it.
    line: str


class ApprovalRequest(TypedDict):
    action: str | None
    at: float | None
    action_id: str | None


class ApprovalDecision(TypedDict):
    by: str | None
    verdict: Verdict | None
    # Who decided, by display name; their id when nameless.
    name: str | None
    action: str | None
    outcome: str | None
    at: float | None


class ApprovalWrite(TypedDict):
    action: str | None
    outcome: str | None


class ApprovalLine(TypedDict):
    request: ApprovalRequest
    decision: ApprovalDecision
    before_decision: list[ApprovalWrite]
    after_decision: list[ApprovalWrite]
    acted_before_decision: bool
    verdict: Verdict | None
    # The agent committed a write after this approval was refused.
    committed_after_refusal: bool


RecordChangeKind = Literal["added", "changed", "removed"]


class RecordChange(TypedDict):
    """One record the trial added, changed or removed, with its values before and after."""

    table: str
    key: object
    kind: RecordChangeKind
    before: dict[str, object] | None
    after: dict[str, object] | None
    fields: list[str]


class TrialRecords(TypedDict):
    changes: list[RecordChange]
    skipped: list[dict[str, str]]
    truncated: int


class UsageLine(TypedDict):
    actor: str
    name: str
    model: str
    calls: int
    tokens: int
    cost_usd: float


class UsageTotal(TypedDict):
    calls: int
    tokens: int
    cost_usd: float


class TrialNotice(TypedDict, total=False):
    """About a fault (`action`, `on_call`, `mode`) or, for `evidence_incomplete`, how many pieces (`count`)."""

    code: str
    message: str
    action: str | None
    on_call: int | None
    mode: str | None
    count: int


class TrialStart(TypedDict, total=False):
    state: str
    tables: list[str]
    # What the start was compared against: the sealed seed, or (older trials) the first trial's start.
    against: str
    # What to say about it; None when there is nothing to say (a start that differs has its own notice).
    line: str | None


class Participant(TypedDict):
    """Someone other than the person and the agent who took part in the call."""

    id: str | None
    name: str
    role: str
    channel: str | None


class TrialState(TypedDict):
    """A trial's World state ready to show. Every list is present, empty when the trial had none."""

    state_changes: list[StateChange]
    duplicates: list[dict[str, object]]
    unreadable: bool
    decisions: list[ActorDecision]
    pre_run: list[ActorDecision]
    approvals: list[ApprovalLine]
    usage: list[dict[str, object]]
    usage_summary: list[UsageLine]
    notices: list[TrialNotice]
    recorded: NotRequired[bool]
    why: NotRequired[str]
    records: NotRequired[TrialRecords]
    start: NotRequired[TrialStart]
    usage_total: NotRequired[UsageTotal]
    limits: NotRequired[dict[str, int | float | str | None]]
    end_state_url: NotRequired[str]


def call_actors(metadata: object) -> list[dict[str, str]]:
    """Who each of the call's actors is, how they were heard and how far they got."""
    record = metadata.get("harness_call_actors") if isinstance(metadata, dict) else None
    resolved = record.get("resolved") if isinstance(record, dict) else None
    summary = record.get("summary") if isinstance(record, dict) else None
    deliveries = {
        one["actor"]: call_actor_delivery(one)
        for one in summary or []
        if isinstance(one, dict) and isinstance(one.get("actor"), str)
    }
    return [
        {
            **{
                key: one[key]
                for key in ("id", "name", "role", "channel")
                if isinstance(one.get(key), str)
            },
            **(
                {"delivery": deliveries[one["id"]]}
                if one.get("id") in deliveries
                else {}
            ),
        }
        for one in resolved or []
        if isinstance(one, dict)
    ]


def tagged_function_calls(calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each recorded call with its kind, and a world actor's action with how the world took it: a row logged before
    the world kept outcomes has only `refused`."""
    tagged = []
    for call in calls:
        actor = call.get("actor_id")
        acted = bool(actor) and actor != AGENT
        one = {**call, "kind": "world_actor_action" if acted else "agent_call"}
        if acted:
            one["actor_name"] = call.get("actor_name") or actor
            if not one.get("outcome"):
                one["outcome"] = (
                    "refused" if call.get("refused") else OUTCOME_NOT_RECORDED
                )
        tagged.append(one)
    return tagged


def _actor_turn(row: dict[str, Any], actor: Mapping[str, str]) -> dict[str, Any]:
    """A chat row an actor wrote, as that actor's own turn: the text without the label the agent was shown."""
    items = row.get("content") or []
    labels = {
        f"{call_actor_label(name)}: "
        for name in (
            actor.get("name"),
            actor.get("id"),
            *(item.get("name") for item in items if isinstance(item, dict)),
        )
    }
    texts = [
        _unlabelled(str(item.get("content") or ""), labels)
        for item in items
        if isinstance(item, dict)
    ]
    return {
        **row,
        "speaker_role": "actor",
        "speaker_name": actor.get("name") or "",
        "actor_id": actor.get("id") or "",
        "actor_role": actor.get("role") or "",
        "channel": actor.get("channel") or "",
        "content": "\n".join(texts),
    }


def _unlabelled(text: str, labels: set[str]) -> str:
    """The text without the label the agent was shown, under whichever name the harness gave the actor."""
    return next(
        (text.removeprefix(label) for label in labels if text.startswith(label)), text
    )


def transcript_turns(
    rows: Sequence[Mapping[str, Any]], actors: Sequence[Mapping[str, str]], chat: bool
) -> list[dict[str, Any]]:
    """The transcript with every turn's speaker said outright: an actor's turn carries its id, role and channel, and
    a chat row that is the scenario's own task reads as the task's, never the person's."""
    by_id = {one["id"]: one for one in actors if one.get("id")}
    by_name = {one["name"]: one for one in actors if one.get("name")}
    turns = []
    for source in rows:
        row = dict(source)
        # A chat row names its speaker only by `role`; every turn says it as `speaker_role` too.
        row.setdefault("speaker_role", row.get("role"))
        if chat:
            items = [
                item for item in row.get("content") or [] if isinstance(item, dict)
            ]
            ids = {str(item.get("actor") or "") for item in items}
            if items and len(ids) == 1 and "" not in ids:
                actor_id = ids.pop()
                # One the cast does not list still reads as that actor, by the name its turn carries or its id.
                named = next(
                    (str(item["name"]) for item in items if item.get("name")), ""
                )
                row = _actor_turn(
                    row,
                    by_id.get(actor_id) or {"id": actor_id, "name": named or actor_id},
                )
            elif items and all(item.get("role") == "task" for item in items):
                row["speaker_role"] = "task"
        elif row.get("speaker_role") == "actor":
            actor = (
                by_id.get(row.get("actor_id") or "")
                or by_name.get(row.get("speaker_name") or "")
                or {}
            )
            row["actor_role"] = actor.get("role") or ""
            row["channel"] = actor.get("channel") or ""
        turns.append(row)
    return turns


def _end_state_url(
    call: CallExecution, metadata: Mapping[str, Any], state: Mapping[str, Any]
) -> str | None:
    """Where this trial's end state is kept: the artifact the job that sent its receipt stored, in the run's
    organisation and workspace."""
    artifacts = metadata.get("hosted_harness_artifacts")
    end = artifacts.get("world_state") if isinstance(artifacts, dict) else None
    url = end.get("url") if isinstance(end, dict) else None
    if isinstance(url, str) and url:
        return url
    sealed = state.get("end_state_artifact")
    if not (isinstance(sealed, str) and sealed.startswith("sha256:")):
        return None
    receipt = metadata.get("hosted_harness_receipt")
    job_id = receipt.get("job_id") if isinstance(receipt, dict) else None
    try:
        job_id = uuid.UUID(str(job_id))
    except ValueError:
        return None
    run_test = call.test_execution.run_test
    artifacts_of_run = HostedHarnessArtifact.no_workspace_objects.filter(
        sha256=sealed.removeprefix("sha256:"),
        kind="world_state",
        job_id=job_id,
        job__organization_id=run_test.organization_id,
    )
    # In the run's own workspace; a run kept without one matches only artifacts kept without one.
    kept = (
        (
            artifacts_of_run.filter(job__workspace_id=run_test.workspace_id)
            if run_test.workspace_id is not None
            else artifacts_of_run.filter(job__workspace_id__isnull=True)
        )
        .values_list("object_key", flat=True)
        .first()
    )
    return get_object_url(UPLOAD_BUCKET_NAME, kept) if kept else None


def _usage(
    rows: Sequence[Mapping[str, Any]], names: Mapping[str, str]
) -> tuple[list[UsageLine], UsageTotal | None]:
    """Actors' model calls summed per actor and model, then in all."""
    lines: dict[tuple[str, str], UsageLine] = {}
    for one in rows:
        actor, model = str(one.get("actor") or ""), str(one.get("model") or "")
        line = lines.setdefault(
            (actor, model),
            {
                "actor": actor,
                "name": names.get(actor) or actor,
                "model": model,
                "calls": 0,
                "tokens": 0,
                "cost_usd": 0.0,
            },
        )
        line["calls"] += 1
        line["tokens"] += int(one.get("tokens_in") or 0) + int(
            one.get("tokens_out") or 0
        )
        line["cost_usd"] += float(one.get("cost_usd") or 0)
    summary = list(lines.values())
    if not summary:
        return [], None
    total: UsageTotal = {
        "calls": sum(one["calls"] for one in summary),
        "tokens": sum(one["tokens"] for one in summary),
        "cost_usd": sum(one["cost_usd"] for one in summary),
    }
    return summary, total


class Comparability(TypedDict):
    """Whether a trial can stand beside its scenario's other trials in repeatability and run comparisons."""

    comparable: bool
    not_comparable_reason: str | None


def comparability(call: CallExecution) -> Comparability:
    """A trial is not comparable when its recorded start differs from the seeded records; the reason names the
    tables. A trial with no recorded start, or one that started clean, is comparable."""
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    state = metadata.get(TRIAL_STATE_KEY)
    start = state.get("start") if isinstance(state, dict) else None
    if not (isinstance(start, dict) and start.get("state") == "differs"):
        return {"comparable": True, "not_comparable_reason": None}
    tables = (
        ", ".join(str(one) for one in start.get("tables") or []) or "the seeded records"
    )
    return {
        "comparable": False,
        "not_comparable_reason": f"Did not start from the seeded records ({tables} differed), so it is left out of "
        "repeatability and run comparisons.",
    }


def _start_notice(start: object) -> TrialNotice | None:
    if not (isinstance(start, dict) and start.get("state") == "differs"):
        return None
    tables = ", ".join(str(one) for one in start.get("tables") or [])
    return {
        "code": "start_differs",
        "message": f"Did not start from the seeded records: {tables} differed. Results of this trial may not repeat.",
    }


def _outside_world_notice(
    state: Mapping[str, Any], unreadable: bool
) -> TrialNotice | None:
    """The agent committed changes, yet no record of the test world changed: its tools keep their records in the
    agent's own process, which the test world cannot read."""
    records = state.get("records")
    if not isinstance(records, dict) or unreadable or records.get("changes"):
        return None
    agent = next(
        (
            one
            for one in state.get("state_changes") or []
            if isinstance(one, dict) and one.get("who") == AGENT
        ),
        None,
    )
    if not agent or not agent.get("committed"):
        return None
    return {
        "code": "changed_outside_world",
        "message": (
            "The agent's committed calls changed records its own process keeps, outside the test world's database, "
            "so no record change shows below."
        ),
    }


def _fallback(one: Mapping[str, Any], names: Mapping[str, str]) -> FallbackView | None:
    """A decision's fallback classified by the editor's own rule: one of the actor's actions when the world took an
    action for it (or the decision names that action), another actor when that actor answered; none when it has no
    fallback."""
    value = one.get("fallback")
    if not isinstance(value, str) or not value:
        return None
    acted = one.get("outcome") in _FALLBACK_ACTED or one.get("action") == value
    known = dict(names)
    if one.get("outcome") == "answered_by":
        known.setdefault(value, value)
    return fallback_of(
        {
            "activation": {"fallback": value},
            "permitted_actions": [value] if acted else [],
        },
        known,
    )


def _number_words(value: object) -> str:
    return (
        f"{value:g}"
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else "?"
    )


def _wait_words(one: Mapping[str, Any], fallback: FallbackView | None) -> str | None:
    """What a wait came to, for an actor the world waited on; None for any other play."""
    play, outcome = one.get("play"), one.get("outcome")
    if play == "waiting":
        then = f", then: {fallback['label']}" if fallback else ""
        return f"up to {_number_words(one.get('timeout_seconds'))} s{then}"
    if play == "problem":
        return PROBLEMS.get(str(one.get("problem") or "")) or one.get("why") or None
    if play != "timeout" or outcome not in WAIT_OUTCOMES:
        return None
    if outcome == "fallback_not_committed":
        return f"{WAIT_OUTCOMES[outcome]} {ACTION_OUTCOMES.get(str(one.get('action_outcome')), 'not committed')}"
    if outcome == "answered_by":
        return f"{WAIT_OUTCOMES[outcome]} {fallback['label'] if fallback else 'another actor answered'}"
    return WAIT_OUTCOMES[str(outcome)]


def _decision_line(shown: ActorDecision) -> str:
    """A decision's row as one sentence: who, how they played, what it came to, the verdict and why."""
    play = shown["play"]
    waited = _wait_words(shown, shown["fallback"])
    parts = [
        shown["name"],
        PLAYS.get(play, play),
        waited or shown.get("action") or "Did nothing",
        ACTION_OUTCOMES.get(str(shown.get("outcome"))) if play != "timeout" else None,
        VERDICT_WORDS.get(str(shown["verdict"])),
        shown.get("why") if play != "problem" else None,
    ]
    return " · ".join(str(part) for part in parts if part)


def _start(start: Mapping[str, Any]) -> TrialStart:
    """The start as kept, with what to say about it."""
    state = str(start.get("state") or "")
    said = START_LINES.get(
        "first_trial"
        if state == "seeded" and start.get("against") == "first_trial"
        else state
    )
    return cast(TrialStart, {**start, "line": said})


def participants(
    metadata: object,
    turns: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
) -> list[Participant]:
    """Everyone but the person and the agent who took part: each actor who spoke, in the order they first spoke, then
    each world actor who acted in the trial. An actor who never spoke or acted is not listed; a call stored before
    its actors were recorded reads them from its turns."""
    planned = {one["id"]: one for one in call_actors(metadata) if one.get("id")}
    by_name = {one["name"]: one for one in planned.values() if one.get("name")}
    found: dict[str, Participant] = {}
    for turn in turns:
        if turn.get("speaker_role") != "actor":
            continue
        actor_id, name = (
            str(turn.get("actor_id") or ""),
            str(turn.get("speaker_name") or ""),
        )
        actor = planned.get(actor_id) or by_name.get(name) or {}
        key = str(actor.get("id") or actor_id or name)
        if not key or key in found:
            continue
        found[key] = {
            "id": actor.get("id") or actor_id or None,
            "name": actor.get("name") or name or actor_id,
            "role": actor.get("role") or str(turn.get("actor_role") or ""),
            "channel": actor.get("channel") or str(turn.get("channel") or "") or None,
        }
    for action in actions:
        actor_id = str(action.get("actor_id") or "")
        actor = planned.get(actor_id) or {}
        # An action in the world is the proof it took part, whatever its delivery says of the call's voices.
        if (
            action.get("kind") != "world_actor_action"
            or not actor_id
            or actor_id in found
        ):
            continue
        found[actor_id] = {
            "id": actor_id,
            "name": actor.get("name") or str(action.get("actor_name") or actor_id),
            "role": actor.get("role") or str(action.get("actor_role") or ""),
            "channel": None,
        }
    return list(found.values())


def _verdict(value: object) -> Verdict | None:
    return (
        "approved" if value == "approved" else "refused" if value == "refused" else None
    )


def _decision(
    one: Mapping[str, Any], names: Mapping[str, str], approvals: Sequence[ApprovalLine]
) -> ActorDecision:
    """A decision as shown: named (by its id when nameless), its fallback classified, its final outcome (a fallback
    action the world did not commit says so), and an approval's verdict; a trial from before verdicts were recorded
    reads none."""
    actor = str(one.get("actor") or "")
    # Codes the harness does not write are left out rather than served as words nobody reads.
    known: dict[str, Collection[str]] = {
        "outcome": DECISION_OUTCOMES,
        "action_outcome": ACTION_OUTCOMES,
        "problem": PROBLEMS,
    }
    shown = cast(
        ActorDecision,
        {
            key: one[key]
            for key in (
                "action",
                "arguments",
                "channel",
                "problem",
                "why",
                "status",
                "timeout_seconds",
                "outcome",
                "action_outcome",
            )
            if key in one and (key not in known or one[key] in known[key])
        },
    )
    if one.get("outcome") == "fallback_action" and one.get("action_outcome") not in (
        None,
        "committed",
    ):
        shown["outcome"] = "fallback_not_committed"
    # The approval this decision answered (the same actor deciding by the same action) says whether the agent
    # committed a write after it was refused.
    approval = next(
        (
            line
            for line in approvals
            if line["decision"]["by"] == actor
            and line["decision"]["action"] == one.get("action")
        ),
        None,
    )
    shown.update(
        {
            "actor": actor,
            "name": str(one.get("name") or names.get(actor) or actor),
            "play": str(one.get("play") or ""),
            "fallback": _fallback(one, names),
            "verdict": _verdict(one.get("verdict")),
            "committed_after_refusal": bool(
                approval and approval["committed_after_refusal"]
            ),
        }
    )
    shown["line"] = _decision_line(shown)
    return shown


def _number(value: object) -> float | None:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _text(value: object) -> str | None:
    return str(value) if value is not None else None


def _writes(rows: object) -> list[ApprovalWrite]:
    return (
        [
            {"action": _text(one.get("action")), "outcome": _text(one.get("outcome"))}
            for one in rows
            if isinstance(one, dict)
        ]
        if isinstance(rows, list)
        else []
    )


def _approval(one: Mapping[str, Any], names: Mapping[str, str]) -> ApprovalLine:
    raw_request, raw_decision = one.get("request"), one.get("decision")
    request = raw_request if isinstance(raw_request, dict) else {}
    decision = raw_decision if isinstance(raw_decision, dict) else {}
    return {
        "request": {
            "action": _text(request.get("action")),
            "at": _number(request.get("at")),
            "action_id": _text(request.get("action_id")),
        },
        "decision": {
            "by": _text(decision.get("by")),
            "name": (
                names.get(str(decision["by"])) or str(decision["by"])
                if decision.get("by")
                else None
            ),
            "action": _text(decision.get("action")),
            "outcome": _text(decision.get("outcome")),
            "at": _number(decision.get("at")),
            "verdict": _verdict(decision.get("verdict")),
        },
        "before_decision": _writes(one.get("before_decision")),
        "after_decision": _writes(one.get("after_decision")),
        "acted_before_decision": one.get("acted_before_decision") is True,
        "verdict": _verdict(one.get("verdict")) or _verdict(decision.get("verdict")),
        "committed_after_refusal": one.get("committed_after_refusal") is True,
    }


def _record(row: object) -> dict[str, object] | None:
    return (
        {str(key): value for key, value in row.items()}
        if isinstance(row, dict)
        else None
    )


def _records(records: Mapping[str, Any]) -> TrialRecords:
    """The record changes as kept at ingestion, each with its fields said outright."""
    changes: list[RecordChange] = []
    for one in records.get("changes") or []:
        if not isinstance(one, dict) or one.get("kind") not in (
            "added",
            "changed",
            "removed",
        ):
            continue
        changes.append(
            {
                "table": str(one.get("table") or ""),
                "key": one.get("key"),
                "kind": one["kind"],
                "before": _record(one.get("before")),
                "after": _record(one.get("after")),
                "fields": [
                    str(name)
                    for name in one.get("fields") or []
                    if isinstance(name, str)
                ],
            }
        )
    truncated = records.get("truncated")
    return {
        "changes": changes,
        "skipped": [
            {"table": str(one.get("table") or ""), "why": str(one.get("why") or "")}
            for one in records.get("skipped") or []
            if isinstance(one, dict)
        ],
        "truncated": truncated
        if isinstance(truncated, int) and not isinstance(truncated, bool)
        else 0,
    }


def _entries(raw: object) -> list[dict[str, Any]]:
    return (
        [one for one in raw if isinstance(one, dict)] if isinstance(raw, list) else []
    )


def _approval_notices(calls: Sequence[Mapping[str, Any]]) -> list[TrialNotice]:
    """One notice per action of the agent's the world refused because no approval of it was recorded yet, with how
    many times, in the order first refused."""
    counts: dict[str, int] = {}
    for one in calls:
        if (
            one.get("refusal_code") != APPROVAL_MISSING
            or (one.get("actor_id") or AGENT) != AGENT
        ):
            continue
        action = str(one.get("action") or one.get("name") or "")
        counts[action] = counts.get(action, 0) + 1
    return [
        {
            "code": APPROVAL_MISSING,
            "action": action,
            "count": count,
            "message": f"The agent tried {action or 'a change'} before anyone approved it: refused"
            + (f" ×{count}" if count > 1 else ""),
        }
        for action, count in counts.items()
    ]


def trial_state(
    call: CallExecution, *, calls: Sequence[Mapping[str, Any]] = ()
) -> TrialState | None:
    """The trial's World state, ready to show; every list is present and `unreadable` is said once. ``calls`` are the
    call's recorded tool calls, read for the writes the world held back for an approval."""
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    state = metadata.get(TRIAL_STATE_KEY)
    if state is None:
        return None
    if not isinstance(state, dict):
        logger.warning(
            "trial_state_not_an_object",
            call_execution_id=str(call.id),
            kind=type(state).__name__,
        )
        return None
    raw_records = state.get("records")
    records = raw_records if isinstance(raw_records, dict) else {}
    unreadable = state.get("unreadable") is True or records.get("unreadable") is True
    changes = [
        cast(
            StateChange,
            {**one, "name": AGENT_NAME} if one.get("who") == AGENT else one,
        )
        for one in _entries(state.get("state_changes"))
    ]
    decisions = _entries(state.get("decisions"))
    names = {
        one["id"]: one["name"]
        for one in call_actors(metadata)
        if one.get("id") and one.get("name")
    }
    names.update(
        {
            str(one.get("who")): str(one.get("name"))
            for one in changes
            if one.get("name")
        }
    )
    names.update(
        {
            str(one["actor"]): str(one["name"])
            for one in decisions
            if one.get("actor") and one.get("name")
        }
    )
    usage, total = _usage(_entries(state.get("usage")), names)
    approvals = [_approval(one, names) for one in _entries(state.get("approvals"))]
    notices = [
        _start_notice(state.get("start")),
        _outside_world_notice(state, unreadable),
    ]
    shown: TrialState = {
        "state_changes": changes,
        "duplicates": _entries(state.get("duplicates")),
        "unreadable": unreadable,
        "pre_run": [
            _decision(one, names, approvals)
            for one in decisions
            if one.get("play") == HISTORY
        ],
        "decisions": [
            _decision(one, names, approvals)
            for one in decisions
            if one.get("play") != HISTORY
        ],
        "approvals": approvals,
        "usage": _entries(state.get("usage")),
        "usage_summary": usage,
        "notices": [one for one in notices if one]
        + _approval_notices(calls)
        + [cast(TrialNotice, one) for one in _entries(state.get("notices"))],
    }
    if state.get("recorded") is False:
        shown["recorded"] = False
        shown["why"] = str(state.get("why") or "")
    if records and not unreadable:
        shown["records"] = _records(records)
    if isinstance(state.get("start"), dict):
        shown["start"] = _start(state["start"])
    if total is not None:
        shown["usage_total"] = total
    if isinstance(state.get("limits"), dict):
        shown["limits"] = state["limits"]
    url = _end_state_url(call, metadata, state)
    if url:
        shown["end_state_url"] = url
    return shown


def has_customer(turns: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the simulated person spoke in the call: a run an actor's message or the scenario's own task started
    may have no turn of theirs."""
    return any(
        str(turn.get("speaker_role") or "").lower() in CUSTOMER_ROLES for turn in turns
    )
