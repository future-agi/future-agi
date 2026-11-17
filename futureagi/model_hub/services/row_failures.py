"""What a failed experiment row says to a person, chosen from the code it records.

A prompt or agent row's recorded error is {"type", "code", "message"}. The
code is chosen where the row is recorded, from the failure's kind (a prompt
row's exception type, an agent row's outcome) and never from its text, and the
message is that code's sentence. Readers that show a failure beyond the row
itself (the results grid's cell, the run's first error) show it only when the
row carries a code listed here. Anything else keeps their own fallback, so
text that no code chose reaches no new place.

A failed agent step records its code and sentence in the row's step
checkpoint; the step's own execution record, which the agent run view shows,
holds the sentence.
"""

from model_hub.utils.model_endpoints import (
    INVALID_MODEL_CONFIGURATION,
    ModelConfigurationRefused,
)

ROW_FAILED = "row_failed"
MODEL_NOT_IN_CATALOG = "model_not_in_catalog"
PROVIDER_KEY_MISSING = "provider_key_missing"
PROVIDER_FAILED = "provider_failed"
AGENT_FAILED = "agent_failed"
AGENT_CANCELLED = "agent_cancelled"
AGENT_STEP_FAILED = "agent_step_failed"
ROW_LIMITS_NEED_OWN_KEY = "row_limits_need_own_key"
ROW_LIMITS_UNSUPPORTED = "row_limits_unsupported"
# Any other refusal of Future AGI's managed access: its sentence is the
# refusal's own copy, which the ledger port writes and no provider does.
MANAGED_CALL_REFUSED = "managed_call_refused"
# A model configuration, or a saved key a call would be given, refused before
# any call (ModelConfigurationRefused): its sentence is the refusal's own.
MODEL_CONFIGURATION_REFUSED = "model_configuration_refused"
# An evaluator's failure is stored on its score and cells, not as a row error.
EVALUATION_FAILED = "evaluation_failed"

ROW_FAILED_MESSAGE = "Run failed on this row"
MODEL_NOT_IN_CATALOG_MESSAGE = "{model} isn't a model this workspace can run. Choose another model for this prompt."
PROVIDER_FAILED_MESSAGE = "The model provider returned an error."
AGENT_FAILED_MESSAGE = "The agent run failed on this row"
AGENT_CANCELLED_MESSAGE = "The agent run was cancelled"
AGENT_STEP_FAILED_MESSAGE = "The agent run failed at this step."
EVALUATION_FAILED_MESSAGE = "The evaluation failed on this row"
# Per-row limits on managed access: for a model whose provider takes a saved
# key, and for one whose provider takes none (it never asks for that key).
ROW_LIMITS_NEED_OWN_KEY_MESSAGE = (
    "Per-row limits need your own provider key for this model. "
    "Add your key and re-run, or create the experiment again without limits."
)
ROW_LIMITS_UNSUPPORTED_MESSAGE = (
    "Per-row limits can't be used with this model. "
    "Create the experiment again without limits."
)

# Codes whose sentence is fixed. The other codes' sentences name a model or a
# provider, or are a managed-access refusal's own copy, so the recorded message
# is the one their code chose.
_FIXED = {
    ROW_FAILED: ROW_FAILED_MESSAGE,
    PROVIDER_FAILED: PROVIDER_FAILED_MESSAGE,
    AGENT_FAILED: AGENT_FAILED_MESSAGE,
    AGENT_CANCELLED: AGENT_CANCELLED_MESSAGE,
    AGENT_STEP_FAILED: AGENT_STEP_FAILED_MESSAGE,
    EVALUATION_FAILED: EVALUATION_FAILED_MESSAGE,
    ROW_LIMITS_NEED_OWN_KEY: ROW_LIMITS_NEED_OWN_KEY_MESSAGE,
    ROW_LIMITS_UNSUPPORTED: ROW_LIMITS_UNSUPPORTED_MESSAGE,
    MODEL_CONFIGURATION_REFUSED: INVALID_MODEL_CONFIGURATION,
}
CODES = frozenset(_FIXED) | {
    MODEL_NOT_IN_CATALOG,
    PROVIDER_KEY_MISSING,
    MANAGED_CALL_REFUSED,
}

# A model's display name is free text: keep a shown sentence bounded.
MAX_MESSAGE = 2000


def model_not_in_catalog_message(model_name, **config_scope):
    """The sentence for a model the catalog does not have.

    Names the model by the display name its prompt configuration gives it
    (found by ``config_scope``, for that model id), else by the id.
    """
    from model_hub.models.experiments import ExperimentPromptConfig

    shown = None
    if config_scope:
        shown = (
            ExperimentPromptConfig.no_workspace_objects.filter(
                model=model_name, deleted=False, **config_scope
            )
            .values_list("model_display_name", flat=True)
            .first()
        )
    shown = shown.strip() if isinstance(shown, str) and shown.strip() else model_name
    return MODEL_NOT_IN_CATALOG_MESSAGE.format(model=shown)


def prompt_failure(exc, **config_scope):
    """The code and sentence of a prompt row whose call raised ``exc``, by its type."""
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        MissingProviderKeyError,
        ModelNotInCatalog,
        missing_provider_key_message,
    )
    from model_hub.services.credit_ledger_port import (
        ManagedCallRefusal,
        ManagedRowLimitsUnavailableError,
        ManagedRowLimitsUnsupportedModelError,
    )
    from model_hub.services.model_route import ModelRouteUnavailable

    if isinstance(exc, ModelNotInCatalog):
        return MODEL_NOT_IN_CATALOG, model_not_in_catalog_message(
            exc.model_name, **config_scope
        )
    if isinstance(exc, (ModelRouteUnavailable, MissingProviderKeyError)):
        return PROVIDER_KEY_MISSING, missing_provider_key_message(exc.provider)
    if isinstance(exc, ManagedRowLimitsUnsupportedModelError):
        return ROW_LIMITS_UNSUPPORTED, ROW_LIMITS_UNSUPPORTED_MESSAGE
    if isinstance(exc, ManagedRowLimitsUnavailableError):
        return ROW_LIMITS_NEED_OWN_KEY, ROW_LIMITS_NEED_OWN_KEY_MESSAGE
    if isinstance(exc, ManagedCallRefusal):
        return MANAGED_CALL_REFUSED, exc.message
    if isinstance(exc, ModelConfigurationRefused):
        return MODEL_CONFIGURATION_REFUSED, INVALID_MODEL_CONFIGURATION
    return ROW_FAILED, ROW_FAILED_MESSAGE


def coded_error(error_type, code):
    """A row error for a fixed code: its sentence is the code's own."""
    return {"type": error_type, "code": code, "message": _FIXED[code]}


def coded_message(error):
    """The sentence a recorded error's code chose, bounded; None without a known code."""
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if code in _FIXED:
        return _FIXED[code]
    if code not in CODES:
        return None
    message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    return message[:MAX_MESSAGE]
