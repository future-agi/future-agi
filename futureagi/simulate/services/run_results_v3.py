"""Read models for the v3 simulation run results surface."""

from __future__ import annotations

import ast
import json
import math
from collections import defaultdict
from typing import Any

from django.core.cache import cache

from model_hub.models.develop_dataset import Cell
from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.utils.eval_summary import iter_live_eval_outputs

GROUP_FIELDS = {
    "goal": "goal",
    "status": "outcome",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return float(value)
    return None


def _truth_value(eval_data: Any) -> bool | None:
    if not isinstance(eval_data, dict):
        return None
    if str(eval_data.get("status") or "").strip().lower() in {
        "pending",
        "skipped",
        "error",
    }:
        return None
    value = eval_data.get("output")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"pass", "passed", "true", "success", "successful"}:
            return True
        if normalized in {"fail", "failed", "false", "failure", "unsuccessful"}:
            return False
    return None


def call_outcome(call: CallExecution, live_eval_ids: set[str]) -> str:
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    harness_outcome = str(metadata.get("harness_outcome_status") or "").lower()
    if harness_outcome in {"passed", "pass", "success", "successful"}:
        return "passed"
    if harness_outcome in {"failed", "fail", "failure"}:
        return "failed"
    if harness_outcome in {"error", "errored", "cancelled", "canceled"}:
        return "error"
    if harness_outcome in {"inconclusive", "unknown", "skipped"}:
        return "inconclusive"

    if call.status in {
        CallExecution.CallStatus.FAILED,
        CallExecution.CallStatus.CANCELLED,
    }:
        return "error"
    if call.status != CallExecution.CallStatus.COMPLETED:
        return "inconclusive"

    verdicts = [
        verdict
        for _, data in iter_live_eval_outputs(call.eval_outputs, live_eval_ids)
        if (verdict := _truth_value(data)) is not None
    ]
    if any(verdict is False for verdict in verdicts):
        return "failed"
    if verdicts:
        return "passed"
    return "inconclusive"


def _provider(call: CallExecution) -> str | None:
    if isinstance(call.provider_call_data, dict):
        for name, payload in call.provider_call_data.items():
            if isinstance(payload, dict) and payload:
                return str(name)
    agent = call.test_execution.agent_definition
    return getattr(agent, "provider", None) if agent else None


def function_calls(call: CallExecution) -> list[dict[str, Any]]:
    """Return provider-recorded function calls without exposing provider internals."""
    provider_data = call.provider_call_data
    if not isinstance(provider_data, dict):
        return []
    calls: list[dict[str, Any]] = []
    for payload in provider_data.values():
        if not isinstance(payload, dict) or not isinstance(
            payload.get("tool_calls"), list
        ):
            continue
        calls.extend(item for item in payload["tool_calls"] if isinstance(item, dict))
    return calls


def _structured_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return value
    return value


def _persona_details(value: Any) -> dict[str, Any] | None:
    value = _structured_value(value)
    if isinstance(value, str):
        return {"name": value, "voice": None, "age": None, "traits": []}
    if not isinstance(value, dict):
        return None

    name = next(
        (
            str(value[key])
            for key in ("name", "persona", "label", "title")
            if value.get(key)
        ),
        None,
    )
    voice = value.get("voice") or value.get("voice_description")
    if not voice:
        voice_parts = [value.get("accent"), value.get("gender")]
        voice = " ".join(str(part) for part in voice_parts if part) or None
    age = value.get("age") or value.get("age_group")
    traits = value.get("traits") or value.get("characteristics")
    if not traits:
        traits = [value.get("personality"), value.get("communication_style")]
    if isinstance(traits, str):
        traits = [item.strip() for item in traits.split(",") if item.strip()]
    elif isinstance(traits, (tuple, set)):
        traits = list(traits)
    elif not isinstance(traits, list):
        traits = []
    traits = [str(item) for item in traits if item]
    return {
        "name": name,
        "voice": str(voice) if voice else None,
        "age": str(age) if age is not None else None,
        "traits": traits,
    }


