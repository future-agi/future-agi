from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import HarnessAgentVersionRuntime, HostedHarnessJob
from simulate.services.harness_design_contract import (
    apply_edits_to_contract,
    build_proposal,
)
from simulate.services.hosted_harness import BUILD_PIN_KEY, HostedHarnessError

if TYPE_CHECKING:
    from accounts.models.user import User

logger = structlog.get_logger(__name__)

REVIEW_KEY = "design_review"
AWAITING_APPROVAL = "awaiting_approval"
APPROVED = "approved"
DISCARDED = "discarded"
STATUSES = (AWAITING_APPROVAL, APPROVED, DISCARDED)
AWAITING_STAGE = "awaiting_approval"
DISCARD_REASON = "design_discarded"

OPS = ("remove", "edit")
COLLECTIONS = {
    "table": "tables",
    "tool": "tools",
    "rule": "rules",
    "use_case": "use_cases",
}
READING = "reading"
READING_STAGE = "understanding_agent"
READING_FAILED = "reading_failed"
READING_TIMED_OUT = "reading_timed_out"
SourceKind = HarnessAgentVersionRuntime.SourceKind
_CODE_SOURCES = (SourceKind.GITHUB, SourceKind.ARCHIVE)
_CONFIGURED_SOURCES = (SourceKind.PHONE, SourceKind.LIVEKIT)
_PROVIDER_CONNECTORS = frozenset({"vapi", "retell", "retell_chat"})
_UNREVIEWED_MODES = frozenset({"provider_import"})


def _switched_on() -> bool:
    return bool(
        getattr(settings, "HARNESS_DESIGN_REVIEW_ENABLED", False)
        and getattr(settings, "HARNESS_SEED_CONTRACT_ENABLED", False)
        and getattr(settings, "ALK_HOSTED_BUILDER_SESSION", True)
    )


def reviewable(source_kind: str, connector: str, mode: str = "") -> bool:
    if mode in _UNREVIEWED_MODES:
        return False
    if source_kind in _CODE_SOURCES:
        return True
    if not getattr(settings, "HARNESS_HOSTED_DESIGN_REVIEW_ENABLED", False):
        return False
    return source_kind in _CONFIGURED_SOURCES or (
        source_kind == SourceKind.PROVIDER and connector in _PROVIDER_CONNECTORS
    )


def _agent_of(runtime: HarnessAgentVersionRuntime) -> tuple[str, str]:
    payload = getattr(runtime, "agent_payload", None) or {}
    source = getattr(runtime, "source", None) or {}
    return (
        str(payload.get("connector") or source.get("provider") or ""),
        str(payload.get("mode") or ""),
    )


def review_applies(runtime: HarnessAgentVersionRuntime | None) -> bool:
    """A design is reviewed only where the build can start from it, so an approved
    change always reaches the sandbox."""
    return bool(
        _switched_on()
        and runtime is not None
        and reviewable(runtime.source_kind, *_agent_of(runtime))
    )


def review_wanted(
    payload: dict[str, Any], runtime: HarnessAgentVersionRuntime | None
) -> bool:
    if runtime is not None:
        return review_applies(runtime)
    if not _switched_on():
        return False
    from simulate.services.harness_versioning.backfill import legacy_source

    agent = payload.get("agent") or {}
    kind, _facts = legacy_source(payload)
    return reviewable(
        str(kind), str(agent.get("connector") or ""), str(agent.get("mode") or "")
    )


def review_contract_for(
    runtime: HarnessAgentVersionRuntime | None,
) -> dict[str, Any] | None:
    if runtime is None or not review_applies(runtime):
        return None
    from simulate.services.agent_contracts import stored_read_contract

    return stored_read_contract(runtime.agent_version_id)


def stored_review(job: HostedHarnessJob) -> dict[str, Any] | None:
    review = ((job.payload or {}).get("metadata") or {}).get(REVIEW_KEY)
    return review if isinstance(review, dict) else None


def awaiting_design_approval(job: HostedHarnessJob) -> bool:
    review = stored_review(job)
    return review is not None and review.get("status") == AWAITING_APPROVAL


def design_review_pending(job: HostedHarnessJob) -> bool:
    review = stored_review(job)
    return review is not None and review.get("status") in (READING, AWAITING_APPROVAL)


