"""Validated writes for run plans, run items, and run lifecycle transitions."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from tfc.routers import force_primary

_RUN_KEY = "_futureagi_run_write"
_TERMINAL_ITEMS = frozenset({"completed", "failed", "skipped"})
_SCORE_STATUSES = frozenset(
    {"assessed", "unknown", "not_assessed", "unsupported", "error"}
)
_TRANSITIONS = {
    "draft": {"queued", "cancelled"},
    "queued": {"running", "cancelled"},
    "running": {"partial", "cancelled"},
    "partial": {"running", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class RunWriteError(ValueError):
    """A run write failed validation, membership, lifecycle, or idempotency checks."""


def _canonical(value: Any) -> str:
    def check(value: Any, active: set[int], depth: int = 0) -> Any:
        if depth > 100:
            raise RunWriteError("receipt payload is too deeply nested")
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str):
            if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise RunWriteError("receipt payload contains an invalid JSON string")
            return value
        if isinstance(value, int):
            try:
                digits = len(str(abs(value)))
            except ValueError as exc:
                raise RunWriteError("receipt payload integer is too large") from exc
            if digits > 4300:
                raise RunWriteError("receipt payload integer is too large")
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise RunWriteError("receipt payload contains a non-finite number")
            return value
        if isinstance(value, list):
            identity = id(value)
            if identity in active:
                raise RunWriteError("receipt payload contains a cycle")
            active.add(identity)
            result = [check(item, active, depth + 1) for item in value]
            active.remove(identity)
            return result
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise RunWriteError("receipt payload object keys must be strings")
            identity = id(value)
            if identity in active:
                raise RunWriteError("receipt payload contains a cycle")
            active.add(identity)
            result = {
                check(key, active, depth + 1): check(item, active, depth + 1)
                for key, item in value.items()
            }
            active.remove(identity)
            return result
        raise RunWriteError("receipt payload contains a non-JSON value")

    return json.dumps(
        check(value, set()), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _uuid(value: UUID | str) -> str:
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RunWriteError("identifier must be a valid UUID") from exc


def _receipt(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunWriteError("receipt_key must be a non-empty string")
    return value.strip()


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RunWriteError("metadata must be a mapping")
    return dict(value)


def _envelope(value: Any) -> dict[str, Any]:
    metadata = _metadata(value)
    nested = metadata.get(_RUN_KEY, {})
    if not isinstance(nested, Mapping):
        raise RunWriteError(f"{_RUN_KEY} must be a mapping")
    metadata[_RUN_KEY] = dict(nested)
    return metadata


def _scope(
    organization_id: UUID | str, workspace_id: UUID | str | None
) -> dict[str, Any]:
    scope = {
        "dataset_version__dataset__organization_id": _uuid(organization_id),
        "dataset_version__dataset__deleted": False,
        "dataset_version__deleted": False,
        "deleted": False,
    }
    if workspace_id is not None:
        scope["dataset_version__dataset__workspace_id"] = _uuid(workspace_id)
    return scope


@contextmanager
def _run(
    run_id: UUID | str, organization_id: UUID | str, workspace_id: UUID | str | None
):
    with force_primary(), transaction.atomic():
        try:
            value = (
                ExperimentRun.no_workspace_objects.select_for_update(of=("self",))
                .select_related("dataset_version", "dataset_version__dataset")
                .filter(id=_uuid(run_id), **_scope(organization_id, workspace_id))
                .get()
            )
        except ExperimentRun.DoesNotExist as exc:
            raise RunWriteError("experiment run was not found in this tenant") from exc
        yield value


def _save_run(run: ExperimentRun, fields: Sequence[str]) -> ExperimentRun:
    run.full_clean()
    run.save(update_fields=[*fields, "updated_at"])
    return run


def _identities(
    values: Iterable[tuple[UUID | str, int] | Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if values is None:
        raise RunWriteError("expected item identities")
    result: dict[tuple[str, int], dict[str, Any]] = {}
    count = 0
    for value in values:
        count += 1
        if isinstance(value, Mapping):
            example_id, trial = value.get("example_id"), value.get("trial_number", 1)
        else:
            try:
                example_id, trial = value
            except (TypeError, ValueError) as exc:
                raise RunWriteError("expected item identities") from exc
        identity = (_uuid(example_id), trial)
        if type(trial) is not int or trial < 1 or identity in result:
            raise RunWriteError(
                "item identities must have unique positive trial numbers"
            )
        result[identity] = {"example_id": identity[0], "trial_number": trial}
    if count != len(result):
        raise RunWriteError("item identities must be unique")
    return [result[key] for key in sorted(result)]


def _planned(run: ExperimentRun) -> set[tuple[str, int]]:
    plan = _envelope(run.metadata)[_RUN_KEY].get("plan")
    if not isinstance(plan, Mapping) or not isinstance(plan.get("identities"), list):
        raise RunWriteError("run plan must be pinned before execution")
    identities = _identities(plan["identities"])
    if not identities or plan.get("plan_hash") != _digest(identities):
        raise RunWriteError("run plan digest is invalid")
    return {(item["example_id"], item["trial_number"]) for item in identities}


def _score_refs(
    run: ExperimentRun, refs: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    if not isinstance(refs, (list, tuple)):
        raise RunWriteError("score_refs must be a list")
    allowed: set[str] = set()
    if not isinstance(run.evaluator_versions, list):
        raise RunWriteError("evaluator_versions must be a list")
    for version in run.evaluator_versions:
        if isinstance(version, str):
            allowed.add(_uuid(version))
        elif isinstance(version, Mapping):
            identifier = version.get(
                "evaluator_version_id", version.get("version_id", version.get("id"))
            )
            if identifier is None:
                raise RunWriteError("each evaluator version must identify a UUID")
            allowed.add(_uuid(identifier))
        else:
            raise RunWriteError("each evaluator version must identify a UUID")
    checked: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, Mapping):
            raise RunWriteError("each score reference must be a mapping")
        identifier = ref.get("evaluator_version_id", ref.get("version_id"))
        if identifier is None or _uuid(identifier) not in allowed:
            raise RunWriteError("score reference evaluator version is not pinned")
        value = ref.get("value", ref.get("score"))
        status = ref.get("status", "assessed" if value is not None else "not_assessed")
        if not isinstance(status, str) or status not in _SCORE_STATUSES:
            raise RunWriteError("score reference status is invalid")
        if status == "assessed":
            try:
                valid = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                )
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                raise RunWriteError("assessed score reference must be finite numeric")
        checked.append(dict(ref))
    return checked


def pin_run_plan(
    *,
    run_id: UUID | str,
    organization_id: UUID | str,
    expected_items: Iterable[tuple[UUID | str, int] | Mapping[str, Any]],
    workspace_id: UUID | str | None = None,
) -> ExperimentRun:
    """Pin the exact active example/trial set required for finalization."""
    plan_items = _identities(expected_items)
    if not plan_items:
        raise RunWriteError("run plan must contain at least one item")
    with _run(run_id, organization_id, workspace_id) as run:
        ids = [item["example_id"] for item in plan_items]
        available = {
            str(value)
            for value in DatasetVersionItem.no_workspace_objects.filter(
                dataset_version_id=run.dataset_version_id,
                example_id__in=ids,
                deleted=False,
            ).values_list("example_id", flat=True)
        }
        if set(ids) - available:
            raise RunWriteError(
                "run plan contains an example outside the dataset version"
            )
        metadata = _envelope(run.metadata)
        plan = {"identities": plan_items, "plan_hash": _digest(plan_items)}
        previous = metadata[_RUN_KEY].get("plan")
        if previous is not None:
            if previous == plan:
                return run
            raise RunWriteError("run plan is already pinned to a different item set")
        if run.status not in {"draft", "queued"}:
            raise RunWriteError("run plan can only be pinned before execution")
        metadata[_RUN_KEY]["plan"] = plan
        run.metadata = metadata
        return _save_run(run, ["metadata"])


def claim_run_item(
    *,
    run_id: UUID | str,
    example_id: UUID | str,
    trial_number: int = 1,
    receipt_key: str,
    organization_id: UUID | str,
    workspace_id: UUID | str | None = None,
) -> ExperimentRunItem:
    """Claim one pinned item and make it running; same receipt is idempotent."""
    example_id = _uuid(example_id)
    if type(trial_number) is not int or trial_number < 1:
        raise RunWriteError("trial_number must be a positive integer")
    receipt_key = _receipt(receipt_key)
    with _run(run_id, organization_id, workspace_id) as run:
        if run.status not in {"running", "partial"} or run.completed_at is not None:
            raise RunWriteError("run item can only be claimed while executing")
        if (example_id, trial_number) not in _planned(run):
            raise RunWriteError("item identity is not in the pinned run plan")
        membership = DatasetVersionItem.no_workspace_objects.filter(
            dataset_version_id=run.dataset_version_id,
            example_id=example_id,
            deleted=False,
        ).first()
        if membership is None:
            raise RunWriteError("example_id is not an active dataset-version member")
        item = (
            ExperimentRunItem.no_workspace_objects.select_for_update()
            .filter(
                run=run, example_id=example_id, trial_number=trial_number, deleted=False
            )
            .first()
        )
        if item is not None:
            claim = _envelope(item.metadata)[_RUN_KEY].get("claim_receipt")
            if isinstance(claim, Mapping) and claim.get("receipt_key") == receipt_key:
                return item
            raise RunWriteError("run item is already claimed")
        metadata = _metadata(membership.metadata)
        metadata.pop(_RUN_KEY, None)
        metadata = _envelope(metadata)
        metadata[_RUN_KEY]["claim_receipt"] = {"receipt_key": receipt_key}
        item = ExperimentRunItem(
            run=run,
            example_id=example_id,
            trial_number=trial_number,
            status="running",
            input=membership.input,
            expected=membership.expected,
            metadata=metadata,
        )
        item.full_clean()
        item.save()
        return item


def record_run_item_result(
    *,
    item_id: UUID | str,
    status: Literal["completed", "failed", "skipped"],
    receipt_key: str,
    organization_id: UUID | str,
    output: Any = None,
    error: Any = None,
    trace_id: str | None = None,
    root_span_id: str | None = None,
    latency_ms: float | None = None,
    token_usage: Any = None,
    cost: float | None = None,
    score_refs: Sequence[Mapping[str, Any]] = (),
    workspace_id: UUID | str | None = None,
) -> ExperimentRunItem:
    """Write a terminal result; identical receipt and payload retries are no-ops."""
    if not isinstance(status, str) or status not in _TERMINAL_ITEMS:
        raise RunWriteError("result status must be completed, failed, or skipped")
    if status == "completed" and error is not None:
        raise RunWriteError("completed result cannot contain an error")
    receipt_key = _receipt(receipt_key)
    for name, value in (("latency_ms", latency_ms), ("cost", cost)):
        try:
            valid = value is None or (
                not isinstance(value, bool)
                and math.isfinite(float(value))
                and value >= 0
            )
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise RunWriteError(f"{name} must be finite and non-negative")
    with force_primary(), transaction.atomic():
        try:
            run = (
                ExperimentRun.no_workspace_objects.select_for_update(of=("self",))
                .select_related("dataset_version", "dataset_version__dataset")
                .filter(
                    **_scope(organization_id, workspace_id),
                    items__id=_uuid(item_id),
                    items__deleted=False,
                )
                .get()
            )
            item = ExperimentRunItem.no_workspace_objects.select_for_update().get(
                id=_uuid(item_id), run=run, deleted=False
            )
        except (ExperimentRun.DoesNotExist, ExperimentRunItem.DoesNotExist) as exc:
            raise RunWriteError("run item was not found in this tenant") from exc
        if (str(item.example_id), item.trial_number) not in _planned(run):
            raise RunWriteError("item identity is not in the pinned run plan")
        if not DatasetVersionItem.no_workspace_objects.filter(
            dataset_version_id=run.dataset_version_id, example_id=item.example_id
        ).exists():
            raise RunWriteError("example_id is not an active dataset-version member")
        checked = _score_refs(run, score_refs)
        payload = {
            "status": status,
            "output": output,
            "error": error,
            "trace_id": trace_id,
            "root_span_id": root_span_id,
            "latency_ms": latency_ms,
            "token_usage": token_usage,
            "cost": cost,
            "score_refs": checked,
        }
        payload_hash = _digest(payload)
        metadata = _envelope(item.metadata)
        previous = metadata[_RUN_KEY].get("result_receipt")
        if item.status in _TERMINAL_ITEMS:
            if (
                isinstance(previous, Mapping)
                and previous.get("receipt_key") == receipt_key
                and previous.get("payload_hash") == payload_hash
            ):
                return item
            raise RunWriteError("terminal run item cannot be overwritten")
        if run.completed_at is not None or run.status not in {"running", "partial"}:
            raise RunWriteError(
                "cannot write a new result after the run is terminal or inactive"
            )
        if not DatasetVersionItem.no_workspace_objects.filter(
            dataset_version_id=run.dataset_version_id,
            example_id=item.example_id,
            deleted=False,
        ).exists():
            raise RunWriteError(
                "example_id is no longer an active dataset-version member"
            )
        metadata[_RUN_KEY]["result_receipt"] = {
            "receipt_key": receipt_key,
            "payload_hash": payload_hash,
        }
        item.status, item.output, item.error = status, output, error
        item.trace_id, item.root_span_id = trace_id, root_span_id
        item.latency_ms, item.token_usage, item.cost = latency_ms, token_usage, cost
        item.score_refs, item.metadata = checked, metadata
        item.full_clean()
        item.save(
            update_fields=[
                "status",
                "output",
                "error",
                "trace_id",
                "root_span_id",
                "latency_ms",
                "token_usage",
                "cost",
                "score_refs",
                "metadata",
                "updated_at",
            ]
        )
        return item


def transition_run(
    *,
    run_id: UUID | str,
    status: str,
    organization_id: UUID | str,
    workspace_id: UUID | str | None = None,
    now: datetime | None = None,
) -> ExperimentRun:
    """Apply draft/queue/running/partial/cancelled transitions under a lock."""
    if not isinstance(status, str) or status not in _TRANSITIONS:
        raise RunWriteError(f"unknown run status: {status}")
    with _run(run_id, organization_id, workspace_id) as run:
        if run.status == status:
            return run
        if run.completed_at is not None:
            raise RunWriteError("sealed run cannot transition")
        if status not in _TRANSITIONS[run.status]:
            raise RunWriteError(f"cannot transition run from {run.status} to {status}")
        if status in {"queued", "running"}:
            _planned(run)
        timestamp = now or timezone.now()
        fields = ["status"]
        run.status = status
        if status == "running" and run.started_at is None:
            run.started_at, fields = timestamp, ["status", "started_at"]
        if status == "cancelled":
            run.completed_at, fields = timestamp, [*fields, "completed_at"]
        return _save_run(run, fields)


def finalize_run(
    *,
    run_id: UUID | str,
    organization_id: UUID | str,
    workspace_id: UUID | str | None = None,
    now: datetime | None = None,
) -> ExperimentRun:
    """Derive completed/partial/failed only from the pinned, terminal item set."""
    with _run(run_id, organization_id, workspace_id) as run:
        if run.status == "cancelled":
            raise RunWriteError("cancelled run cannot be finalized")
        expected = _planned(run)
        items = list(
            ExperimentRunItem.no_workspace_objects.select_for_update().filter(
                run=run, deleted=False
            )
        )
        actual = {(str(item.example_id), item.trial_number): item for item in items}
        if expected != actual.keys():
            raise RunWriteError("run item set does not match pinned plan")
        if any(item.status not in _TERMINAL_ITEMS for item in items):
            raise RunWriteError("run has pending or running items")
        statuses = {item.status for item in items}
        target = (
            "completed"
            if statuses == {"completed"}
            else "partial"
            if "completed" in statuses
            else "failed"
        )
        if run.completed_at is not None:
            if run.status == target:
                return run
            raise RunWriteError("sealed run result no longer matches its items")
        if run.status not in {"running", "partial"}:
            raise RunWriteError(f"cannot finalize run from {run.status}")
        run.status, run.completed_at = target, now or timezone.now()
        return _save_run(run, ["status", "completed_at"])
