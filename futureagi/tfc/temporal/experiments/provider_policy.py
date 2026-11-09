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
    from model_hub.services.experiment_row_budget import budget_for_prompt

    budget = budget_for_prompt(experiment, row_id, column_id)
    if budget is not None and not USE_NEW_RUNPROMPT_HANDLERS:
        raise ValueError("Per-row limits require the current prompt executor.")
    with budget_context(budget):
        try:
            response, info = run_prompt.litellm_response()
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
        return response, info


def run_graph_node(runner, config, inputs, execution_context, graph_execution_id):
    from agentic_eval.core_evals.run_prompt.litellm_response import (
        USE_NEW_RUNPROMPT_HANDLERS,
    )
    from model_hub.services.experiment_row_budget import (
        budget_for_graph,
        check_deadline,
    )

    budget = budget_for_graph(graph_execution_id)
    if budget is not None and not USE_NEW_RUNPROMPT_HANDLERS:
        raise ValueError("Per-row limits require the current prompt executor.")
    with budget_context(budget):
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
