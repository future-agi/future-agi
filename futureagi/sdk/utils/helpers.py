import structlog

logger = structlog.get_logger(__name__)
from model_hub.models.choices import ModelChoices
from tfc.constants.api_calls import APICallTypeChoices

model_to_api_call_type = {
    ModelChoices.TURING_LARGE: APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
    ModelChoices.TURING_SMALL: APICallTypeChoices.TURING_SMALL_EVALUATOR.value,
    ModelChoices.TURING_FLASH: APICallTypeChoices.TURING_FLASH_EVALUATOR.value,
    ModelChoices.PROTECT_FLASH: APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value,
    ModelChoices.PROTECT: APICallTypeChoices.PROTECT_EVALUATOR.value,
}


def _get_api_call_type(model: str, template=None, runtime_config=None):
    """The legacy APICallType a run is logged and rate-limited under.

    Pass the eval template (and the run's config) when the caller's model may
    be empty, so a saved Jev template is still logged as a Jev run.
    """
    from tfc.ee_gates import is_jev_model

    try:
        if template is not None:
            from model_hub.utils.jev_templates import binding_model

            if is_jev_model(
                binding_model(template, model=model, runtime_config=runtime_config)
            ):
                return APICallTypeChoices.JEV_EVALUATOR.value
        if is_jev_model(model):
            return APICallTypeChoices.JEV_EVALUATOR.value
        if not model:
            return APICallTypeChoices.TURING_LARGE_EVALUATOR.value

        model_key: ModelChoices | str = model
        if isinstance(model, str):
            try:
                model_key = ModelChoices(model)
            except ValueError:
                # Non-Turing/Protect models (e.g. "gpt-5", "gemini-...") are
                # legitimate user input but not part of ModelChoices. This is
                # expected, not an error: fall back to the default api call type.
                logger.warning("unknown_model_for_api_call_type", model=model)
                return APICallTypeChoices.TURING_LARGE_EVALUATOR.value

        return model_to_api_call_type.get(
            model_key, APICallTypeChoices.TURING_LARGE_EVALUATOR.value
        )
    except Exception as e:
        logger.exception(f"Error getting api call type: {e}")
        return APICallTypeChoices.TURING_LARGE_EVALUATOR.value


def _get_billing_event_type(api_call_type: str, *models, evaluator=None) -> str:
    """Event type for the billing UsageEvent of an eval run.

    Jev runs bill as jev_evaluator. A caller that logged the run before it
    knew the model ran Jev passes the legacy type it used; the models and the
    evaluator that actually ran decide.
    """
    from tfc.ee_gates import is_jev_model

    is_jev = any(is_jev_model(model) for model in models)
    if not is_jev and evaluator is not None:
        try:
            from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
        except ImportError:
            JevEvaluator = None
        is_jev = JevEvaluator is not None and isinstance(evaluator, JevEvaluator)
    if not is_jev:
        return api_call_type
    from ee.usage.schemas.event_types import BillingEventType

    return BillingEventType.JEV_EVALUATOR.value


def _is_jev_evaluator(evaluator) -> bool:
    """Whether a built evaluator runs Jev: what billing should go by, rather
    than a second copy of the engine's model rule."""
    try:
        from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator
    except ImportError:
        return False
    return isinstance(evaluator, JevEvaluator)


def _emit_jev_usage(evaluator, *, org_id: str, run_id: str) -> bool:
    """Bill a completed Jev run the one way every eval path bills Jev: the
    ee.jev.metering events at the flat per-run eval fee, with ids stable per
    run attempt. Emits nothing and returns False for any other evaluator, which
    the caller bills as before."""
    if not _is_jev_evaluator(evaluator):
        return False
    try:
        from ee.jev.metering import emit_usage_events
    except ImportError:
        return False
    emit_usage_events(evaluator, org_id=org_id, cell_or_run_id=run_id)
    return True
