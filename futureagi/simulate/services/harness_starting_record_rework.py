"""Starting a scenario from another seeded record: a request to the environment's builder to rework the scenario
around it (caller's facts, setup and checks together, then a proof), and the state its turn left. The builder's own
turn writes the scenario, into the unsaved changes; nothing here does."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from django.db import transaction

from simulate.models import (
    HostedHarnessConversation,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
)
from simulate.services.harness_actor_validation import Problem, problem
from simulate.services.harness_starting_records import (
    ReworkRequest,
    ScenarioFiles,
    StartingRecord,
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


def _outcome(
    message: HostedHarnessConversationMessage,
    files: ScenarioFiles,
    records: list[StartingRecord],
) -> tuple[Literal["pending", "proven", "refused"], str | None]:
    """Pending while the turn runs. Once it ends: proven when the scenario now starts from the record and a proof made
    after the request holds; refused otherwise, saying why."""
    if message.state in _RUNNING:
        return "pending", None
    if message.state == _Message.State.FAILED:
        return "refused", "The builder couldn't finish this request."
    wanted = message.payload["_record"]
    starts = any(
        one["table"] == wanted["table"]
        and one["key"] == wanted["key"]
        and one["status"] == "seeded"
        for one in records
    )
    proof = _proof(files, message.payload["_scenario_key"])
    proved = _proved_at(proof) if proof is not None else None
    fresh = proved is not None and proved >= message.created_at - PROOF_CLOCK_SKEW
    if not starts:
        return (
            "refused",
            f"The builder finished without starting this scenario from {wanted['table']} "
            f"{wanted['key_field']} {wanted['key']}.",
        )
    if not fresh:
        return "refused", "The builder changed the scenario but didn't prove it."
    if proof is not None and proof.get("holds") is True:
        return "proven", None
    why = str(proof.get("why") or "") if proof is not None else ""
    return "refused", why or "The reworked scenario didn't pass its proof."


def _request(
    message: HostedHarnessConversationMessage,
    files: ScenarioFiles,
    records: list[StartingRecord],
) -> ReworkRequest:
    record = message.payload["_record"]
    state, reason = _outcome(message, files, records)
    return ReworkRequest(
        id=str(message.id),
        state=state,
        table=str(record["table"]),
        key_field=str(record["key_field"]),
        key=str(record["key"]),
        reason=reason,
        requested_at=message.created_at.isoformat(),
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
) -> StartingRecord:
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
    return record


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
    record = _chosen(files, path=path, table=table, key=key)
    own_key = str(
        files.document.get("scenario_key") or files.document.get("name") or ""
    )
    earlier = _latest_message(job, own_key)
    if earlier is not None and earlier.state in _RUNNING:
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
    }
    conversation, message, _created = enqueue_message(
        job,
        content=(
            f"Rework scenario {own_key} so it starts from the {table} record {record['key_field']}={key} instead of "
            f"{record['key']}: update the caller's facts ({record['label']}), the setup and the checks together, "
            "keep the scenario's key, then prove it."
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
