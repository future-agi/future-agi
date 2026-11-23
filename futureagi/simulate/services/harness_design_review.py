from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import HarnessAgentVersionRuntime, HostedHarnessJob
from simulate.services.harness_design_contract import (
    apply_edits_to_contract,
    build_proposal,
)
from simulate.services.hosted_harness import HostedHarnessError

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
_REVIEWABLE_SOURCES = (
    HarnessAgentVersionRuntime.SourceKind.GITHUB,
    HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
)


def review_applies(runtime: HarnessAgentVersionRuntime | None) -> bool:
    """A design is reviewed only where the build can start from it, so an approved
    change always reaches the sandbox."""
    return bool(
        getattr(settings, "HARNESS_DESIGN_REVIEW_ENABLED", False)
        and getattr(settings, "HARNESS_SEED_CONTRACT_ENABLED", False)
        and getattr(settings, "ALK_HOSTED_BUILDER_SESSION", True)
        and runtime is not None
        and runtime.source_kind in _REVIEWABLE_SOURCES
    )


def review_contract_for(
    runtime: HarnessAgentVersionRuntime | None,
) -> dict[str, Any] | None:
    if runtime is None or not review_applies(runtime):
        return None
    from simulate.services.agent_contracts import stored_version_contract

    return stored_version_contract(runtime.agent_version_id)


def stored_review(job: HostedHarnessJob) -> dict[str, Any] | None:
    review = ((job.payload or {}).get("metadata") or {}).get(REVIEW_KEY)
    return review if isinstance(review, dict) else None


def awaiting_design_approval(job: HostedHarnessJob) -> bool:
    review = stored_review(job)
    return review is not None and review.get("status") == AWAITING_APPROVAL


def approved_contract_key(job: HostedHarnessJob) -> str:
    """The stored contract an approved, changed design builds from, or ""."""
    review = stored_review(job)
    if review is None or review.get("status") != APPROVED:
        return ""
    return str(review.get("approved_contract_key") or "")


def _write(job: HostedHarnessJob, review: dict[str, Any]) -> None:
    payload = dict(job.payload or {})
    payload["metadata"] = {**(payload.get("metadata") or {}), REVIEW_KEY: review}
    job.payload = payload


def close_open_review(job: HostedHarnessJob) -> bool:
    """Mark a design still waiting for approval as discarded on ``job``, unsaved;
    whether it was open."""
    review = stored_review(job)
    if review is None or review.get("status") != AWAITING_APPROVAL:
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
            "proposal": build_proposal(contract, job.scenario_count),
            "edits": [],
            "created_at": timezone.now().isoformat(),
            "approved_by": None,
            "approved_at": None,
            "approved_contract_key": "",
        },
    )
    job.current_stage = AWAITING_STAGE
    job.save(update_fields=["payload", "current_stage", "updated_at"])


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


def design_document(job: HostedHarnessJob) -> dict[str, Any]:
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
        "edits": [{**edit, "index": index} for index, edit in enumerate(edits)],
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
    if review is None:
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
            one for one in review.get("edits") or [] if not _same_rewording(one, record)
        ]
        _validate(_effective(review.get("proposal") or {}, edits), record)
        record.update(by=str(user.pk or ""), at=timezone.now().isoformat())
        _write(locked, {**review, "edits": [*edits, record]})
        locked.save(update_fields=["payload", "updated_at"])
    return locked


def undo_edit(job: HostedHarnessJob, index: int, *, user: User) -> HostedHarnessJob:
    with transaction.atomic():
        locked, review = _locked(job)
        _require_open(review)
        edits = list(review.get("edits") or [])
        if index < 0 or index >= len(edits):
            raise HostedHarnessError(
                "design_edit_not_found", "No such change to undo", status_code=404
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
            if any(_mentions(rule["text"], name) for name in named)
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
    environment first, so the build seeds from exactly what was approved. Refused
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
            reverted, current = _locked(locked)
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
        if contract_key:
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
        locked, review = _locked(job)
        _require_open(review)
        delete_environment(locked, reason=DISCARD_REASON)
    logger.info(
        "design_review_discarded",
        environment_id=str(job.id),
        user_id=str(user.pk or ""),
    )
    return HostedHarnessJob.all_objects.get(id=job.id)