def approved_contract_key(job: HostedHarnessJob) -> str:
    """The stored contract an approved, changed design builds from, or ""."""
    review = stored_review(job)
    if review is None or review.get("status") != APPROVED:
        return ""
    return str(review.get("approved_contract_key") or "")


def approved_seed_key(job: HostedHarnessJob) -> str:
    review = stored_review(job)
    if review is None or review.get("status") != APPROVED:
        return ""
    return str(
        review.get("approved_contract_key") or review.get("source_contract_key") or ""
    )


def _write(job: HostedHarnessJob, review: dict[str, Any]) -> None:
    payload = dict(job.payload or {})
    payload["metadata"] = {**(payload.get("metadata") or {}), REVIEW_KEY: review}
    job.payload = payload


def close_open_review(job: HostedHarnessJob) -> bool:
    """Mark a design still waiting for approval as discarded on ``job``, unsaved;
    whether it was open."""
    review = stored_review(job)
    if review is None or review.get("status") not in (AWAITING_APPROVAL, READING):
        return False
    _write(job, {**review, "status": DISCARDED})
    return True


def create_design_review(
    job: HostedHarnessJob,
    runtime: HarnessAgentVersionRuntime,
    contract: dict[str, Any],
) -> None:
    from simulate.services.agent_contracts import version_contract_key

    _write(
        job,
        {
            "status": AWAITING_APPROVAL,
            "agent_version_id": (
                str(runtime.agent_version_id) if runtime.agent_version_id else None
            ),
            "source_contract_key": (
                version_contract_key(runtime.agent_version_id)
                if runtime.agent_version_id
                else ""
            ),
            "proposal": build_proposal(
                contract,
                job.scenario_count,
                source_kind=((job.payload or {}).get("source") or {}).get("kind"),
            ),
            "edits": [],
            "created_at": timezone.now().isoformat(),
            "approved_by": None,
            "approved_at": None,
            "approved_contract_key": "",
        },
    )
    job.current_stage = AWAITING_STAGE
    job.save(update_fields=["payload", "current_stage", "updated_at"])


def begin_design_review(
    job: HostedHarnessJob,
    runtime: HarnessAgentVersionRuntime | None,
    contract: dict[str, Any] | None,
) -> None:
    from simulate.services.agent_contracts import stored_read_contract
    from simulate.services.harness_agent_versions import _side_row

    if contract is None or runtime is None:
        contract = None
        if runtime is None or not getattr(runtime, "agent_version_id", None):
            runtime = _side_row(job.agent_version_id) if job.agent_version_id else None
        if runtime is None or not review_applies(runtime):
            job.state = HostedHarnessJob.State.QUEUED
            job.current_stage = "queued"
            job.save(update_fields=["state", "current_stage", "updated_at"])
            return
    current = _current_agent(job, runtime)
    if current is not runtime:
        runtime, contract = current, None
    _build_as_read(job, runtime)
    if contract is None:
        contract = stored_read_contract(runtime.agent_version_id)
    if contract is not None:
        create_design_review(job, runtime, contract)
        return
    _write(
        job,
        {
            "status": READING,
            "agent_version_id": str(runtime.agent_version_id),
            "created_at": timezone.now().isoformat(),
        },
    )
    job.current_stage = READING_STAGE
    job.save(update_fields=["payload", "current_stage", "updated_at"])
    job_id = str(job.id)
    transaction.on_commit(lambda: _start_reading(job_id))


def _pins_a_profile(runtime: HarnessAgentVersionRuntime) -> bool:
    from simulate.services.agent_reading_sandbox import _has_document

    return bool(
        (runtime.source or {}).get("profile_digest")
        or _has_document(runtime.agent_version_id)
    )


def _current_agent(
    job: HostedHarnessJob, runtime: HarnessAgentVersionRuntime
) -> HarnessAgentVersionRuntime:
    if getattr(runtime, "source_kind", None) != SourceKind.PROVIDER or not (
        _pins_a_profile(runtime)
    ):
        return runtime
    from simulate.services.agent_versions import provider_sync
    from simulate.services.harness_agent_versions import _side_row

    try:
        version = provider_sync(
            runtime.agent_definition,
            runtime.agent_version,
            dict(runtime.source or {}),
            read=False,
        )
    except HostedHarnessError as exc:
        logger.warning(
            "design_review_agent_not_refreshed",
            environment_id=str(job.id),
            agent_version_id=str(runtime.agent_version_id),
            code=exc.code,
            exc_info=True,
        )
        return runtime
    if version.id == runtime.agent_version_id:
        return runtime
    current = _side_row(version.id)
    if current is None:
        return runtime
    _move_build_to(job, current)
    logger.info(
        "design_review_agent_refreshed",
        environment_id=str(job.id),
        pinned_version_id=str(runtime.agent_version_id),
        agent_version_id=str(current.agent_version_id),
    )
    return current


