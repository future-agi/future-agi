"""
Evaluator instance creation.

Extracted from EvaluationRunner._create_eval_instance(), _prepare_eval_config(),
_prepare_futureagi_config(), _resolve_version(), _apply_version_overrides().

These functions take explicit parameters instead of reading from `self`.
"""

import structlog

from evaluations.constants import SERVING_EMBEDDING_EVAL_TYPES

logger = structlog.get_logger(__name__)
_DEFAULT_MODEL = object()

#: ``create_eval_instance(managed_judge_billing=...)``: the caller settles a
#: managed judge's provider cost only through the credit meter and bills
#: no second amount for it (EvaluationRunner without the legacy combined
#: billing). Any other caller gets the visible cut refusal for a managed judge
#: (each of them bills the evaluation's LLM cost again).
MANAGED_JUDGE_SINGLE_METER = "dsx_single_meter"

#: Refusal for a pinned version whose model needs a key or provider the
#: organization does not have. Raised before the evaluator is built.
PINNED_VERSION_MODEL_KEY_MESSAGE = (
    "This evaluation's saved version uses a different model than its key "
    "allows. Pick a key for that model or unpin the version."
)


# Per-evaluator allow-list for per-binding ``run_config`` overrides. Caps
# the runtime-tunable surface — both evaluators accept ``**kwargs`` so
# unknown keys would silently pass through rather than raise. ``model`` is
# excluded from CustomPromptEvaluator: ``prepare_eval_config`` derives
# api_key / provider from the resolved model upstream (see line ~301-320),
# and overwriting ``config["model"]`` after that derivation would leave
# stale auth on the config dict. ``error_localizer_enabled`` is handled at
# the surface-runner layer (Temporal activities, inline-eval polling,
# dataset runner), not on the evaluator instance — kept out of both
# allow-lists.
_RUNTIME_ALLOWED_KEYS = {
    "JevEvaluator": {
        "pass_threshold",
        "reverse_output",
        "choice_scores",
        "output_type",
        "choices",
    },
    "AgentEvaluator": {
        "model",
        "agent_mode",
        "check_internet",
        "knowledge_base_id",
        "knowledge_bases",
        "tools",
        "data_injection",
        "summary",
        "pass_threshold",
        "output_type",
        "choices",
        "choice_scores",
        "reverse_output",
        "multi_choice",
    },
    "CustomPromptEvaluator": {
        "check_internet",
        "multi_choice",
        "pass_threshold",
        "output_type",
        "choices",
        "choice_scores",
        "reverse_output",
        "knowledge_base_id",
        "knowledge_bases",
    },
}


def resolve_version(eval_template, version_number=None, organization=None):
    """
    The live version ``version_number`` names, or None.

    Only an explicit number resolves: callers pass one for a version the
    binding pins. With no number the run uses the live template, so the
    template's default version is not looked up here.

    Extracted from EvaluationRunner._resolve_version (eval_runner.py:1630).
    """
    if version_number is None:
        return None
    from django.db import models

    from model_hub.models.evals_metric import EvalTemplateVersion

    if organization is None:
        organization = getattr(eval_template, "organization", None)

    # A lookup error is not swallowed: a pinned run must not quietly fall
    # back to the live template.
    return (
        EvalTemplateVersion.all_objects.filter(
            eval_template=eval_template,
            version_number=version_number,
            deleted=False,
        )
        .filter(
            models.Q(organization__isnull=True) | models.Q(organization=organization)
        )
        .first()
    )


def _pinned_model_choice(
    eval_template, version_model, *, model, model_override, keep_model, binding_model
):
    """(pinned_model, crosses_model) for a run whose version names a model.

    pinned_model is the version's model when it is the model that runs: not
    for a playground selection, a Jev selection or a caller that keeps its
    own model (keep_model). crosses_model says it replaces another model, or
    differs from the live template's model (whose stored provider belongs to
    that model), so provider and key must be found for it.
    """
    template_model = (eval_template.config or {}).get("model")
    supplied_model = None if model is _DEFAULT_MODEL else model
    effective_model = (
        model_override
        or binding_model
        or supplied_model
        or version_model
        or template_model
    )
    jev_selection = any(
        isinstance(value, str) and value.lower().startswith("jev-")
        for value in (template_model, version_model, effective_model)
    )
    if not version_model or jev_selection or model_override or keep_model:
        return None, False
    replaced = "turing_large" if model is _DEFAULT_MODEL else (model or template_model)
    crosses = version_model != replaced or version_model != template_model
    return version_model, crosses


def _judge_scope(eval_template, organization_id=None, workspace_id=None):
    """The organization and workspace a judge's key is looked up for."""
    org_id = organization_id or (
        str(eval_template.organization.id)
        if getattr(eval_template, "organization", None)
        else None
    )
    ws_id = workspace_id or (
        str(eval_template.workspace.id)
        if getattr(eval_template, "workspace", None)
        else None
    )
    return org_id, ws_id


def _require_pinned_model_key(model, organization_id, workspace_id):
    """Refuse a pinned model the organization has no key or route for."""
    from model_hub.models.choices import ModelChoices

    keyless = {
        ModelChoices.TURING_LARGE.value,
        ModelChoices.TURING_SMALL.value,
        ModelChoices.TURING_FLASH.value,
    }
    if not model or model in keyless:
        return
    try:
        _get_api_key(model, organization_id, workspace_id, allow_managed=True)
    except ValueError:
        raise ValueError(PINNED_VERSION_MODEL_KEY_MESSAGE) from None


