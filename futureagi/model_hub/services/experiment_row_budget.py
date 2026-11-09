"""Atomic per-row reservations, including unfinished and uncertain provider calls."""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from model_hub.models.develop_dataset import Column, Row
from model_hub.models.experiment_row_budget import ExperimentRowBudget


def _create_budget(experiment, row_id, variant):
    limits = experiment.run_policy.get("limits") or {}
    if not limits:
        return None
    dataset = experiment.snapshot_dataset
    if not dataset or (
        dataset.organization_id != experiment.dataset.organization_id
        or dataset.workspace_id != experiment.dataset.workspace_id
    ):
        raise ValueError("Budget dataset does not match the experiment.")
    Row.objects.get(id=row_id, dataset=dataset, deleted=False)
    deadline = (
        timezone.now() + timedelta(seconds=limits["timeout_s"])
        if limits.get("timeout_s")
        else None
    )
    from temporalio import activity

    # Retries and cooperative resume share a run; an explicit rerun gets a new one.
    run_id = activity.info().workflow_run_id if activity.in_activity() else "direct"
    budget, _ = ExperimentRowBudget.objects.get_or_create(
        experiment=experiment,
        row_id=row_id,
        variant=f"{variant}:{run_id}",
        defaults={"limits": limits, "deadline": deadline},
    )
    return budget


def budget_for_prompt(experiment, row_id, column_id):
    if not experiment.run_policy.get("limits"):
        return None
    Column.objects.get(
        id=column_id, dataset_id=experiment.snapshot_dataset_id, deleted=False
    )
    return _create_budget(experiment, row_id, f"prompt:{column_id}")


def bind_graph_budget(experiment_id, row_id, config_id, graph_execution_id):
    from agent_playground.models import GraphExecution
    from model_hub.models.experiments import ExperimentAgentConfig, ExperimentsTable

    experiment = ExperimentsTable.objects.select_related(
        "dataset", "snapshot_dataset"
    ).get(id=experiment_id, deleted=False)
    if not experiment.run_policy.get("limits"):
        return None
    config = ExperimentAgentConfig.objects.get(
        id=config_id, experiment_dataset__experiment=experiment, deleted=False
    )
    GraphExecution.no_workspace_objects.get(
        id=graph_execution_id,
        graph_version_id=config.graph_version_id,
        graph_version__graph__organization_id=experiment.dataset.organization_id,
        graph_version__graph__workspace_id=config.graph.workspace_id,
    )
    budget = _create_budget(experiment, row_id, f"agent:{config_id}")
    ExperimentRowBudget.objects.filter(id=budget.id).update(
        graph_execution_id=graph_execution_id
    )
    return budget


def budget_for_graph(graph_execution_id):
    from agent_playground.models import GraphExecution

    seen = set()
    while graph_execution_id:
        if str(graph_execution_id) in seen:
            raise ValueError("Cyclic graph execution ancestry.")
        seen.add(str(graph_execution_id))
        budget = ExperimentRowBudget.objects.filter(
            graph_execution_id=graph_execution_id
        ).first()
        if budget:
            return budget
        graph = GraphExecution.no_workspace_objects.select_related(
            "parent_node_execution"
        ).get(id=graph_execution_id)
        graph_execution_id = (
            graph.parent_node_execution.graph_execution_id
            if graph.parent_node_execution_id
            else None
        )
    return None


def check_deadline(budget):
    if budget and (
        budget.exhausted or (budget.deadline and timezone.now() >= budget.deadline)
    ):
        raise ValueError(
            "Per-row execution limit reached (timeout_s or exhausted budget)."
        )


def _cost(tokens, output, rates):
    return (Decimal(tokens) * rates[0] + Decimal(output) * rates[1]) / Decimal(1000000)


def _rates(model):
    from agentic_eval.core_evals.run_prompt.model_pricing import get_model_pricing

    pricing = get_model_pricing(model)
    if (
        not pricing
        or not {"input_per_1M_tokens", "output_per_1M_tokens"} <= pricing.keys()
    ):
        raise ValueError("A known token price is required for per-row cost_usd limits.")
    return Decimal(str(pricing["input_per_1M_tokens"])), Decimal(
        str(pricing["output_per_1M_tokens"])
    )


