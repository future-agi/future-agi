from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import HarnessAgentVersionRuntime, HostedHarnessJob
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

REVIEW_KEY = "design_review"
AWAITING_APPROVAL = "awaiting_approval"
APPROVED = "approved"
DISCARDED = "discarded"
STATUSES = (AWAITING_APPROVAL, APPROVED, DISCARDED)
AWAITING_STAGE = "awaiting_approval"
DISCARD_REASON = "design_discarded"

KINDS = ("table", "tool", "rule", "use_case")
OPS = ("remove", "edit")
_REVIEWABLE_SOURCES = (
    HarnessAgentVersionRuntime.SourceKind.GITHUB,
    HarnessAgentVersionRuntime.SourceKind.ARCHIVE,
)


def contract_hash(contract: dict[str, Any]) -> str:
    body = json.dumps(contract, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(body.encode()).hexdigest()}"


def review_applies(runtime: HarnessAgentVersionRuntime | None) -> bool:
    return bool(
        getattr(settings, "HARNESS_DESIGN_REVIEW_ENABLED", False)
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


def _columns(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return sorted(str(name) for name in properties)
    items = schema.get("items")
    if isinstance(items, dict) and isinstance(items.get("properties"), dict):
        return sorted(str(name) for name in items["properties"])
    return []


def _tool(spec: dict[str, Any]) -> dict[str, Any]:
    arg_types = spec.get("arg_types") or {}
    return {
        "id": str(spec.get("name") or ""),
        "name": str(spec.get("name") or ""),
        "description": str(spec.get("description") or ""),
        "args": [
            {"name": str(name), "type": str(arg_types.get(name) or "")}
            for name in spec.get("args") or []
        ],
        "requires": [str(name) for name in spec.get("requires") or []],
        "inputs_from": {
            str(arg): str(origin)
            for arg, origin in (spec.get("inputs_from") or {}).items()
        },
        "effects": str(spec.get("effects") or ""),
        "refuses": str(spec.get("refuses") or ""),
        "source": "agent_code",
    }


def build_proposal(contract: dict[str, Any], scenario_count: int) -> dict[str, Any]:
    schema = contract.get("data_schema") or {}
    return {
        "scenario_count": max(int(scenario_count or 0), 0),
        "tables": [
            {
                "id": str(name),
                "name": str(name),
                "columns": _columns(body),
                "source": "agent_reading",
            }
            for name, body in (schema.items() if isinstance(schema, dict) else [])
        ],
        "tools": [
            _tool(spec)
            for spec in contract.get("tools") or []
            if isinstance(spec, dict) and spec.get("name")
        ],
        "rules": [
            {"id": f"rule-{index}", "text": str(text), "source": "agent_prompt"}
            for index, text in enumerate(contract.get("hard_constraints") or [], 1)
            if str(text).strip()
        ],
        "use_cases": [
            {"id": f"use-case-{index}", "text": str(text), "source": "agent_reading"}
            for index, text in enumerate(contract.get("real_use_cases") or [], 1)
            if str(text).strip()
        ],
    }


def stored_review(job: HostedHarnessJob) -> dict[str, Any] | None:
    review = ((job.payload or {}).get("metadata") or {}).get(REVIEW_KEY)
    return review if isinstance(review, dict) else None


def awaiting_design_approval(job: HostedHarnessJob) -> bool:
    review = stored_review(job)
    return review is not None and review.get("status") == AWAITING_APPROVAL


def _write(job: HostedHarnessJob, review: dict[str, Any]) -> None:
    payload = dict(job.payload or {})
    payload["metadata"] = {**(payload.get("metadata") or {}), REVIEW_KEY: review}
    job.payload = payload


def create_design_review(
    job: HostedHarnessJob,
    runtime: HarnessAgentVersionRuntime,
    contract: dict[str, Any],
) -> None:
    _write(
        job,
        {
            "status": AWAITING_APPROVAL,
            "agent_version_id": (
                str(runtime.agent_version_id) if runtime.agent_version_id else None
            ),
            "source_contract_hash": contract_hash(contract),
            "proposal": build_proposal(contract, job.scenario_count),
            "edits": [],
            "approved_by": None,
            "approved_at": None,
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
        for kind, key in (
            ("table", "tables"),
            ("tool", "tools"),
            ("rule", "rules"),
            ("use_case", "use_cases"),
        )
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
    if not review.get("agent_version_id"):
        return False
    from simulate.services.agent_contracts import stored_version_contract

    current = stored_version_contract(review["agent_version_id"])
    return current is not None and contract_hash(current) != review.get(
        "source_contract_hash"
    )


def design_document(job: HostedHarnessJob) -> dict[str, Any]:
    review = review_for(job)
    edits = review.get("edits") or []
    view = _effective(review.get("proposal") or {}, edits)
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
        "edits": [{**edit, "index": index} for index, edit in enumerate(edits)],
        "approved_at": review.get("approved_at"),
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
    if op == "edit" and not str(edit.get("text") or "").strip():
        raise HostedHarnessError(
            "design_edit_invalid", "The rule needs some text", status_code=422
        )
    key = {
        "table": "tables",
        "tool": "tools",
        "rule": "rules",
        "use_case": "use_cases",
    }[kind]
    item = next(
        (one for one in view.get(key) or [] if one["id"] == edit.get("id")), None
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


def add_edit(
    job: HostedHarnessJob, edit: dict[str, Any], *, user: Any
) -> HostedHarnessJob:
    with transaction.atomic():
        locked, review = _locked(job)
        _require_open(review)
        record = {
            "op": edit["op"],
            "kind": edit["kind"],
            "id": str(edit.get("id") or ""),
        }
        if edit["op"] == "edit":
            record["text"] = str(edit.get("text") or "").strip()
        edits = list(review.get("edits") or [])
        _validate(_effective(review.get("proposal") or {}, edits), record)
        record.update(
            by=str(getattr(user, "id", "") or ""), at=timezone.now().isoformat()
        )
        _write(locked, {**review, "edits": [*edits, record]})
        locked.save(update_fields=["payload", "updated_at"])
    return locked


def undo_edit(job: HostedHarnessJob, index: int, *, user: Any) -> HostedHarnessJob:
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
        user_id=str(getattr(user, "id", "") or ""),
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


def impact(job: HostedHarnessJob, kind: str, item_id: str) -> dict[str, Any]:
    review = review_for(job)
    view = _effective(review.get("proposal") or {}, review.get("edits") or [])
    tools = [tool for tool in view.get("tools") or [] if not tool["removed"]]
    rules = [rule for rule in view.get("rules") or [] if not rule["removed"]]
    affected: dict[str, str] = {}
    if kind == "tool":
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
    elif kind == "table":
        for tool in tools:
            text = " ".join((tool["description"], tool["effects"], tool["refuses"]))
            if _mentions(text, item_id):
                affected[tool["name"]] = f"reads or changes {item_id}"
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


def _approver(user: Any) -> dict[str, str] | None:
    if not getattr(user, "pk", None):
        return None
    name = getattr(user, "name", "") or getattr(user, "email", "")
    return {"id": str(user.pk), "name": str(name or "")}


def approve(job: HostedHarnessJob, *, user: Any, base_url: str) -> HostedHarnessJob:
    from simulate.temporal.client import start_hosted_harness_gateway_workflow

    with transaction.atomic():
        locked, review = _locked(job)
        _require_open(review)
        if locked.state != HostedHarnessJob.State.RECEIVED:
            raise HostedHarnessError(
                "design_review_not_waiting",
                "This environment is not waiting for approval",
                status_code=409,
            )
        _write(
            locked,
            {
                **review,
                "status": APPROVED,
                "approved_by": _approver(user),
                "approved_at": timezone.now().isoformat(),
            },
        )
        locked.state = HostedHarnessJob.State.QUEUED
        locked.current_stage = "queued"
        locked.save(update_fields=["payload", "state", "current_stage", "updated_at"])
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
                },
            )
            reverted.state = HostedHarnessJob.State.RECEIVED
            reverted.current_stage = AWAITING_STAGE
            reverted.save(
                update_fields=["payload", "state", "current_stage", "updated_at"]
            )
        logger.warning(
            "design_review_build_not_started",
            environment_id=str(locked.id),
            error=type(exc).__name__,
        )
        raise HostedHarnessError(
            "scheduler_unavailable",
            "The build could not start; the design is still waiting for approval",
            status_code=503,
        ) from exc
    logger.info(
        "design_review_approved",
        environment_id=str(locked.id),
        user_id=str(getattr(user, "id", "") or ""),
        edits=len(review.get("edits") or []),
    )
    return locked


def discard(job: HostedHarnessJob, *, user: Any) -> HostedHarnessJob:
    now = timezone.now()
    with transaction.atomic():
        locked, review = _locked(job)
        _require_open(review)
        _write(locked, {**review, "status": DISCARDED})
        locked.state = HostedHarnessJob.State.CANCELED
        locked.cancel_requested_at = now
        locked.cancel_reason = DISCARD_REASON
        locked.deleted = True
        locked.deleted_at = now
        locked.save(
            update_fields=[
                "payload",
                "state",
                "cancel_requested_at",
                "cancel_reason",
                "deleted",
                "deleted_at",
                "updated_at",
            ]
        )
    logger.info(
        "design_review_discarded",
        environment_id=str(locked.id),
        user_id=str(getattr(user, "id", "") or ""),
    )
    return locked


def apply_edits_to_contract(
    contract: dict[str, Any], edits: list[dict[str, Any]]
) -> dict[str, Any]:
    edited = copy.deepcopy(contract)
    rules = [
        {"id": f"rule-{index}", "text": str(text)}
        for index, text in enumerate(edited.get("hard_constraints") or [], 1)
    ]
    cases = [
        {"id": f"use-case-{index}", "text": str(text)}
        for index, text in enumerate(edited.get("real_use_cases") or [], 1)
    ]
    removed: dict[str, set[str]] = {kind: set() for kind in KINDS}
    for edit in edits:
        op, kind, item_id = edit["op"], edit["kind"], edit.get("id")
        if op == "remove":
            removed[kind].add(str(item_id))
        elif op == "edit" and kind == "rule":
            for rule in rules:
                if rule["id"] == item_id:
                    rule["text"] = edit["text"]
    edited["hard_constraints"] = [
        rule["text"] for rule in rules if rule["id"] not in removed["rule"]
    ]
    edited["real_use_cases"] = [
        case["text"] for case in cases if case["id"] not in removed["use_case"]
    ]
    edited["tools"] = [
        tool
        for tool in edited.get("tools") or []
        if not (isinstance(tool, dict) and tool.get("name") in removed["tool"])
    ]
    schema = edited.get("data_schema")
    if isinstance(schema, dict):
        edited["data_schema"] = {
            name: body for name, body in schema.items() if name not in removed["table"]
        }
    return edited