def pinned_run_choice(
    eval_template,
    *,
    version_number,
    model=_DEFAULT_MODEL,
    runtime_config=None,
    organization_id=None,
    keep_model=False,
):
    """(pinned_model, crosses_model) as create_eval_instance will choose them.

    pinned_model is the pinned version's model when that is the model the
    evaluator runs, else None (no version number, no model on the version,
    a Jev selection or a caller that keeps its own model). Runners use it to
    limit-check, debit and record the model that actually runs, and to refuse
    a pinned model with no key before anything is charged.
    """
    if version_number is None or eval_template is None:
        return None, False
    if organization_id:
        from accounts.models.organization import Organization

        org = Organization.objects.filter(id=organization_id).first()
    else:
        org = getattr(eval_template, "organization", None)
    resolved = resolve_version(eval_template, version_number, org)
    binding = runtime_config or {}
    binding_model = (binding.get("run_config") or {}).get("model") or binding.get(
        "model"
    )
    return _pinned_model_choice(
        eval_template,
        getattr(resolved, "model", None),
        model=model,
        model_override=None,
        keep_model=keep_model,
        binding_model=binding_model,
    )


def charged_model_for_metric(metric):
    """The model a run of ``metric`` is limit-checked and charged under.

    The model that actually runs: the metric's live pin's model when the
    engine runs it (the same choice as pinned_run_choice, which the runner
    makes), else the metric's own model exactly as before. For callers that
    check usage limits before a runner exists.
    """
    card = getattr(metric, "model", None)
    if getattr(metric, "pinned_version_id", None) is None:
        return card
    pinned = metric.pinned_version
    if pinned is None or getattr(pinned, "deleted", False):
        return card
    organization_id = getattr(metric, "organization_id", None)
    pinned_model, _ = pinned_run_choice(
        metric.template,
        version_number=pinned.version_number,
        model=card,
        runtime_config=metric.config,
        organization_id=str(organization_id) if organization_id else None,
    )
    return pinned_model or card


def refuse_pinned_model_without_key(
    eval_template,
    *,
    version_number,
    model=_DEFAULT_MODEL,
    runtime_config=None,
    organization_id=None,
    workspace_id=None,
    keep_model=False,
    choice=None,
):
    """Refuse, before anything is charged, a pinned model with no key.

    The same model choice create_eval_instance makes (``choice`` when the
    caller already has it from pinned_run_choice). Runners call this before
    their up-front debit, so a refused evaluator leaves no charge and no
    refund. The lookup is the one an unpinned evaluator naming that model
    gets (_get_api_key with allow_managed): a stored key, else a managed
    route the gateway admits, else the refusal. Only the LLM-as-judge
    evaluator looks a key up before it runs, pinned or not, so only it is
    checked here; an agent evaluator finds its own key when it runs, pinned
    or not, and Turing models need none.
    """
    if version_number is None or eval_template is None:
        return
    eval_type_id = (eval_template.config or {}).get("eval_type_id", "")
    if eval_type_id != "CustomPromptEvaluator":
        return
    pinned_model, crosses_model = choice or pinned_run_choice(
        eval_template,
        version_number=version_number,
        model=model,
        runtime_config=runtime_config,
        organization_id=organization_id,
        keep_model=keep_model,
    )
    if crosses_model:
        _require_pinned_model_key(
            pinned_model, *_judge_scope(eval_template, organization_id, workspace_id)
        )


def apply_version_overrides(
    config, resolved_version, criteria=None, saved_choices=False, preserve_model=False
):
    """
    Apply prompt overrides from the resolved version to the config.

    Returns (config, criteria) where criteria may be updated from the version.

    The version's saved choice definition (choices, choice_scores,
    multi_choice and output type) replaces the live one only with
    ``saved_choices``, which callers set for a version the binding pins. A
    default version resolved for an unpinned run keeps the template's live
    choices, which the template editor changes in place.

    Extracted from EvaluationRunner._apply_version_overrides (eval_runner.py:1678).
    """
    if not resolved_version:
        return config, criteria

    from model_hub.utils.prompt_migration import prompt_messages_to_flat_config

    flat = prompt_messages_to_flat_config(resolved_version.prompt_messages or [])

    if flat.get("system_prompt") is not None:
        config["system_prompt"] = flat["system_prompt"]
    if flat.get("rule_prompt") is not None:
        config["rule_prompt"] = flat["rule_prompt"]
    if flat.get("criteria") is not None and criteria is None:
        criteria_text = flat["criteria"]
        required_keys = config.get("required_keys", [])
        for i, key in enumerate(required_keys):
            criteria_text = criteria_text.replace(
                f"{{{{{key}}}}}", f"{{{{variable_{i + 1}}}}}"
            )
        criteria = criteria_text

    if resolved_version.model and not preserve_model:
        config["model"] = resolved_version.model

    from evaluations.engine.version_choices import version_choice_config

    if saved_choices and "output_type" in config:
        config.update(version_choice_config(None, resolved_version))
    return config, criteria