def _row_dimensions(calls: list[CallExecution]) -> dict[str, dict[str, Any]]:
    row_ids = {
        str(call.row_id or (call.call_metadata or {}).get("row_id"))
        for call in calls
        if call.row_id
        or (isinstance(call.call_metadata, dict) and call.call_metadata.get("row_id"))
    }
    if not row_ids:
        return {}
    dimensions: dict[str, dict[str, Any]] = defaultdict(dict)
    cells = Cell.all_objects.filter(
        row_id__in=row_ids,
        column__name__in=["persona", "use_case", "goal", "outcome", "situation"],
    ).select_related("column")
    for cell in cells:
        dimensions[str(cell.row_id)][cell.column.name] = cell.value
    return dimensions


def _eval_rows(call: CallExecution, live_eval_ids: set[str]) -> list[dict[str, Any]]:
    rows = []
    for eval_id, data in iter_live_eval_outputs(call.eval_outputs, live_eval_ids):
        if not isinstance(data, dict):
            continue
        value = data.get("output")
        measured = str(data.get("status") or "").strip().lower() not in {
            "pending",
            "skipped",
            "error",
        }
        numeric = _number(value) if measured else None
        verdict = _truth_value(data)
        score = numeric
        if verdict is not None:
            score = 1.0 if verdict else 0.0
        elif numeric is not None and numeric > 1:
            score = numeric / 100
        rows.append(
            {
                "id": str(eval_id),
                "name": data.get("name") or str(eval_id),
                "type": data.get("output_type") or "",
                "value": value,
                "score": round(score, 4) if score is not None else None,
                "passed": verdict,
                "reason": data.get("reason") or "",
                "status": data.get("status") or "completed",
            }
        )
    return rows


def build_evaluation_catalog(
    execution: TestExecution,
) -> tuple[list[dict[str, str]], set[str]]:
    """Return stable configured and harness-native columns for an execution."""
    cache_key = None
    if execution.status == TestExecution.ExecutionStatus.COMPLETED:
        version = execution.completed_at or execution.updated_at
        cache_key = f"simulate:v3:eval-catalog:{execution.id}:{version.timestamp()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached[0], set(cached[1])
    configs = list(
        SimulateEvalConfig.objects.filter(
            run_test=execution.run_test, deleted=False
        ).values("id", "name")
    )
    columns = [
        {"id": str(config["id"]), "name": str(config["name"])} for config in configs
    ]
    live_eval_ids = {column["id"] for column in columns}
    known = set(live_eval_ids)
    # The catalog is execution-wide so columns never vary by page or filter.
    outputs = CallExecution.objects.filter(test_execution=execution).values_list(
        "eval_outputs", flat=True
    )
    for eval_outputs in outputs:
        if not isinstance(eval_outputs, dict):
            continue
        for eval_id, data in eval_outputs.items():
            eval_id = str(eval_id)
            if (
                eval_id in known
                or not isinstance(data, dict)
                or data.get("source") != "harness"
            ):
                continue
            columns.append({"id": eval_id, "name": str(data.get("name") or eval_id)})
            known.add(eval_id)
    if cache_key:
        cache.set(cache_key, (columns, list(live_eval_ids)), timeout=60 * 60)
    return columns, live_eval_ids


