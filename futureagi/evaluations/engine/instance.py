"""
Evaluator instance creation.

Extracted from EvaluationRunner._create_eval_instance(), _prepare_eval_config(),
_prepare_futureagi_config(), _resolve_version(), _apply_version_overrides().

These functions take explicit parameters instead of reading from `self`.
"""

import structlog

from evaluations.constants import FUTUREAGI_EVAL_TYPES

logger = structlog.get_logger(__name__)


def resolve_version(eval_template, version_number=None, organization=None):
    """
    Resolve the eval template version to use.

    Returns the EvalTemplateVersion instance or None.
    Increments usage_count on the resolved version.

    Extracted from EvaluationRunner._resolve_version (eval_runner.py:1630).
    """
    try:
        from django.db import models

        from model_hub.models.evals_metric import EvalTemplateVersion

        if organization is None:
            organization = getattr(eval_template, "organization", None)

        if version_number is not None:
            resolved = (
                EvalTemplateVersion.all_objects.filter(
                    eval_template=eval_template,
                    version_number=version_number,
                    deleted=False,
                )
                .filter(
                    models.Q(organization__isnull=True)
                    | models.Q(organization=organization)
                )
                .first()
            )
        else:
            resolved = EvalTemplateVersion.objects.get_default(eval_template)

        if resolved:
            EvalTemplateVersion.all_objects.filter(id=resolved.id).update(
                usage_count=models.F("usage_count") + 1
            )

        return resolved

    except Exception:
        logger.debug("version_resolution_skipped")
        return None


def apply_version_overrides(config, resolved_version, criteria=None):
    """
    Apply prompt overrides from the resolved version to the config.

    Returns (config, criteria) where criteria may be updated from the version.

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

    if resolved_version.model:
        config["model"] = resolved_version.model

    return config, criteria


def _get_api_key(model, organization_id, workspace_id=None):
    """
    Get API key for the model via LiteLLMModelManager.

    Extracted from EvaluationRunner._get_api_key (eval_runner.py:1869).
    """
    from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

    model_manager = LiteLLMModelManager(model, exclude_providers="custom")
    api_key = model_manager.get_api_key(
        organization_id=organization_id, workspace_id=workspace_id
    )

    if not api_key:
        raise ValueError(f"No API key found for organization {organization_id}")

    return api_key


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
):
    """
    Prepare evaluation configuration based on eval type.

    Returns (config, criteria) where criteria may be extracted from config.

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
        config["rule_prompt"] = eval_template.config.get("rule_prompt")
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
        config["provider"] = eval_template.config.get("provider")
        config["rule_prompt"] = eval_template.config.get("rule_prompt")
        config["system_prompt"] = eval_template.config.get("system_prompt")
        raw_output = eval_template.config.get("output")
        if eval_template.choice_scores and raw_output != "Pass/Fail":
            config["output_type"] = "choices"
        else:
            config["output_type"] = raw_output
        # Multi-message and few-shot support
        if eval_template.config.get("messages"):
            config["messages"] = eval_template.config.get("messages")
        if eval_template.config.get("few_shot_examples"):
            config["few_shot_examples"] = eval_template.config.get("few_shot_examples")

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
            config["api_key"] = _get_api_key(raw_model, org_id, ws_id)

        config["check_internet"] = eval_template.config.get("check_internet", False)
        config["multi_choice"] = eval_template.config.get("multi_choice")
        config["choices"] = eval_template.choices or (
            list(eval_template.choice_scores.keys())
            if eval_template.choice_scores
            else []
        )
        config["choice_scores"] = eval_template.choice_scores

    # FutureAGI evals (DeterministicEvaluator, RankingEvaluator)
    if is_futureagi:
        config, extracted_criteria = _prepare_futureagi_config(
            config, eval_template, model=model, kb_id=kb_id
        )
        if extracted_criteria:
            criteria_override = extracted_criteria

    return config, criteria_override


def create_eval_instance(
    eval_class,
    eval_template,
    config=None,
    model="turing_large",
    kb_id=None,
    runtime_config=None,
    organization_id=None,
    workspace_id=None,
    version_number=None,
    is_futureagi=False,
):
    """
    Create an evaluator instance ready to call .run().

    This is the single entry point for evaluator instantiation. It:
    1. Resolves the template version
    2. Prepares the eval config based on eval_type_id
    3. Applies version overrides
    4. Handles function eval params normalization
    5. Instantiates the evaluator class

    Extracted from EvaluationRunner._create_eval_instance (eval_runner.py:1711).

    Returns: (eval_instance, criteria) where criteria may be set by version or config.
    """
    from model_hub.utils.function_eval_params import (
        has_function_params_schema,
        normalize_eval_runtime_config,
    )

    if config is None:
        config = {}

    # Resolve version
    org = None
    if organization_id:
        from accounts.models.organization import Organization

        org = Organization.objects.filter(id=organization_id).first()
    elif eval_template.organization:
        org = eval_template.organization

    resolved_version = resolve_version(eval_template, version_number, org)

    # Prepare config based on eval type
    config, criteria = prepare_eval_config(
        eval_template=eval_template,
        config=config,
        model=model,
        organization_id=organization_id,
        workspace_id=workspace_id,
        kb_id=kb_id,
        is_futureagi=is_futureagi,
    )

    # Apply version overrides
    config, criteria = apply_version_overrides(config, resolved_version, criteria)

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
    # Per-evaluator allow-list of keys that runtime overrides may touch. Only
    # keys that the evaluator's __init__ actually consumes belong here —
    # passing unknown kwargs raises TypeError at instantiation.
    _RUNTIME_ALLOWED_KEYS = {
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
            # error_localizer is handled at the surface-runner layer.
        },
        # CustomPromptEvaluator (LLM-as-judge) supports the per-binding
        # toggles the FE EvalPicker exposes, minus agent-only features
        # (agent_mode, tools, summary, data_injection — none of which the
        # single-LLM-call evaluator consumes).
        #
        # Note: `model` is intentionally NOT in this allow-list. The model
        # kwarg has its own resolution path (caller passes `model=` to
        # create_eval_instance, which prepare_eval_config uses to derive
        # `api_key` / `provider` for non-Turing models). Letting run_config
        # overwrite `config["model"]` after that derivation would leave a
        # stale api_key in config — to override model on an LLM binding,
        # update UserEvalMetric.model / CustomEvalConfig.model directly so
        # the kwarg path picks it up.
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
            # error_localizer is handled at the surface-runner layer.
        },
    }
    _allowed = _RUNTIME_ALLOWED_KEYS.get(eval_type_id_for_overrides)
    if _allowed and runtime_config:
        _overrides = (runtime_config or {}).get("run_config") or {}
        for key, value in _overrides.items():
            if key in _allowed and value is not None:
                config[key] = value

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

    # Instantiate
    if not config and not is_futureagi:
        return eval_class(), criteria

    return eval_class(**config), criteria