def resolve_pass_threshold(
    eval_template,
    runtime_config: dict | None = None,
    resolved_version=None,
) -> float:
    """Priority: runtime_config[run_config][pass_threshold] > runtime_config[pass_threshold] > resolved_version.pass_threshold > eval_template.pass_threshold > 0.5."""
    if isinstance(runtime_config, dict):
        run_config = runtime_config.get("run_config") or {}
        if (
            isinstance(run_config, dict)
            and run_config.get("pass_threshold") is not None
        ):
            return float(run_config["pass_threshold"])
        if runtime_config.get("pass_threshold") is not None:
            return float(runtime_config["pass_threshold"])

    if resolved_version is not None:
        version_threshold = getattr(resolved_version, "pass_threshold", None)
        if version_threshold is not None:
            return float(version_threshold)

    template_threshold = getattr(eval_template, "pass_threshold", None)
    if template_threshold is not None:
        return float(template_threshold)

    return 0.5


def resolve_binding_model(
    runtime_config: dict | None,
    eval_template,
) -> str | None:
    """Priority: runtime_config[run_config][model] > runtime_config[model] > eval_template.config[model]."""
    if isinstance(runtime_config, dict):
        run_config = runtime_config.get("run_config") or {}
        if isinstance(run_config, dict):
            nested = run_config.get("model")
            if nested:
                return nested
        top_level = runtime_config.get("model")
        if top_level:
            return top_level

    template_config = getattr(eval_template, "config", None) or {}
    return template_config.get("model")


SUMMARY_TEMPLATE_UNAVAILABLE_MESSAGE = (
    "Summary template is unavailable. Choose another summary."
)


class SummaryTemplateUnavailable(ValueError):
    """A playground run names a saved summary template it cannot use."""


def resolve_summary_config(summary, organization_id, *, strict=False):
    """The summary the agent evaluator runs for the editor's ``custom:<id>``.

    The evaluator takes inline custom criteria, not a saved-template id. With
    ``strict`` (the eval playground request) the id resolves to the saved
    template's criteria in the caller's organization; a malformed, missing,
    deleted, other-organization or empty template raises
    SummaryTemplateUnavailable before anything is billed. Every other run logs
    a warning and keeps the concise summary it has always produced. Presets and
    inline summaries are returned unchanged.
    """
    summary_type = summary.get("type") if isinstance(summary, dict) else None
    if not isinstance(summary_type, str) or not summary_type.startswith("custom:"):
        return summary

    template_id = summary_type.removeprefix("custom:")
    if not strict:
        logger.warning(
            "summary_template_not_applied",
            summary_template_id=template_id,
            fallback="concise",
        )
        return {"type": "concise"}

    from uuid import UUID

    try:
        UUID(template_id)
    except ValueError:
        raise SummaryTemplateUnavailable(SUMMARY_TEMPLATE_UNAVAILABLE_MESSAGE) from None

    template = None
    if organization_id:
        from model_hub.models.eval_summary_template import EvalSummaryTemplate

        template = EvalSummaryTemplate.objects.filter(
            id=template_id, organization_id=organization_id
        ).first()
    if template is None or not (template.criteria or "").strip():
        raise SummaryTemplateUnavailable(SUMMARY_TEMPLATE_UNAVAILABLE_MESSAGE)
    return {"type": "custom", "custom": template.criteria}


def choice_score_override(runtime_config: dict | None) -> dict | None:
    """The binding's per-evaluation choice score edit, or None.

    Priority: runtime_config[run_config][choice_scores] > runtime_config[choice_scores]
    (observe and simulate bindings store the edit at the top level). Only a
    non-empty dict is an override.
    """
    if not isinstance(runtime_config, dict):
        return None
    run_config = runtime_config.get("run_config")
    if isinstance(run_config, dict):
        nested = run_config.get("choice_scores")
        if isinstance(nested, dict) and nested:
            return nested
    top_level = runtime_config.get("choice_scores")
    if isinstance(top_level, dict) and top_level:
        return top_level
    return None


_CHOICE_SCORES_UNSET = object()


def resolve_choice_scores(
    eval_template,
    runtime_config: dict | None = None,
    *,
    base_scores=_CHOICE_SCORES_UNSET,
):
    """Overlay named binding scores on the selected live or saved choice map.

    Omitting ``base_scores`` preserves the two-argument live-template API.
    An explicitly supplied None or empty saved map must not fall back to live
    scores. Without an override the selected map is returned unchanged.
    """
    if base_scores is _CHOICE_SCORES_UNSET:
        base_scores = getattr(eval_template, "choice_scores", None)
    override = choice_score_override(runtime_config)
    if override is None:
        return base_scores
    base = base_scores if isinstance(base_scores, dict) else {}
    return {**base, **override}


def _get_api_key(model, organization_id, workspace_id=None, *, allow_managed=False):
    """
    Get API key for the model via LiteLLMModelManager.

    Extracted from EvaluationRunner._get_api_key (eval_runner.py:1869).
    With ``allow_managed`` a key-less organization whose judge model the
    gateway admits gets a ``ManagedRouteBinding`` (one key; nothing is
    admitted) instead of the refusal.
    """
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
    from model_hub.services.model_route import OP_CUSTOM_PROMPT_EVAL

    model_manager = LiteLLMModelManager(model, exclude_providers="custom")
    api_key = model_manager.get_api_key(
        organization_id=organization_id,
        workspace_id=workspace_id,
        allow_managed=allow_managed,
        operation=OP_CUSTOM_PROMPT_EVAL,
    )

    if not api_key:
        raise ValueError(f"No API key found for organization {organization_id}")

    return api_key