def reserve(budget_id, payload):
    """Reserve a conservative upper bound; concurrent calls cannot spend it twice.

    UTF-8 bytes plus framing deliberately over-reserve text inputs. Never estimate
    an unbounded completion or allow provider retries to multiply an admission.
    """
    with transaction.atomic():
        budget = ExperimentRowBudget.objects.select_for_update().get(id=budget_id)
        check_deadline(budget)
        limits, usage = budget.limits, dict(budget.usage)
        request = dict(payload)
        messages = payload.get("messages", [])
        count = len(messages) + 1
        if payload.get("n", 1) != 1:
            raise ValueError("Per-row limits require one completion per request.")
        if (
            limits.get("messages")
            and usage.get("messages", 0) + count > limits["messages"]
        ):
            raise ValueError("Per-row messages limit reached.")
        input_tokens, max_output, cost = 0, 0, Decimal(0)
        rates = None
        if limits.get("tokens") or limits.get("cost_usd"):
            for message in messages:
                content = message.get("content")
                if isinstance(content, list) and any(
                    part.get("type") != "text" for part in content
                ):
                    raise ValueError(
                        "Token and cost limits require text-only messages."
                    )
            input_tokens = len(
                json.dumps(messages, ensure_ascii=False).encode()
            ) + 64 * len(messages)
            input_tokens += len(json.dumps(payload.get("tools") or []).encode())
            input_tokens += len(
                json.dumps(payload.get("response_format") or {}).encode()
            )
            max_output = int(
                payload.get("max_completion_tokens")
                or payload.get("max_tokens")
                or 4096
            )
            if limits.get("tokens"):
                max_output = min(
                    max_output, limits["tokens"] - usage.get("tokens", 0) - input_tokens
                )
            if limits.get("cost_usd"):
                rates = _rates(payload["model"])
                remaining = Decimal(str(limits["cost_usd"])) - Decimal(
                    usage.get("cost_usd", "0")
                )
                remaining -= _cost(input_tokens, 0, rates)
                if remaining < 0:
                    raise ValueError("Per-row cost_usd limit reached.")
                if rates[1] > 0:
                    max_output = min(
                        max_output, int(remaining * Decimal(1000000) / rates[1])
                    )
                cost = _cost(input_tokens, max(0, max_output), rates)
            if max_output < 1:
                raise ValueError("Per-row tokens or cost_usd limit reached.")
            token_key = (
                "max_completion_tokens"
                if "max_completion_tokens" in request
                else "max_tokens"
            )
            request[token_key] = max_output
        if budget.deadline:
            request["timeout"] = max(
                0.001, (budget.deadline - timezone.now()).total_seconds()
            )
        request["num_retries"] = 0
        reservation_id = str(uuid.uuid4())
        reserved = {
            "messages": count,
            "tokens": input_tokens + max_output,
            "cost_usd": str(cost),
            "rates": [str(x) for x in rates] if rates else None,
        }
        budget.reservations = {**budget.reservations, reservation_id: reserved}
        budget.usage = {
            "messages": usage.get("messages", 0) + count,
            "tokens": usage.get("tokens", 0) + reserved["tokens"],
            "cost_usd": str(Decimal(usage.get("cost_usd", "0")) + cost),
        }
        budget.save(update_fields=["usage", "reservations"])
    return reservation_id, request


def settle(budget_id, reservation_id, response):
    """Only confirmed usage refunds a reservation; an uncertain call stays charged."""
    with transaction.atomic():
        budget = ExperimentRowBudget.objects.select_for_update().get(id=budget_id)
        reserved = budget.reservations.get(reservation_id)
        if reserved is None:
            return
        raw = (
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        raw = (
            raw
            if isinstance(raw, dict)
            else (raw.model_dump() if hasattr(raw, "model_dump") else None)
        )
        missing_usage = not raw or any(
            type(raw.get(k)) is not int or raw[k] < 0
            for k in ("prompt_tokens", "completion_tokens")
        )
        if missing_usage:
            budget.exhausted = True
            budget.save(update_fields=["exhausted"])
        else:
            _settle_confirmed(budget, reservation_id, reserved, raw)
    if missing_usage:
        raise ValueError("Provider usage is required to reconcile per-row limits.")
    check_deadline(budget)


def _settle_confirmed(budget, reservation_id, reserved, raw):
    tokens = raw["prompt_tokens"] + raw["completion_tokens"]
    rates = reserved.get("rates")
    actual_cost = (
        _cost(
            raw["prompt_tokens"], raw["completion_tokens"], [Decimal(x) for x in rates]
        )
        if rates
        else Decimal(0)
    )
    budget.usage = {
        **budget.usage,
        "tokens": budget.usage["tokens"] - reserved["tokens"] + tokens,
        "cost_usd": str(
            Decimal(budget.usage["cost_usd"])
            - Decimal(reserved["cost_usd"])
            + actual_cost
        ),
    }
    budget.reservations = {
        k: v for k, v in budget.reservations.items() if k != reservation_id
    }
    budget.exhausted = budget.exhausted or bool(
        (
            budget.limits.get("tokens")
            and budget.usage["tokens"] > budget.limits["tokens"]
        )
        or (
            budget.limits.get("cost_usd")
            and Decimal(budget.usage["cost_usd"])
            > Decimal(str(budget.limits["cost_usd"]))
        )
    )
    budget.save(update_fields=["usage", "reservations", "exhausted"])