def _move_build_to(job: HostedHarnessJob, runtime: HarnessAgentVersionRuntime) -> None:
    metadata = dict((job.payload or {}).get("metadata") or {})
    metadata[BUILD_PIN_KEY] = str(runtime.agent_version_id)
    if metadata.get("agent_version_id"):
        metadata["agent_version_id"] = str(runtime.agent_version_id)
    job.agent_version = runtime.agent_version
    job.payload = {**(job.payload or {}), "metadata": metadata}
    job.save(update_fields=["agent_version", "payload", "updated_at"])


def _build_as_read(job: HostedHarnessJob, runtime: HarnessAgentVersionRuntime) -> None:
    from simulate.services.agent_reading_sandbox import hosted_reading_agent

    agent = (job.payload or {}).get("agent")
    if not isinstance(agent, dict):
        return
    matched = hosted_reading_agent(runtime, agent)
    if matched != agent:
        job.payload["agent"] = matched
        job.save(update_fields=["payload", "updated_at"])


def _start_reading(job_id: str) -> None:
    from simulate.tasks.harness_agent_versions import schedule_design_reading

    try:
        schedule_design_reading(job_id)
    except Exception:
        logger.warning(
            "design_reading_not_started", environment_id=job_id, exc_info=True
        )
        fail_design_reading(
            job_id,
            code="scheduler_unavailable",
            message="Reading the agent couldn't start. Build again.",
            retryable=True,
        )


def _waiting_on_reading(job_id: str) -> tuple[HostedHarnessJob, dict[str, Any]] | None:
    job = (
        HostedHarnessJob.no_workspace_objects.select_for_update()
        .filter(id=job_id)
        .first()
    )
    review = stored_review(job) if job is not None else None
    if (
        job is None
        or review is None
        or review.get("status") != READING
        or job.state != HostedHarnessJob.State.RECEIVED
    ):
        return None
    return job, review


def reading_version_id(job_id: str) -> str | None:
    with transaction.atomic():
        waiting = _waiting_on_reading(job_id)
    if waiting is None:
        return None
    return str(waiting[1].get("agent_version_id") or "") or None