def _require_single_meter_judge(config, managed_judge_billing):
    """One key: a managed judge runs only for a caller that bills its
    evaluation once. Refused before the evaluator exists, so nothing is held,
    admitted or sent; a stored provider key (BYOK) is never refused here."""
    from model_hub.services.model_route import is_managed_route_binding

    if not isinstance(config, dict) or not is_managed_route_binding(
        config.get("model_route")
    ):
        return
    if managed_judge_billing == MANAGED_JUDGE_SINGLE_METER:
        return
    from model_hub.services.credit_ledger_port import (
        MANAGED_JUDGE_CUT_MESSAGE,
        ManagedDispatchUnavailableError,
    )

    logger.info(
        "managed_judge_cut",
        model=config.get("model"),
        reason="caller_bills_the_evaluation_again",
    )
    raise ManagedDispatchUnavailableError(MANAGED_JUDGE_CUT_MESSAGE)


def _rebind_judge_route(config):
    """One key: the managed judge binding for ``config["model"]``, the
    model the evaluator will run. ``prepare_eval_config`` bound it for the
    template's model; when a version override changed the model, the
    credential is resolved again for the final model exactly as at
    runtime (a stored key for it wins, else a binding for it, else the
    usual refusal). Only a binding made here reaches the evaluator, and a
    binding is not an admission, so nothing is spent here.
    """
    from model_hub.services.model_route import is_managed_route_binding

    binding = config.get("model_route")
    if not is_managed_route_binding(binding):
        config.pop("model_route", None)
        return config
    model = config.get("model")
    if binding.model_name == model:
        return config
    credential = _get_api_key(
        model, binding.organization_id, binding.workspace_id, allow_managed=True
    )
    if is_managed_route_binding(credential):
        config["api_key"] = None
        config["model_route"] = credential
    else:
        config["api_key"] = credential
        config.pop("model_route", None)
    return config


def _get_futureagi_model_config(model="turing_large"):
    """
    Get model and provider configuration for FutureAGI evals.

    Returns (model_name, provider) tuple.

    Extracted from EvaluationRunner._get_futureagi_model_config (eval_runner.py:2397).
    """
    from agentic_eval.core.utils.model_config import ModelConfigs
    from model_hub.models.choices import ModelChoices

    futureagi_model_configs = {
        ModelChoices.TURING_LARGE.value: ModelConfigs.TURING_LARGE,
        ModelChoices.TURING_SMALL.value: ModelConfigs.TURING_SMALL,
        ModelChoices.TURING_FLASH.value: ModelConfigs.TURING_FLASH,
        ModelChoices.PROTECT.value: ModelConfigs.PROTECT,
        ModelChoices.PROTECT_FLASH.value: ModelConfigs.PROTECT_FLASH,
    }

    cfg = futureagi_model_configs.get(model, ModelConfigs.TURING_LARGE)
    return cfg.model_name, cfg.provider


def _prepare_futureagi_config(config, eval_template, model="turing_large", kb_id=None):
    """
    Prepare configuration for FutureAGI (DeterministicEvaluator, RankingEvaluator).

    Extracted from EvaluationRunner._prepare_futureagi_config (eval_runner.py:1883).
    """
    from model_hub.models.choices import OwnerChoices

    config["api_key"] = None
    if kb_id:
        config["knowledge_base_id"] = str(kb_id)

    if eval_template.owner == OwnerChoices.USER.value:
        model = eval_template.model if eval_template.model else model

    llm_model, provider = _get_futureagi_model_config(model=model)
    config["model"] = llm_model
    config["provider"] = provider

    eval_type_id = eval_template.config.get("eval_type_id", "")

    if eval_type_id == "DeterministicEvaluator":
        if "rule_prompt" not in config:
            config["choices"] = eval_template.choices
            config["rule_prompt"] = eval_template.criteria
            config["multi_choice"] = eval_template.multi_choice
            config["custom_eval"] = eval_template.config.get("custom_eval", False)

        config["model_type"] = model

        if "param_modalities" in eval_template.config:
            config["param_modalities"] = eval_template.config["param_modalities"]
        if "required_keys" in eval_template.config:
            config["required_keys"] = eval_template.config["required_keys"]

    criteria = None
    if config.get("criteria"):
        criteria = config.pop("criteria")

    return config, criteria


