"""Starting a scenario from another seeded record: a request to the environment's builder to rework the scenario
around it (caller's facts, setup and checks together, then a proof), and the state its turn left. The builder's own
turn writes the scenario, into the unsaved changes; nothing here does."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

import structlog
from django.db import transaction

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.services.harness_actor_validation import Problem, problem
from simulate.services.harness_starting_records import (
    JsonValue,
    ReworkRequest,
    Row,
    ScenarioFiles,
    StartingRecord,
    archive_scenario_files,
    scenario_files,
    scenario_records,
    scenario_token,
    seeded_row,
)
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import PROOF_CLOCK_SKEW
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

# Marks the builder messages this module files, so their turns can be found again.
REWORK_ORIGIN = "starting_record"
_Message = HostedHarnessConversationMessage
_RUNNING = (_Message.State.QUEUED, _Message.State.DELIVERED, _Message.State.STREAMING)


def rework_invalid(problems: list[Problem]) -> HostedHarnessError:
    return HostedHarnessError(
        "starting_record_invalid",
        " ".join(one["message"] for one in problems),
        status_code=400,
        details={"problems": problems},
    )


def _proof(files: ScenarioFiles, scenario_key: str) -> dict[str, object] | None:
    """The scenario's latest proof, from the `proofs.json` the builder saves beside the suite."""
    try:
        proofs = json.loads(files.suite.get("proofs.json", b"{}").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(proofs, dict):
        return None
    wanted = scenario_token(scenario_key)
    return next(
        (
            proof
            for key, proof in proofs.items()
            if scenario_token(key) == wanted and isinstance(proof, dict)
        ),
        None,
    )


def _proved_at(proof: dict[str, object]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(proof.get("proved_at") or ""))
    except ValueError:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _published(
    message: HostedHarnessConversationMessage, turn: HostedHarnessConversationEvent
) -> bool:
    """Whether the turn's save has landed: the environment records each chat's last published checkpoint, and the
    turn's own is the last it committed before it ended (as `promote_turn_checkpoint` picks it). A turn that
    committed none saved nothing, so nothing is still to land."""
    committed = HostedHarnessConversationEvent.no_workspace_objects.filter(
        conversation_id=message.conversation_id,
        kind="checkpoint_committed",
        sequence__lt=turn.sequence,
        payload__has_key="digest",
    )
    if turn.invocation_id:
        committed = committed.filter(invocation_id=turn.invocation_id)
    last = committed.order_by("-sequence").values_list("sequence", flat=True).first()
    if last is None:
        return True
    job = HostedHarnessJob.no_workspace_objects.select_related("environment").get(
        id=message.conversation.job_id
    )
    environment = job.environment or job
    checkpoints = ((environment.payload or {}).get("metadata") or {}).get(
        "conversation_checkpoints"
    ) or {}
    published = checkpoints.get(str(message.conversation_id)) or {}
    return int(published.get("sequence") or 0) >= last


def _not_saved(message: HostedHarnessConversationMessage) -> str | None:
    """What the chat said when this request's own save was refused: a failed reply before the next request."""
    replies = _Message.no_workspace_objects.filter(
        conversation_id=message.conversation_id,
        role=_Message.Role.ASSISTANT,
        state=_Message.State.FAILED,
        sequence__gt=message.sequence,
    )
    next_request = (
        _Message.no_workspace_objects.filter(
            conversation_id=message.conversation_id,
            role=_Message.Role.USER,
            sequence__gt=message.sequence,
        )
        .order_by("sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    if next_request is not None:
        replies = replies.filter(sequence__lt=next_request)
    return replies.order_by("sequence").values_list("content", flat=True).first()


def _asking(message: HostedHarnessConversationMessage) -> str | None:
    """The builder's open question or confirmation on this request's turn, which waits for the person: its message
    id; None when the chat is not waiting on this request."""
    conversation = HostedHarnessConversation.no_workspace_objects.filter(
        id=message.conversation_id,
        state=HostedHarnessConversation.State.WAITING_FOR_USER,
    ).first()
    asked = (
        str((conversation.blocking_input or {}).get("message_id") or "")
        if conversation
        else ""
    )
    if not asked:
        return None
    later = _Message.no_workspace_objects.filter(
        conversation_id=message.conversation_id,
        role=_Message.Role.USER,
        sequence__gt=message.sequence,
    )
    asked_at = (
        _Message.no_workspace_objects.filter(
            id=asked, conversation_id=message.conversation_id
        )
        .values_list("sequence", flat=True)
        .first()
    )
    if asked_at is None or asked_at < message.sequence:
        return None
    # Asked on a later request's turn: that request's, not this one's.
    if later.filter(sequence__lt=asked_at).exclude(reply_to__isnull=False).exists():
        return None
    return asked


def _reply(
    message: HostedHarnessConversationMessage,
) -> HostedHarnessConversationMessage | None:
    """The builder's answer to this request: its last reply before the next request."""
    replies = _Message.no_workspace_objects.filter(
        conversation_id=message.conversation_id,
        role=_Message.Role.ASSISTANT,
        state=_Message.State.COMPLETED,
        sequence__gt=message.sequence,
    )
    next_request = (
        _Message.no_workspace_objects.filter(
            conversation_id=message.conversation_id,
            role=_Message.Role.USER,
            sequence__gt=message.sequence,
        )
        .order_by("sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    if next_request is not None:
        replies = replies.filter(sequence__lt=next_request)
    said = replies.order_by("-sequence").first()
    return said if said is not None and str(said.content or "").strip() else None


def _same(value: JsonValue, seeded: JsonValue) -> bool:
    numbers = [
        one
        for one in (value, seeded)
        if isinstance(one, (int, float, str)) and not isinstance(one, bool)
    ]
    if len(numbers) == 2:
        try:
            return float(str(value)) == float(str(seeded))
        except ValueError:
            pass
    return str(value).strip() == str(seeded).strip()


def _not_as_seeded(
    files: ScenarioFiles, records: list[StartingRecord], wanted: Mapping[str, object]
) -> str | None:
    """Why the facts kept beside the chosen record are not that record as seeded: a fact that is not one of its
    fields, or a field with another value. None when every one is the record's own."""
    record = next(
        (
            one
            for one in records
            if one["table"] == wanted["table"]
            and one["key"] == wanted["key"]
            and one["status"] == "seeded"
        ),
        None,
    )
    row = record["fields"] if record is not None else None
    if record is None or row is None:
        return None
    block: JsonValue = files.document.get("fixture")
    for segment in record["path"][:-1]:
        block = (
            block.get(segment)
            if isinstance(block, dict)
            else block[int(segment)]
            if isinstance(block, list)
            and segment.isdigit()
            and int(segment) < len(block)
            else None
        )
    if not isinstance(block, dict):
        return None
    named = f"{record['table']} {record['key']}"
    for name, value in block.items():
        if name in ("origin", record["path"][-1]) or isinstance(value, (dict, list)):
            continue
        if name not in row:
            return (
                f"{name} is not a field of {named}, so it is not the record as seeded."
            )
        if not _same(value, row[name]):
            return f"{name} is {value} in the scenario, but {named} is seeded with {row[name]}."
    return None


def _outcome(
    message: HostedHarnessConversationMessage,
    files: ScenarioFiles,
    records: list[StartingRecord],
) -> tuple[Literal["pending", "proven", "refused"], str | None]:
    """Pending until the turn's save has landed with a proof made after the request. Refused only when the builder
    failed, its save was refused, the proof fails, or its landed change does not start from the record or wasn't
    proven. Proven when the scenario starts from the record and that proof holds."""
    if message.state in _RUNNING:
        return "pending", None
    turn = (
        HostedHarnessConversationEvent.no_workspace_objects.filter(
            conversation_id=message.conversation_id,
            kind="turn_completed",
            payload__command_message_id=str(message.id),
        )
        .order_by("-sequence")
        .first()
    )
    if message.state == _Message.State.FAILED or (
        turn is not None and (turn.payload or {}).get("outcome") != "success"
    ):
        return "refused", "The builder couldn't finish this request."
    if turn is None:
        return "pending", None
    refused_save = _not_saved(message)
    if refused_save:
        return "refused", refused_save
    if not _published(message, turn):
        return "pending", None
    wanted = message.payload["_record"]
    proof = _proof(files, message.payload["_scenario_key"])
    proved = _proved_at(proof) if proof is not None else None
    fresh = proved is not None and proved >= message.created_at - PROOF_CLOCK_SKEW
    if fresh and proof is not None and proof.get("holds") is not True:
        return "refused", str(
            proof.get("why") or ""
        ) or "The reworked scenario didn't pass its proof."
    starts = any(
        one["table"] == wanted["table"]
        and one["key"] == wanted["key"]
        and one["status"] == "seeded"
        for one in records
    )
    if not starts:
        if _reply(message) is not None:
            return (
                "refused",
                f"The builder says this scenario can't start from {wanted['table']} {wanted['key']} as seeded.",
            )
        return (
            "refused",
            "The builder's change did not start the scenario from that record.",
        )
    invented = _not_as_seeded(files, records, wanted)
    if invented:
        return "refused", invented
    if not fresh:
        return "refused", "The builder's change wasn't proven."
    return "proven", None


def _request(
    message: HostedHarnessConversationMessage,
    files: ScenarioFiles,
    records: list[StartingRecord],
) -> ReworkRequest:
    record = message.payload["_record"]
    asking = _asking(message)
    state, reason = ("waiting", None) if asking else _outcome(message, files, records)
    # A refusal the builder explained keeps its own words, to show in full beside the short reason.
    answer = _reply(message) if state == "refused" else None
    return ReworkRequest(
        id=str(message.id),
        state=state,
        table=str(record["table"]),
        key_field=str(record["key_field"]),
        key=str(record["key"]),
        reason=reason,
        requested_at=message.created_at.isoformat(),
        confirm_message_id=asking,
        builder_reply=str(answer.content) if answer is not None else None,
        reply_message_id=str(answer.id) if answer is not None else None,
    )


def _latest_message(
    job: HostedHarnessJob, scenario_key: str
) -> HostedHarnessConversationMessage | None:
    return (
        _Message.no_workspace_objects.filter(
            conversation__job=job,
            role=_Message.Role.USER,
            payload___origin=REWORK_ORIGIN,
            payload___scenario_key=scenario_token(scenario_key),
        )
        .order_by("-sequence")
        .first()
    )


def latest_rework(
    job: HostedHarnessJob, files: ScenarioFiles, records: list[StartingRecord]
) -> ReworkRequest | None:
    """The scenario's latest request to start from another record, as its builder turn left it; none if never asked."""
    key = str(files.document.get("scenario_key") or files.document.get("name") or "")
    message = _latest_message(job, key)
    return _request(message, files, records) if message is not None else None


def _current_document(environment: HostedHarnessJob, scenario_key: str) -> object:
    """The scenario as its unsaved changes, else its saved version, hold it now; None when it has none."""
    try:
        return scenario_files(environment, scenario_key).document
    except HostedHarnessError:
        return None


def refuse_invented(environment: HostedHarnessJob, archive: bytes) -> None:
    """Refuse a builder's save that starts a scenario from a record it was asked to, with facts about that record
    that are not the record as seeded. Checked on every save, for the scenarios it changes, so an invented record never
    reaches the scenario and a mismatch made outside the builder never holds up another scenario's save."""
    latest: dict[str, HostedHarnessConversationMessage] = {}
    for message in _Message.no_workspace_objects.filter(
        conversation__job=environment,
        role=_Message.Role.USER,
        payload___origin=REWORK_ORIGIN,
    ).order_by("sequence"):
        latest[str(message.payload.get("_scenario_key") or "")] = message
    for scenario_key, message in latest.items():
        files = archive_scenario_files(archive, scenario_key) if scenario_key else None
        if files is None or files.document == _current_document(
            environment, scenario_key
        ):
            continue
        why = _not_as_seeded(
            files, scenario_records(files)[0], message.payload["_record"]
        )
        if why:
            raise HostedHarnessError(
                "starting_record_not_as_seeded",
                f"scenario {scenario_key} does not start from the record as seeded: {why}",
                status_code=409,
            )


def _refuse_unready(job: HostedHarnessJob) -> None:
    if job.state != HostedHarnessJob.State.COMPLETED:
        raise HostedHarnessError(
            "environment_busy",
            "The environment isn't ready; its scenarios can't be reworked now.",
            status_code=409,
        )
    if HostedHarnessConversation.no_workspace_objects.filter(
        job=job, state=HostedHarnessConversation.State.RETIRED
    ).exists():
        raise HostedHarnessError(
            "conversation_workspace_not_ready",
            "This environment's saved files are no longer available; rebuild it to chat.",
            status_code=409,
        )
    drafts.require_no_rebuild(job, piece="suite")


def _chosen(
    files: ScenarioFiles, *, path: list[str], table: str, key: str
) -> tuple[StartingRecord, Row]:
    """The record ``path`` names, checked against the choice: a seeded row of its table, other than its own."""
    records, unavailable = scenario_records(files)
    if unavailable is not None:
        raise HostedHarnessError(
            unavailable["code"], unavailable["reason"], status_code=409
        )
    label = ".".join(path)
    record = next((one for one in records if one["path"] == path), None)
    if record is None:
        raise rework_invalid(
            [problem("path", f"{label} doesn't name a seeded record of this scenario.")]
        )
    if record["table"] != table:
        raise rework_invalid(
            [
                problem(
                    "table",
                    f"{label} names a record of {record['table']}, not {table}.",
                )
            ]
        )
    if record["key"] == key and record["status"] == "seeded":
        raise rework_invalid(
            [problem("key", "The scenario already starts from this record.")]
        )
    row, _ = seeded_row(files, table=table, key_field=record["key_field"], key=key)
    if row is None:
        raise rework_invalid(
            [
                problem(
                    "key",
                    f"{table} has no seeded row with {record['key_field']} {key}.",
                )
            ]
        )
    return record, row


def file_rework(
    job: HostedHarnessJob,
    scenario_key: str,
    *,
    path: list[str],
    table: str,
    key: str,
    author_id: str,
    base_url: str,
) -> ReworkRequest:
    """Ask the environment's builder to rework ``scenario_key`` so it starts from the ``table`` row ``key``. Nothing is
    sent for a choice the world can't hold, or while an earlier request for the scenario is still running."""
    from simulate.services.harness_usage import require_harness_authoring
    from simulate.services.hosted_harness_conversation import enqueue_message
    from simulate.tasks.hosted_harness_conversation import (
        schedule_conversation_runtime,
    )

    _refuse_unready(job)
    files = scenario_files(job, scenario_key)
    record, row = _chosen(files, path=path, table=table, key=key)
    own_key = str(
        files.document.get("scenario_key") or files.document.get("name") or ""
    )
    earlier = _latest_message(job, own_key)
    # Pending as the panel reads it: running, or ended with its save still to land.
    if (
        earlier is not None
        and _outcome(earlier, files, scenario_records(files)[0])[0] == "pending"
    ):
        raise HostedHarnessError(
            "rework_pending",
            "The builder is still reworking this scenario; wait for it to finish.",
            status_code=409,
        )
    # Admitted like any builder turn after the build: it is billed as authoring.
    require_harness_authoring(str(job.organization_id))
    chosen = {
        "path": path,
        "table": table,
        "key_field": record["key_field"],
        "key": key,
        "fields": row,
    }
    conversation, message, _created = enqueue_message(
        job,
        content=(
            f"Rework scenario {own_key} so it starts from the {table} record {record['key_field']}={key} instead of "
            f"{record['key']}: update the caller's facts ({record['label']}), the setup and the checks together, "
            "keep the scenario's key, then prove it. The record is seeded as "
            f"{json.dumps(row, ensure_ascii=False, sort_keys=True)}. Every fact about it is one of these fields with "
            "this exact value, kept beside its key in the fixture; never change the record in the setup. If the "
            "scenario cannot hold with this record as seeded, change nothing and say which value rules it out."
        ),
        client_request_id=f"starting-record-{uuid.uuid4().hex}",
        server_payload={
            "_origin": REWORK_ORIGIN,
            "_author_id": author_id,
            "_scenario_key": scenario_token(own_key),
            "_record": chosen,
        },
    )
    conversation_id = str(conversation.id)

    def schedule() -> None:
        try:
            schedule_conversation_runtime(conversation_id, base_url)
        except Exception:  # noqa: BLE001 - the queued request waits for recovery
            logger.exception(
                "starting_record_rework_not_scheduled", conversation_id=conversation_id
            )

    transaction.on_commit(schedule)
    logger.info(
        "starting_record_rework_filed",
        job_id=str(job.id),
        scenario_key=own_key,
        table=table,
        message_id=str(message.id),
    )
    return _request(message, files, [])