def _close_reading(
    job: HostedHarnessJob,
    review: dict[str, Any],
    *,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    from simulate.services.hosted_harness import project_terminal_job_onto_run

    now = timezone.now()
    _write(job, {**review, "status": READING_FAILED})
    job.state = HostedHarnessJob.State.FAILED
    job.current_stage = "failed"
    job.terminal_at = now
    job.failure = {
        "domain": "agent",
        "stage": READING_STAGE,
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    job.save(
        update_fields=[
            "payload",
            "state",
            "current_stage",
            "terminal_at",
            "failure",
            "updated_at",
        ]
    )
    project_terminal_job_onto_run(job, now)
    logger.info("design_reading_failed", environment_id=str(job.id), code=code)


def fail_design_reading(
    job_id: str,
    *,
    code: str,
    message: str,
    retryable: bool,
    started_before: datetime | None = None,
) -> bool:
    with transaction.atomic():
        waiting = _waiting_on_reading(job_id)
        if waiting is None or (
            started_before is not None
            and not _reading_started_before(waiting[1], started_before)
        ):
            return False
        _close_reading(*waiting, code=code, message=message, retryable=retryable)
    return True


def _reading_started_before(review: dict[str, Any], cutoff: datetime) -> bool:
    started = parse_datetime(str(review.get("created_at") or ""))
    return started is not None and started < cutoff


def fail_stale_reading(job: HostedHarnessJob) -> bool:
    review = stored_review(job)
    if (
        review is None
        or review.get("status") != READING
        or job.state != HostedHarnessJob.State.RECEIVED
    ):
        return False
    from simulate.tasks.harness_agent_versions import DESIGN_READING_DEADLINE_SECONDS

    cutoff = timezone.now() - timedelta(seconds=DESIGN_READING_DEADLINE_SECONDS)
    if not _reading_started_before(review, cutoff):
        return False
    failed = fail_design_reading(
        str(job.id),
        code=READING_TIMED_OUT,
        message="Reading the agent took too long and stopped. Build again.",
        retryable=True,
        started_before=cutoff,
    )
    if failed:
        logger.warning("design_reading_timed_out", environment_id=str(job.id))
    return failed


def reopen_reading(job: HostedHarnessJob) -> None:
    review = stored_review(job) or {}
    _write(
        job,
        {
            "status": READING,
            "agent_version_id": review.get("agent_version_id"),
            "created_at": timezone.now().isoformat(),
        },
    )
    job.state = HostedHarnessJob.State.RECEIVED
    job.current_stage = READING_STAGE
    job.failure = None
    job.terminal_at = None
    job.save(
        update_fields=[
            "payload",
            "state",
            "current_stage",
            "failure",
            "terminal_at",
            "updated_at",
        ]
    )


def _pin_read_commit(job: HostedHarnessJob, commit_sha: str) -> None:
    source = (job.payload or {}).get("source") or {}
    if not commit_sha or source.get("kind") != "github" or source.get("commit_sha"):
        return
    job.payload = {**job.payload, "source": {**source, "commit_sha": commit_sha}}


def open_review_after_reading(
    job_id: str,
    *,
    code: str = "",
    message: str = "",
    retryable: bool = False,
    commit_sha: str = "",
) -> str:
    from simulate.services.agent_contracts import stored_read_contract
    from simulate.services.harness_agent_versions import _side_row

    with transaction.atomic():
        waiting = _waiting_on_reading(job_id)
        if waiting is None:
            return "closed"
        job, review = waiting
        runtime = _side_row(review.get("agent_version_id"))
        contract = (
            stored_read_contract(runtime.agent_version_id)
            if runtime is not None
            else None
        )
        if runtime is None or contract is None:
            _close_reading(
                job,
                review,
                code=code or "agent_design_missing",
                message=message
                or "Reading the agent left no design to review. Build again.",
                retryable=retryable or not code,
            )
            return "failed"
        _pin_read_commit(job, commit_sha)
        create_design_review(job, runtime, contract)
    logger.info("design_review_opened", environment_id=job_id)
    return "opened"


def _spread(total: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base, extra = divmod(max(total, 0), count)
    return [base + (1 if index < extra else 0) for index in range(count)]


def _effective(proposal: dict[str, Any], edits: list[dict[str, Any]]) -> dict[str, Any]:
    view = copy.deepcopy(proposal)
    for kind in ("tables", "tools", "use_cases"):
        for item in view.get(kind) or []:
            item["removed"] = False
    for rule in view.get("rules") or []:
        rule.update(removed=False, edited=False, original_text=None)
    by_id = {
        (kind, item["id"]): item
        for kind, key in COLLECTIONS.items()
        for item in view.get(key) or []
    }
    for edit in edits:
        item = by_id.get((edit["kind"], edit.get("id")))
        if item is None:
            continue
        if edit["op"] == "remove":
            item["removed"] = True
        elif edit["op"] == "edit" and edit["kind"] == "rule":
            if item.get("original_text") is None:
                item["original_text"] = item["text"]
            item["text"] = edit["text"]
            item["edited"] = True
            item["source"] = "your_change"
    kept = [case for case in view.get("use_cases") or [] if not case["removed"]]
    counts = iter(_spread(view.get("scenario_count") or 0, len(kept)))
    for case in view.get("use_cases") or []:
        case["planned_scenarios"] = 0 if case["removed"] else next(counts)
    return view


def agent_changed(review: dict[str, Any]) -> bool:
    """Whether the agent version's stored contract is no longer the one this design
    was read from; compared by object key, since every new contract gets a new key."""
    if not review.get("agent_version_id") or not review.get("source_contract_key"):
        return False
    from simulate.services.agent_contracts import version_contract_key

    return version_contract_key(review["agent_version_id"]) != review.get(
        "source_contract_key"
    )


def _legacy_edit_id(edit: dict[str, Any]) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_OID, json.dumps(edit, sort_keys=True, default=str)
    ).hex


def _with_ids(edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        edit if edit.get("edit_id") else {**edit, "edit_id": _legacy_edit_id(edit)}
        for edit in edits
    ]


def design_document(job: HostedHarnessJob) -> dict[str, Any]:
    fail_stale_reading(job)
    review = review_for(job)
    edits = review.get("edits") or []
    view = _effective(review.get("proposal") or {}, edits)
    removed_tools = {tool["id"] for tool in view.get("tools") or [] if tool["removed"]}
    return {
        "environment_id": str(job.id),
        "status": review.get("status") or AWAITING_APPROVAL,
        "agent_version_id": review.get("agent_version_id"),
        "agent_changed": agent_changed(review),
        "scenario_count": view.get("scenario_count") or 0,
        "tables": view.get("tables") or [],
        "tools": view.get("tools") or [],
        "rules": view.get("rules") or [],
        "use_cases": view.get("use_cases") or [],
        "brief": {
            "description": "",
            "channel": "chat",
            **(view.get("brief") or {}),
            "agent": _agent_label(review.get("agent_version_id")),
        },
        "actors": view.get("actors") or [],
        "unsupported": [
            {**one, "removed": one["tool"] in removed_tools}
            for one in view.get("unsupported") or []
        ],
        "edits": _with_ids(edits),
        "created_at": review.get("created_at"),
        "approved_at": review.get("approved_at"),
    }


def _agent_label(agent_version_id: str | None) -> dict[str, str] | None:
    if not agent_version_id:
        return None
    from simulate.models import AgentVersion

    version = (
        AgentVersion.no_workspace_objects.select_related("agent_definition")
        .filter(id=agent_version_id)
        .first()
    )
    if version is None:
        return None
    return {
        "name": version.agent_definition.agent_name,
        "version": version.version_name or f"v{version.version_number}",
    }


def review_for(job: HostedHarnessJob) -> dict[str, Any]:
    review = stored_review(job)
    if review is None or (review.get("status") or AWAITING_APPROVAL) not in STATUSES:
        raise HostedHarnessError(
            "design_review_not_found",
            "This environment has no design waiting for review",
            status_code=404,
        )
    return review


def _locked(job: HostedHarnessJob) -> tuple[HostedHarnessJob, dict[str, Any]]:
    locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job.id)
    return locked, review_for(locked)


def _require_open(review: dict[str, Any]) -> None:
    status = review.get("status") or AWAITING_APPROVAL
    if status != AWAITING_APPROVAL:
        raise HostedHarnessError(
            "design_review_closed",
            f"This design was already {status.replace('_', ' ')}",
            status_code=409,
        )


def _validate(view: dict[str, Any], edit: dict[str, Any]) -> None:
    op, kind = edit["op"], edit["kind"]
    if op == "edit" and kind != "rule":
        raise HostedHarnessError(
            "design_edit_invalid",
            "Only rules can be reworded; other changes go through the builder",
            status_code=422,
        )
    item = next(
        (
            one
            for one in view.get(COLLECTIONS[kind]) or []
            if one["id"] == edit.get("id")
        ),
        None,
    )
    if item is None:
        raise HostedHarnessError(
            "design_item_not_found",
            f"No {kind.replace('_', ' ')} {edit.get('id')!r} in this design",
            status_code=404,
        )
    if item.get("removed"):
        raise HostedHarnessError(
            "design_edit_invalid",
            f"{edit.get('id')!r} is already removed",
            status_code=422,
        )
    if op == "remove" and kind == "use_case":
        kept = [case for case in view.get("use_cases") or [] if not case["removed"]]
        if len(kept) <= 1:
            raise HostedHarnessError(
                "design_keep_one_use_case",
                "Keep at least one use case, or there is nothing to test",
                status_code=422,
            )


def _same_rewording(one: dict[str, Any], other: dict[str, Any]) -> bool:
    return (
        one["op"] == other["op"] == "edit"
        and one["kind"] == other["kind"]
        and one.get("id") == other.get("id")
    )


def add_edit(
    job: HostedHarnessJob,
    *,
    op: str,
    kind: str,
    item_id: str,
    text: str | None,
    user: User,
) -> HostedHarnessJob:
    """Record one change to the design; a rule reworded again replaces its earlier
    wording, so the list never holds more changes than the design has items."""
    with transaction.atomic():
        locked, review = _locked(job)
        _require_open(review)
        record: dict[str, Any] = {"op": op, "kind": kind, "id": str(item_id)}
        if op == "edit":
            record["text"] = str(text or "").strip()
        edits = [
            one
            for one in _with_ids(review.get("edits") or [])
            if not _same_rewording(one, record)
        ]
        _validate(_effective(review.get("proposal") or {}, edits), record)
        record.update(
            edit_id=uuid.uuid4().hex,
            by=str(user.pk or ""),
            at=timezone.now().isoformat(),
        )
        _write(locked, {**review, "edits": [*edits, record]})
        locked.save(update_fields=["payload", "updated_at"])
    return locked


def undo_edit(job: HostedHarnessJob, edit_id: str, *, user: User) -> HostedHarnessJob:
    with transaction.atomic():
        locked, review = _locked(job)
        _require_open(review)
        edits = _with_ids(review.get("edits") or [])
        index = next(
            (at for at, edit in enumerate(edits) if edit["edit_id"] == edit_id), None
        )
        if index is None:
            raise HostedHarnessError(
                "design_edit_gone",
                "This change was already undone or replaced",
                status_code=409,
            )
        undone = edits.pop(index)
        _write(locked, {**review, "edits": edits})
        locked.save(update_fields=["payload", "updated_at"])
    logger.info(
        "design_review_edit_undone",
        environment_id=str(job.id),
        user_id=str(user.pk or ""),
        op=undone["op"],
        kind=undone["kind"],
    )
    return locked


def _mentions(text: str, name: str) -> bool:
    return (
        bool(name)
        and re.search(rf"\b{re.escape(name)}\b", text or "", re.I) is not None
    )


def _feeds_from(origin: str, name: str) -> bool:
    return origin == name or origin.startswith(f"{name}.")


def _tools_needing(tools: list[dict[str, Any]], item_id: str) -> dict[str, str]:
    affected: dict[str, str] = {}
    frontier = [item_id]
    while frontier:
        name = frontier.pop()
        for tool in tools:
            if tool["name"] == item_id or tool["name"] in affected:
                continue
            if name in tool["requires"]:
                affected[tool["name"]] = f"needs {name} to run first"
            elif any(
                _feeds_from(origin, name) for origin in tool["inputs_from"].values()
            ):
                affected[tool["name"]] = f"takes its input from {name}"
            else:
                continue
            frontier.append(tool["name"])
    return affected


def _tools_using(tools: list[dict[str, Any]], table: str) -> dict[str, str]:
    affected: dict[str, str] = {}
    for tool in tools:
        text = " ".join((tool["description"], tool["effects"], tool["refuses"]))
        if table in (tool.get("tables_changed") or []):
            affected[tool["name"]] = f"changes {table}"
        elif table in (tool.get("tables_read") or []):
            affected[tool["name"]] = f"reads {table}"
        elif _mentions(text, table):
            affected[tool["name"]] = f"reads or changes {table}"
    return affected


def impact(job: HostedHarnessJob, kind: str, item_id: str) -> dict[str, Any]:
    review = review_for(job)
    view = _effective(review.get("proposal") or {}, review.get("edits") or [])
    tools = [tool for tool in view.get("tools") or [] if not tool["removed"]]
    rules = [rule for rule in view.get("rules") or [] if not rule["removed"]]
    if kind == "tool":
        affected = _tools_needing(tools, item_id)
    elif kind == "table":
        affected = _tools_using(tools, item_id)
    else:
        affected = {}
    named = [item_id, *affected] if kind in ("tool", "table") else []
    return {
        "kind": kind,
        "id": item_id,
        "tools": [{"name": name, "reason": why} for name, why in affected.items()],
        "rules": [
            {"id": rule["id"], "text": rule["text"]}
            for rule in rules
            if any(
                _mentions(rule["text"], alias)
                for name in named
                for alias in (name, name.replace("_", " "))
            )
        ],
    }


def _approver(user: User) -> dict[str, str] | None:
    if not user.pk:
        return None
    name = getattr(user, "name", "") or getattr(user, "email", "")
    return {"id": str(user.pk), "name": str(name or "")}


def _agent_changed_refusal() -> HostedHarnessError:
    return HostedHarnessError(
        "design_agent_changed",
        "The agent changed after this design was read; discard it and build again",
        status_code=409,
    )


def _store_approved_contract(job: HostedHarnessJob, review: dict[str, Any]) -> str:
    from simulate.services.agent_contracts import (
        SEED_MAX_BYTES,
        contract_bytes,
        read_contract,
        store_environment_contract,
    )

    if agent_changed(review):
        raise _agent_changed_refusal()
    source = read_contract(str(review.get("source_contract_key") or ""))
    if source is None:
        raise HostedHarnessError(
            "design_contract_unreadable",
            "The agent's design could not be read; try approving again",
            status_code=503,
        )
    edited = apply_edits_to_contract(source, review.get("edits") or [])
    if len(contract_bytes(edited)) > SEED_MAX_BYTES:
        raise HostedHarnessError(
            "design_too_large",
            "This design is too large to build with your changes",
            status_code=422,
        )
    key = store_environment_contract(
        organization_id=job.organization_id, environment_id=job.id, contract=edited
    )
    if key is None:
        raise HostedHarnessError(
            "design_contract_not_stored",
            "Your changes could not be saved; try approving again",
            status_code=503,
        )
    return key


def approve(job: HostedHarnessJob, *, user: User, base_url: str) -> HostedHarnessJob:
    """Start the build from the design as changed. A changed design is stored on the
    environment first, and an unchanged one builds from the contract it was read
    from, so the build seeds from exactly what was approved. Refused
    with 409 when the design is closed, the job is not waiting, or the agent changed,
    and with 503 when the build could not be started."""
    from simulate.services.agent_contracts import remove_contract
    from simulate.temporal.client import start_hosted_harness_gateway_workflow

    first = review_for(job)
    _require_open(first)
    edits = first.get("edits") or []
    contract_key = _store_approved_contract(job, first) if edits else ""
    try:
        with transaction.atomic():
            locked, review = _locked(job)
            _require_open(review)
            if locked.state != HostedHarnessJob.State.RECEIVED:
                raise HostedHarnessError(
                    "design_review_not_waiting",
                    "This environment is not waiting for approval",
                    status_code=409,
                )
            if (review.get("edits") or []) != edits:
                raise HostedHarnessError(
                    "design_review_changed",
                    "The design changed while it was being approved; approve again",
                    status_code=409,
                )
            if agent_changed(review):
                raise _agent_changed_refusal()
            _write(
                locked,
                {
                    **review,
                    "status": APPROVED,
                    "approved_by": _approver(user),
                    "approved_at": timezone.now().isoformat(),
                    "approved_contract_key": contract_key,
                },
            )
            locked.state = HostedHarnessJob.State.QUEUED
            locked.current_stage = "queued"
            locked.save(
                update_fields=["payload", "state", "current_stage", "updated_at"]
            )
    except HostedHarnessError:
        if contract_key:
            remove_contract(contract_key)
        raise
    retry = locked.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(locked.id),
            base_url,
            retry["max_infrastructure_attempts"],
            retry["initial_backoff_seconds"],
            retry["max_backoff_seconds"],
        )
    except Exception as exc:
        with transaction.atomic():
            reverted = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=locked.id
            )
            current = stored_review(reverted) or {}
            still_queued = (
                reverted.state == HostedHarnessJob.State.QUEUED
                and current.get("status") == APPROVED
            )
            if still_queued:
                _write(
                    reverted,
                    {
                        **current,
                        "status": AWAITING_APPROVAL,
                        "approved_by": None,
                        "approved_at": None,
                        "approved_contract_key": "",
                    },
                )
                reverted.state = HostedHarnessJob.State.RECEIVED
                reverted.current_stage = AWAITING_STAGE
                reverted.save(
                    update_fields=["payload", "state", "current_stage", "updated_at"]
                )
        if contract_key and still_queued:
            remove_contract(contract_key)
        logger.warning(
            "design_review_build_not_started",
            environment_id=str(locked.id),
            exc_info=True,
        )
        raise HostedHarnessError(
            "scheduler_unavailable",
            "The build could not start; the design is still waiting for approval",
            status_code=503,
        ) from exc
    logger.info(
        "design_review_approved",
        environment_id=str(locked.id),
        user_id=str(user.pk or ""),
        edits=len(edits),
    )
    return locked


def discard(job: HostedHarnessJob, *, user: User) -> HostedHarnessJob:
    """Drop the design and the environment with it, through the one delete path."""
    from simulate.services.hosted_harness import delete_environment

    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        review = stored_review(locked)
        if review is None or review.get("status") != READING:
            _require_open(review_for(locked))
        delete_environment(locked, reason=DISCARD_REASON)
    logger.info(
        "design_review_discarded",
        environment_id=str(job.id),
        user_id=str(user.pk or ""),
    )
    return HostedHarnessJob.all_objects.get(id=job.id)