def build_call_rows(
    execution: TestExecution,
    calls: list[CallExecution] | None = None,
    columns: list[dict[str, str]] | None = None,
    live_eval_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if calls is None:
        calls = list(
            CallExecution.objects.filter(test_execution=execution)
            .select_related("scenario", "test_execution__agent_definition")
            .order_by("-updated_at")
        )
    if columns is None or live_eval_ids is None:
        catalog, catalog_live_ids = build_evaluation_catalog(execution)
        columns = catalog if columns is None else columns
        live_eval_ids = catalog_live_ids if live_eval_ids is None else live_eval_ids
    dimensions = _row_dimensions(calls)
    rows = []
    harness_columns: dict[str, str] = {}
    for call in calls:
        metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
        row_data = metadata.get("row_data")
        row_data = row_data if isinstance(row_data, dict) else {}
        row_id = str(call.row_id or metadata.get("row_id") or "")
        row_dimensions = dimensions.get(row_id, {})
        scenario_metadata = (
            call.scenario.metadata if isinstance(call.scenario.metadata, dict) else {}
        )
        persona_details = _persona_details(
            metadata.get("persona")
            or row_data.get("persona")
            or row_dimensions.get("persona")
            or scenario_metadata.get("persona")
        )
        persona = persona_details.get("name") if persona_details else None
        goal = (
            getattr(call, "result_goal", None)
            or metadata.get("use_case")
            or metadata.get("goal")
            or row_data.get("use_case")
            or row_data.get("goal")
            or row_dimensions.get("use_case")
            or row_dimensions.get("goal")
            or scenario_metadata.get("use_case")
            or scenario_metadata.get("goal")
            or call.scenario.name
        )
        receipt = metadata.get("hosted_harness_receipt")
        receipt = receipt if isinstance(receipt, dict) else {}
        raw_sub_goals = receipt.get("sub_goals") or metadata.get("sub_goals") or []
        sub_goals = [
            str(item.get("name") if isinstance(item, dict) else item)
            for item in raw_sub_goals
            if (item.get("name") if isinstance(item, dict) else item)
        ]
        ideal_outcome = row_data.get("outcome") or row_dimensions.get("outcome")
        situation = row_data.get("situation") or row_dimensions.get("situation")
        conversation_branch = (
            receipt.get("scenario_key")
            or metadata.get("conversation_branch")
            or scenario_metadata.get("conversation_branch")
        )
        metrics = call.conversation_metrics_data or {}
        tokens = _number(metrics.get("total_tokens"))
        latency = _number(call.avg_agent_latency_ms)
        if latency is None:
            latency = _number(metrics.get("avg_latency_ms"))
        turn_count = _number(metrics.get("turn_count"))
        if turn_count is None:
            turn_count = _number(metrics.get("bot_message_count"))
        evaluations = _eval_rows(call, live_eval_ids)
        for evaluation in evaluations:
            harness_columns[evaluation["id"]] = evaluation["name"]
        rows.append(
            {
                "id": str(call.id),
                "scenario": call.scenario.name,
                "scenario_details": str(situation) if situation else None,
                "goal": str(goal),
                "ideal_outcome": str(ideal_outcome) if ideal_outcome else None,
                "conversation_branch": (
                    str(conversation_branch) if conversation_branch else None
                ),
                "persona": persona,
                "persona_details": persona_details,
                "sub_goals": sub_goals,
                "outcome": call_outcome(call, live_eval_ids),
                "execution_status": call.status,
                "harness_outcome_status": metadata.get("harness_outcome_status"),
                "eval_started": metadata.get("eval_started") is True,
                "eval_completed": metadata.get("eval_completed") is True,
                "csat_status": metadata.get("csat_status") or None,
                "csat_error": metadata.get("csat_error") or None,
                "source_scenario_key": metadata.get("harness_scenario_key"),
                "trial_index": metadata.get("harness_trial_index"),
                "modality": call.simulation_call_type,
                "provider": _provider(call),
                "started_at": call.started_at,
                "completed_at": call.completed_at,
                "duration_seconds": call.duration_seconds,
                "latency_ms": latency,
                "turn_count": int(turn_count) if turn_count is not None else None,
                "tokens": int(tokens) if tokens is not None else None,
                "cost_cents": call.cost_cents,
                "cost_breakdown_cents": {
                    "stt": call.stt_cost_cents,
                    "llm": call.llm_cost_cents,
                    "tts": call.tts_cost_cents,
                    "storage": call.storage_cost_cents,
                    "customer": call.customer_cost_cents,
                },
                "csat": call.overall_score,
                "ended_reason": call.ended_reason,
                "error_message": call.error_message,
                "evaluations": evaluations,
            }
        )
    columns = list(columns)
    known = {column["id"] for column in columns}
    columns.extend(
        {"id": eval_id, "name": name}
        for eval_id, name in harness_columns.items()
        if eval_id not in known
    )
    return rows, columns