def prepare_eval_config(
    eval_template,
    config,
    model="turing_large",
    organization_id=None,
    workspace_id=None,
    kb_id=None,
    is_futureagi=False,
    criteria_override=None,
    model_override=None,
    missing_key_message=None,
):
    """
    Prepare evaluation configuration based on eval type.

    Returns (config, criteria) where criteria may be extracted from config.

    ``missing_key_message`` replaces the error when no key can be found for
    ``model``; create_eval_instance sets it for a pinned version's model, so
    that evaluator is refused before anything is sent.

    Extracted from EvaluationRunner._prepare_eval_config (eval_runner.py:1760).
    """
    from model_hub.models.choices import ModelChoices

    eval_type_id = eval_template.config.get("eval_type_id", "")

    # CustomCodeEval — only needs the code string
    if eval_type_id == "CustomCodeEval":
        config = {
            "code": eval_template.config.get("code") or config.get("code", ""),
        }
        return config, criteria_override

    # AgentEvaluator — multi-turn reasoning via Falcon AI AgentLoop
    if eval_type_id == "AgentEvaluator":
        config["rule_prompt"] = (
            config["rule_prompt"]
            if "rule_prompt" in config
            else eval_template.config.get("rule_prompt")
        )
        config["model"] = model or eval_template.config.get("model")
        raw_output = eval_template.config.get("output")
        if eval_template.choice_scores and raw_output != "Pass/Fail":
            config["output_type"] = "choices"
        else:
            config["output_type"] = raw_output
        config["choices"] = eval_template.choices or (
            list(eval_template.choice_scores.keys())
            if eval_template.choice_scores
            else []
        )
        config["choice_scores"] = eval_template.choice_scores
        config["pass_threshold"] = (
            eval_template.pass_threshold
            if eval_template.pass_threshold is not None
            else 0.5
        )
        config["reverse_output"] = bool(
            eval_template.config.get("reverse_output", False)
        )
        config["check_internet"] = eval_template.config.get("check_internet", False)
        config["knowledge_base_id"] = eval_template.config.get("knowledge_base_id")
        config["agent_mode"] = eval_template.config.get("agent_mode", "agent")
        config["tools"] = eval_template.config.get("tools", {})
        config["knowledge_bases"] = eval_template.config.get("knowledge_bases", [])
        config["data_injection"] = eval_template.config.get("data_injection", {})
        config["summary"] = eval_template.config.get("summary", {"type": "concise"})
        config["multi_choice"] = bool(getattr(eval_template, "multi_choice", False))
        # Pass org/workspace context for tool resolution
        config["organization_id"] = (
            str(eval_template.organization.id) if eval_template.organization else None
        )
        config["workspace_id"] = (
            str(eval_template.workspace.id)
            if getattr(eval_template, "workspace", None)
            else None
        )

    # CustomPromptEvaluator — LLM-as-judge
    elif eval_type_id == "CustomPromptEvaluator":
        # A playground selection can cross providers; derive auth for that model.
        config["provider"] = (
            None if model_override else eval_template.config.get("provider")
        )
        config["rule_prompt"] = (
            config["rule_prompt"]
            if "rule_prompt" in config
            else eval_template.config.get("rule_prompt")
        )
        config["system_prompt"] = (
            config["system_prompt"]
            if "system_prompt" in config
            else eval_template.config.get("system_prompt")
        )
        raw_output = eval_template.config.get("output")
        if eval_template.choice_scores and raw_output != "Pass/Fail":
            config["output_type"] = "choices"
        else:
            config["output_type"] = raw_output
        # Multi-message and few-shot support
        if eval_template.config.get("messages"):
            config["messages"] = eval_template.config.get("messages")
        if eval_template.config.get("few_shot_examples"):
            from model_hub.utils.few_shot_examples import (
                expand_static_few_shot_examples,
            )

            config["few_shot_examples"] = expand_static_few_shot_examples(
                eval_template.config.get("few_shot_examples"),
                organization=eval_template.organization,
            )

        # Resolve model
        raw_model = model or eval_template.config.get("model")
        futureagi_models = {
            ModelChoices.TURING_LARGE.value,
            ModelChoices.TURING_SMALL.value,
            ModelChoices.TURING_FLASH.value,
        }
        config["model"] = raw_model
        if raw_model in futureagi_models:
            config["api_key"] = None
            config["provider"] = "turing"
        else:
            org_id = organization_id or (
                str(eval_template.organization.id)
                if eval_template.organization
                else None
            )
            ws_id = workspace_id or (
                str(eval_template.workspace.id)
                if getattr(eval_template, "workspace", None)
                else None
            )
            from model_hub.services.model_route import is_managed_route_binding

            try:
                judge_credential = _get_api_key(
                    raw_model, org_id, ws_id, allow_managed=True
                )
            except ValueError:
                if missing_key_message is None:
                    raise
                raise ValueError(missing_key_message) from None
            if is_managed_route_binding(judge_credential):
                # One key: no provider key. The binding admits nothing;
                # create_eval_instance binds it again for the final model
                # (after version overrides), and the judge's LLM admits
                # each call it makes.
                config["api_key"] = None
                config["model_route"] = judge_credential
            else:
                config["api_key"] = judge_credential
                config.pop("model_route", None)
            if not config.get("provider"):
                from model_hub.utils.llm_providers import get_provider_for_model

                config["provider"] = get_provider_for_model(raw_model, org_id, ws_id)

        config["check_internet"] = eval_template.config.get("check_internet", False)
        config["multi_choice"] = eval_template.config.get("multi_choice")
        config["choices"] = eval_template.choices or (
            list(eval_template.choice_scores.keys())
            if eval_template.choice_scores
            else []
        )
        config["choice_scores"] = eval_template.choice_scores
        config["multi_choice"] = bool(getattr(eval_template, "multi_choice", False))

    # FutureAGI evals (DeterministicEvaluator, RankingEvaluator)
    if is_futureagi:
        config, extracted_criteria = _prepare_futureagi_config(
            config, eval_template, model=model, kb_id=kb_id
        )
        if extracted_criteria:
            criteria_override = extracted_criteria

    return config, criteria_override


