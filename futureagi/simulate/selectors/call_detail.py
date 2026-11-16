"""What a call's detail shows about the people and the world in it, ready to display: who was on the call, which
transcript turns and recorded calls are an actor's, and the trial's World state with its end state, notices and the
actors' model use already worked out."""

from collections.abc import Mapping, Sequence
from typing import Any

import structlog

from simulate.models import CallExecution, HostedHarnessArtifact
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
    label = f"{call_actor_label(actor.get('name'))}: "
    items = row.get("content") or []
    texts = [str(item.get("content") or "") for item in items if isinstance(item, dict)]
    return {
        **row,
        "speaker_role": "actor",
        "speaker_name": actor.get("name") or "",
        "actor_id": actor.get("id") or "",
        "actor_role": actor.get("role") or "",
        "channel": actor.get("channel") or "",
        "content": "\n".join(text.removeprefix(label) for text in texts),
    }


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
                actor = by_id.get(ids.pop())
                if actor is not None:
                    row = _actor_turn(row, actor)
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
    """Where this trial's end state is kept: the artifact its own run stored in its organisation and workspace."""
    artifacts = metadata.get("hosted_harness_artifacts")
    end = artifacts.get("world_state") if isinstance(artifacts, dict) else None
    url = end.get("url") if isinstance(end, dict) else None
    if url:
        return url
    sealed = state.get("end_state_artifact")
    if not (isinstance(sealed, str) and sealed.startswith("sha256:")):
        return None
    run_test = call.test_execution.run_test
    kept = (
        HostedHarnessArtifact.no_workspace_objects.filter(
            sha256=sealed.removeprefix("sha256:"),
            kind="world_state",
            job__payload__metadata__platform_run_id=str(call.test_execution_id),
            job__organization_id=run_test.organization_id,
            job__workspace_id=run_test.workspace_id,
        )
        .values_list("object_key", flat=True)
        .first()
    )
    return get_object_url(UPLOAD_BUCKET_NAME, kept) if kept else None


def _usage(
    rows: Sequence[Mapping[str, Any]], names: Mapping[str, str]
) -> tuple[list[dict[str, Any]], dict | None]:
    """Actors' model calls summed per actor and model, then in all."""
    lines: dict[tuple[str, str], dict[str, Any]] = {}
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
    total = {
        "calls": sum(one["calls"] for one in summary),
        "tokens": sum(one["tokens"] for one in summary),
        "cost_usd": sum(one["cost_usd"] for one in summary),
    }
    return summary, total


def _start_notice(start: object) -> dict[str, str] | None:
    if not (isinstance(start, dict) and start.get("state") == "differs"):
        return None
    tables = ", ".join(str(one) for one in start.get("tables") or [])
    return {
        "code": "start_differs",
        "message": f"Did not start from the seeded records: {tables} differed. Results of this trial may not repeat.",
    }


def _outside_world_notice(state: Mapping[str, Any]) -> dict[str, str] | None:
    """The agent committed changes, yet no record of the test world changed: its tools keep their records in the
    agent's own process, which the test world cannot read."""
    records = state.get("records")
    if (
        not isinstance(records, dict)
        or records.get("unreadable")
        or records.get("changes")
    ):
        return None
    agent = next(
        (one for one in state.get("state_changes") or [] if one.get("who") == AGENT),
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


def _decision(one: Mapping[str, Any]) -> dict[str, Any]:
    """A decision with its final outcome: a fallback action the world did not commit says so."""
    if one.get("outcome") == "fallback_action" and one.get("action_outcome") not in (
        None,
        "committed",
    ):
        return {**one, "outcome": "fallback_not_committed"}
    return dict(one)


def trial_state(call: CallExecution) -> dict[str, Any] | None:
    """The trial's World state, ready to show: what each participant did, the records before and after, messages
    written before the run apart from choices made in it, notices first, actors' model use summed, and where the
    trial's end state is kept."""
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
    shown = dict(state)
    shown["state_changes"] = [
        {**one, "name": AGENT_NAME} if one.get("who") == AGENT else one
        for one in state.get("state_changes") or []
        if isinstance(one, dict)
    ]
    decisions = [one for one in state.get("decisions") or [] if isinstance(one, dict)]
    shown["pre_run"] = [
        _decision(one) for one in decisions if one.get("play") == HISTORY
    ]
    shown["decisions"] = [
        _decision(one) for one in decisions if one.get("play") != HISTORY
    ]
    names = {
        str(one.get("who")): str(one.get("name"))
        for one in state.get("state_changes") or []
        if isinstance(one, dict) and one.get("name")
    }
    names.update(
        {
            str(one["actor"]): str(one["name"])
            for one in decisions
            if one.get("actor") and one.get("name")
        }
    )
    shown["usage_summary"], total = _usage(
        [one for one in state.get("usage") or [] if isinstance(one, dict)], names
    )
    if total is not None:
        shown["usage_total"] = total
    notices = [_start_notice(state.get("start")), _outside_world_notice(state)]
    shown["notices"] = [one for one in notices if one] + list(
        state.get("notices") or []
    )
    url = _end_state_url(call, metadata, state)
    if url:
        shown["end_state_url"] = url
    return shown
