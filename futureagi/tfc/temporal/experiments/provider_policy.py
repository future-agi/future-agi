"""Scoped provider admission for experiment rows; ordinary calls are unchanged."""

from contextlib import contextmanager
from contextvars import ContextVar

_active_budget = ContextVar("experiment_row_budget", default=None)


@contextmanager
def budget_context(budget):
    token = _active_budget.set(budget)
    try:
        if budget is not None:
            from model_hub.services.experiment_row_budget import check_deadline

            check_deadline(budget)
        yield
    finally:
        _active_budget.reset(token)


def active_budget():
    return _active_budget.get()


def guarded_completion(payload):
    import litellm

    budget = active_budget()
    if budget is None:
        return litellm.completion(**payload, drop_params=True)
    from model_hub.services.experiment_row_budget import reserve, settle

    try:
        reservation_id, bounded_payload = reserve(budget.id, payload)
    except ValueError as error:
        budget._policy_error = str(error)
        raise
    response = litellm.completion(**bounded_payload, drop_params=True)
    try:
        settle(budget.id, reservation_id, response)
    except ValueError as error:
        budget._policy_error = str(error)
        raise
    return response


def require_budget_route(context, model_mode, factory):
    budget = active_budget()
    if budget is None:
        return
    if (
        model_mode in ("stt", "image_generation")
        or context.output_format == "audio"
        or model_mode in factory._handler_registry
        or factory._is_custom_model(context.model, context.organization_id)
    ):
        budget._policy_error = "Per-row limits require the text chat executor."
        raise ValueError(budget._policy_error)


def prompt_response(run_prompt, experiment, row_id, column_id):
    from agentic_eval.core_evals.run_prompt.litellm_response import (
        USE_NEW_RUNPROMPT_HANDLERS,
    )
    from model_hub.services import credit_ledger_port as credit_ledger
    from model_hub.services.experiment_row_budget import budget_for_prompt

    budget = budget_for_prompt(experiment, row_id, column_id)
    if budget is not None and not USE_NEW_RUNPROMPT_HANDLERS:
        raise ValueError("Per-row limits require the current prompt executor.")
    # One key (OK-E): prompt generation is its own meter, separate from the
    # evaluator's credit events. A managed row is held before dispatch, settled
    # once when the managed transport claimed it, and backs off per Retry-After
    # on the contract's retryable refusals; a BYOK row gets an inert meter and
    # runs exactly once.
    meter = credit_ledger.call_meter(
        organization=experiment.dataset.organization,
        workspace=experiment.dataset.workspace,
        model_name=run_prompt.model,
        purpose=credit_ledger.PURPOSE_EXPERIMENT_PROMPT,
        source="experiment",
        source_id=experiment.id,
        row_id=row_id,
        call_ref=column_id,
    )
    if budget is not None and meter.is_managed:
        # The managed transport must not bypass the existing per-row reserve /
        # settle policy or multiply its uncertain reservations through retries.
        raise credit_ledger.ManagedDispatchUnavailableError(
            "Future AGI credits cannot run experiment prompts with per-row limits yet. "
            "Add your own provider key to keep these limits enforced."
        )
    with budget_context(budget):
        try:
            (response, info), managed_call = credit_ledger.run_metered(
                meter,
                run_prompt.litellm_response,
                messages=run_prompt.messages,
                max_tokens=run_prompt.max_tokens,
                observe=credit_ledger.observe_value_info,
            )
        except credit_ledger.ManagedCallRefusal:
            # Typed refusals keep their type: terminal ones are ValueError (the
            # row is not retried), retryable ones are not (Temporal retries).
            raise
        except Exception as error:
            if budget is not None:
                # An uncertain admission stays charged; do not multiply it by retries.
                raise ValueError(
                    getattr(budget, "_policy_error", str(error))
                ) from error
            raise
        if budget is not None:
            from model_hub.services.experiment_row_budget import check_deadline

            budget.refresh_from_db()
            check_deadline(budget)
            info = {
                **info,
                "execution_usage": budget.usage,
                "execution_limits": budget.limits,
            }
        if managed_call is not None:
            info = {**info, "credit_settlement": managed_call.summary()}
        return response, info


def run_graph_node(runner, config, inputs, execution_context, graph_execution_id):
    from agentic_eval.core_evals.run_prompt.litellm_response import (
        USE_NEW_RUNPROMPT_HANDLERS,
    )
    from model_hub.services import credit_ledger_port as credit_ledger
    from model_hub.services.experiment_row_budget import (
        budget_for_graph,
        check_deadline,
    )

    budget = budget_for_graph(graph_execution_id)
    if budget is not None and not USE_NEW_RUNPROMPT_HANDLERS:
        raise ValueError("Per-row limits require the current prompt executor.")
    # One key (OK-E): graph nodes are not metered yet. A managed transport that
    # claims a hold inside a node gets a typed disclosure refusal before it
    # dispatches (never an unmetered managed call); BYOK nodes are unchanged.
    with budget_context(budget), credit_ledger.unmetered_surface("agent_graph_node"):
        try:
            result = runner.run(config, inputs, execution_context)
        except Exception as error:
            if budget is not None and getattr(budget, "_policy_error", None):
                raise ValueError(budget._policy_error) from error
            raise
        if budget is not None:
            budget.refresh_from_db()
            check_deadline(budget)
        return result