def _get_typesafe_api_key(organization_id, workspace_id=None):
    """The org's TypeSafe key from Settings -> AI Providers. Jev is bring your
    own key only, so a missing key fails like any other provider's."""
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
    from model_hub.models.choices import LiteLlmModelProvider

    return LiteLLMModelManager("jev").get_provider_api_key(
        organization_id, LiteLlmModelProvider.TYPESAFE.value, workspace_id
    )


def prepare_jev_config(
    eval_template,
    config,
    effective_model,
    resolved_version,
    runtime_config,
    api_key=None,
):
    """Build an effective typed config. The org's TypeSafe key is added only
    after the mapping validates, so no frame that can raise JevMappingError
    holds it; callers that can, hand it to JevEvaluator directly."""
    from copy import deepcopy

    from ee.jev.mapping import validate_jev_mapping

    effective = deepcopy(eval_template.config or {})
    if resolved_version:
        effective.update(deepcopy(resolved_version.config_snapshot or {}))
    effective.update(config)
    effective, _ = apply_version_overrides(effective, resolved_version)
    effective.pop("api_key", None)
    effective["model"] = effective_model
    effective["provider"] = "typesafe"
    effective["output_type"] = effective.get(
        "output_type", effective.get("output", "Pass/Fail")
    )
    effective["choice_scores"] = (
        getattr(resolved_version, "choice_scores", None)
        if resolved_version
        else eval_template.choice_scores
    )
    effective["choices"] = (
        effective.get("choices")
        or list(effective["choice_scores"] or {})
        or eval_template.choices
    )
    effective["multi_choice"] = bool(
        effective.get("multi_choice") or getattr(eval_template, "multi_choice", False)
    )
    effective["pass_threshold"] = resolve_pass_threshold(
        eval_template, runtime_config, resolved_version
    )
    for key, value in ((runtime_config or {}).get("run_config") or {}).items():
        if key in _RUNTIME_ALLOWED_KEYS["JevEvaluator"] and value is not None:
            effective[key] = value
    mapping = validate_jev_mapping(
        eval_template,
        output=effective["output_type"],
        choice_scores=effective["choice_scores"],
        multi_choice=effective["multi_choice"],
        config=effective,
    )
    effective["jev_mapping"] = mapping.to_dict()
    for key in ("organization_id", "workspace_id", "user_id"):
        effective.pop(key, None)
    if api_key is not None:
        effective["api_key"] = api_key
    return effective


def reject_jev_without_code():
    """Refuse a Jev run in a build without Jev's code (ee/ stripped) with a
    clear error, instead of failing on the import."""
    from tfc.ee_gates import JEV_UNAVAILABLE_MESSAGE, jev_available

    if jev_available():
        return
    from tfc.capabilities.errors import CapabilityDenied
    from tfc.licensing.types import DenialReason

    raise CapabilityDenied(
        feature_id="jev_models",
        reason_code=DenialReason.EE_CODE_UNAVAILABLE.value,
        detail=JEV_UNAVAILABLE_MESSAGE,
    )


def reject_unknown_jev_model(model):
    """Raise JEV_MODEL_UNKNOWN for a jev-* ID that is not a supported Jev model."""
    from tfc.ee_gates import is_jev_model

    if (
        isinstance(model, str)
        and model.lower().startswith("jev-")
        and not is_jev_model(model)
    ):
        reject_jev_without_code()
        from ee.jev.mapping import JevMappingError

        raise JevMappingError(
            "JEV_MODEL_UNKNOWN",
            "model",
            f"Model {model!r} is not a supported Jev evaluator model. Supported: "
            "jev-1.13.0, jev-latest.",
        )


def create_eval_instance(
    eval_class,
    eval_template,
    config=None,
    model=_DEFAULT_MODEL,
    kb_id=None,
    runtime_config=None,
    organization_id=None,
    workspace_id=None,
    version_number=None,
    is_futureagi=False,
    saved_choices=False,
    managed_judge_billing=None,
    preview_current_definition=False,
    model_override=None,
    strict_summary=False,
    keep_model=False,
):
    """
    Create an evaluator instance ready to call .run().

    This is the single entry point for evaluator instantiation. It:
    1. Resolves the template version
    2. Prepares the eval config based on eval_type_id
    3. Applies version overrides
    4. Handles function eval params normalization
    5. Instantiates the evaluator class

    ``saved_choices`` runs the resolved version's saved choice definition; only
    a binding that pins that version sets it (EvaluationRunner). Every other
    run, including Observe evaluations, keeps the template's live choices.

    ``preview_current_definition`` lets standalone Jev playground runs test the
    live template saved by the editor, not the default version's definition.
    Explicit version pins and saved-choice bindings still use their version.

    ``version_number`` names a version the binding pins; only then does a
    version apply. The pinned version's model runs unless ``keep_model`` is
    set (a composite child or jury seat whose link already chose its model).
    Provider and key are prepared for the model that runs; when the
    organization has none for a pinned version's model, the evaluator is
    refused with ``PINNED_VERSION_MODEL_KEY_MESSAGE`` before it is built.

    ``strict_summary`` (the eval playground request only) runs an agent
    evaluator's saved summary template and refuses one it cannot use; every
    other caller falls back to the concise summary (resolve_summary_config).

    ``managed_judge_billing``: ``MANAGED_JUDGE_SINGLE_METER`` from a caller
    whose only debit for a managed judge is the settle (EvaluationRunner
    without the legacy combined billing). For every other caller a judge that
    would run on Future AGI credits is refused with a visible line before the
    evaluator is built; BYOK judges are unchanged.

    Extracted from EvaluationRunner._create_eval_instance (eval_runner.py:1711).

    Returns: (eval_instance, criteria) where criteria may be set by version or config.
    """
    from model_hub.utils.function_eval_params import (
        has_function_params_schema,
        normalize_eval_runtime_config,
    )

    if config is None:
        config = {}

    # Only an unpinned playground selection overrides the default-version model.
    # Resolve it before provider/key preparation, never by mutating a saved version.
    if version_number is not None:
        model_override = None
    if model_override:
        model = model_override

    # Resolve version
    org = None
    if organization_id:
        from accounts.models.organization import Organization

        org = Organization.objects.filter(id=organization_id).first()
    elif eval_template.organization:
        org = eval_template.organization

    resolved_version = resolve_version(eval_template, version_number, org)

    from tfc.ee_gates import is_jev_model

    template_model = (eval_template.config or {}).get("model")
    version_model = getattr(resolved_version, "model", None)
    binding = runtime_config or {}
    binding_model = (binding.get("run_config") or {}).get("model") or binding.get(
        "model"
    )
    supplied_model = None if model is _DEFAULT_MODEL else model
    effective_model = (
        model_override
        or binding_model
        or supplied_model
        or version_model
        or template_model
    )
    # An unsupported jev-* ID fails closed before any chat, Turing or BYOK logic:
    # the direct engine path (SDK and other callers) never reaches the view-level
    # validate_jev_binding guard, so the rejection has to live here (R-06).
    reject_unknown_jev_model(effective_model)
    if is_jev_model(effective_model):
        from tfc.capabilities import service as capability_service

        reject_jev_without_code()
        capability_service.check_or_raise(
            "jev_models", org_id=str(org.id) if org is not None else organization_id
        )
        from ee.evals.llm.jev_evaluator.evaluator import JevEvaluator

        if kb_id:
            config = {**config, "knowledge_base_id": str(kb_id)}
        org_id = organization_id or (
            str(eval_template.organization.id) if eval_template.organization else None
        )
        ws_id = workspace_id or (
            str(eval_template.workspace.id)
            if getattr(eval_template, "workspace", None)
            else None
        )
        definition_version = resolved_version
        if preview_current_definition and version_number is None and not saved_choices:
            # Omit the snapshot entirely: removed mapping/rubric fields must
            # not return, and prompt overrides and scores must stay live too.
            definition_version = None
        # Validated first; the key is looked up only then and goes straight
        # to the evaluator.
        jev_config = prepare_jev_config(
            eval_template,
            config,
            effective_model,
            definition_version,
            runtime_config,
        )
        return (
            JevEvaluator(**jev_config, api_key=_get_typesafe_api_key(org_id, ws_id)),
            None,
        )

    jev_selection = any(
        isinstance(value, str) and value.lower().startswith("jev-")
        for value in (template_model, version_model, effective_model)
    )
    # A pinned version's model is the model that runs, unless a playground
    # selection or the caller (keep_model) chose one.
    pinned_model, crosses_model = _pinned_model_choice(
        eval_template,
        version_model,
        model=model,
        model_override=model_override,
        keep_model=keep_model,
        binding_model=binding_model,
    )
    if jev_selection:
        model = effective_model
    elif pinned_model:
        model = pinned_model
    elif model is _DEFAULT_MODEL:
        model = "turing_large"

    # Prepare config based on eval type. When the pinned version's model
    # replaces another one, its provider and key are found for that model
    # (not taken from the live template), or the evaluator is refused.
    config, criteria = prepare_eval_config(
        eval_template=eval_template,
        config=config,
        model=model,
        organization_id=organization_id,
        workspace_id=workspace_id,
        kb_id=kb_id,
        is_futureagi=is_futureagi,
        model_override=pinned_model if crosses_model else model_override,
        missing_key_message=(
            PINNED_VERSION_MODEL_KEY_MESSAGE if crosses_model else None
        ),
    )

    # Apply version overrides. The model was chosen above, before provider
    # and key were prepared, so the version does not change it here.
    config, criteria = apply_version_overrides(
        config,
        resolved_version,
        criteria,
        saved_choices=saved_choices,
        preserve_model=True,
    )
    if jev_selection:
        config["model"] = effective_model

    from agentic_eval.core_evals.fi_evals.eval_type import is_function_eval

    _template_config = eval_template.config or {}
    if not _template_config.get("function_eval") and not is_function_eval(
        _template_config.get("eval_type_id", "")
    ):
        config["pass_threshold"] = resolve_pass_threshold(
            eval_template, runtime_config, resolved_version
        )

    # Runtime override merge.
    #
    # Priority (lowest to highest): template default → UserEvalMetric.run_config
    #                              → API-level runtime_config.run_config
    #
    # The caller passes the merged binding config (UserEvalMetric.config /
    # CustomEvalConfig.config / SimulateEvalConfig.config) as `runtime_config`,
    # so any `run_config` sub-dict it contains holds the user's per-attachment
    # toggles from the EvalPicker (model, agent_mode, check_internet, tools,
    # knowledge_bases, data_injection, summary, pass_threshold, choice_scores,
    # multi_choice, reverse_output, error_localizer_enabled).
    #
    # We use an explicit `is not None` check rather than truthy `or` so that
    # explicit False / 0 / "" overrides survive (e.g. check_internet=False on
    # a binding when the template default is True).
    eval_type_id_for_overrides = eval_template.config.get("eval_type_id", "")
    _allowed = _RUNTIME_ALLOWED_KEYS.get(eval_type_id_for_overrides)
    if _allowed and isinstance(runtime_config, dict):
        _overrides = runtime_config.get("run_config")
        if isinstance(_overrides, dict):
            for key, value in _overrides.items():
                # A playground selection or an explicitly pinned version's
                # model is the model that runs; the binding's does not
                # replace it.
                if key == "model" and (
                    model_override or (pinned_model and version_number is not None)
                ):
                    continue
                # The shared resolver overlays scores on the selected base below;
                # replacing the base here would lose its unspecified labels.
                if key in _allowed and key != "choice_scores" and value is not None:
                    config[key] = value
    # Version selection happened first. Pass/fail and stored scores now apply
    # the same partial overlay, including an explicitly empty/None saved map.
    if _allowed and "choice_scores" in _allowed and "choice_scores" in config:
        config["choice_scores"] = resolve_choice_scores(
            eval_template, runtime_config, base_scores=config["choice_scores"]
        )

    # After the binding's own summary override, before anything is billed.
    if eval_type_id_for_overrides == "AgentEvaluator" and "summary" in config:
        config["summary"] = resolve_summary_config(
            config["summary"], organization_id, strict=strict_summary
        )

    # Handle function eval params normalization — but NOT CustomCodeEval
    # which already got its config ({"code": ...}) from prepare_eval_config.
    # Overwriting it with function_params_schema defaults would lose the code
    # and pass unexpected kwargs to the constructor.
    eval_type_id_check = eval_template.config.get("eval_type_id", "")
    if (
        has_function_params_schema(eval_template.config)
        and eval_type_id_check != "CustomCodeEval"
    ):
        normalized = normalize_eval_runtime_config(eval_template.config, runtime_config)
        config = normalized.get("params", {})
    if (
        eval_template.config.get("function_eval")
        and not has_function_params_schema(eval_template.config)
        and eval_type_id_check != "CustomCodeEval"
    ):
        config = eval_template.config.get("config")

    # Add knowledge base if provided
    if kb_id and config:
        config["knowledge_base_id"] = str(kb_id) if kb_id else None

    # Remove org/workspace for evaluators that don't accept them
    eval_type_id = eval_template.config.get("eval_type_id", "")
    if config and eval_type_id != "AgentEvaluator":
        config.pop("organization_id", None)
        config.pop("workspace_id", None)
        config.pop("user_id", None)

    # Pass template_format only to evaluators that support it (via **kwargs)
    if config and eval_type_id in ("AgentEvaluator", "CustomPromptEvaluator"):
        if "template_format" not in config:
            config["template_format"] = eval_template.config.get(
                "template_format", "mustache"
            )
    elif config:
        config.pop("template_format", None)

    # Their params arrive as run kwargs and their constructors take none, so
    # nothing a binding stores here (the legacy configure form keeps
    # similarity_threshold and match_all in its "config") may reach them.
    # A binding pinned to a system version seeded before SYSTEM_EVALS_VERSION
    # 29 resolves CustomCodeEval from that version's snapshot: its code scored
    # the same but cannot reach serving, so the backend function runs instead.
    if eval_type_id in SERVING_EMBEDDING_EVAL_TYPES:
        if getattr(eval_template, "owner", None) == "system":
            from evaluations.engine.registry import get_eval_class

            eval_class = get_eval_class(eval_type_id)
        return eval_class(), criteria

    # Code that calls model serving (a user's copy of a built-in embedding
    # eval) can never reach it from the code sandbox. Every surface builds its
    # evaluator here before it preprocesses the inputs, so the refusal comes
    # before the name-keyed serving preflight: a copy that kept the built-in's
    # name would otherwise report serving as down. custom_code_eval refuses
    # the same code too, for callers that build CustomCodeEval elsewhere.
    if eval_type_id == "CustomCodeEval" and isinstance(config, dict):
        from agentic_eval.core_evals.fi_evals.function.internal_service_code import (
            refuse_internal_service_code,
        )

        refuse_internal_service_code(config.get("code"))

        from agentic_eval.core_evals.fi_utils.custom_code_pause import (
            refuse_paused_template_code,
        )

        # A paused deployment refuses code that is not this built-in template's
        # own here, before the run is charged or any input is prepared.
        refuse_paused_template_code(eval_template, config.get("code"))

    # One key: a managed judge is bound to the model it will run (the
    # version overrides above may have changed config["model"]). Nothing
    # is admitted here.
    if isinstance(config, dict) and config.get("model_route") is not None:
        config = _rebind_judge_route(config)
        _require_single_meter_judge(config, managed_judge_billing)

    # Instantiate
    if not config and not is_futureagi:
        return eval_class(), criteria

    return eval_class(**config), criteria
