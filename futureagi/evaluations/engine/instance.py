"""
Evaluator instance creation.

Extracted from EvaluationRunner._create_eval_instance(), _prepare_eval_config(),
_prepare_futureagi_config(), _resolve_version(), _apply_version_overrides().

These functions take explicit parameters instead of reading from `self`.
"""

import structlog

from evaluations.constants import FUTUREAGI_EVAL_TYPES

logger = structlog.get_logger(__name__)


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
    Resolve the eval template version to use.

    Returns the EvalTemplateVersion instance or None.

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

        return resolved

    except Exception:
        logger.debug("version_resolution_skipped")
        return None


def effective_template_config(eval_template, resolved_version=None):
    """Template config with the pinned version's snapshot merged over it.

    Merge, not replace: a version created before a config key existed must
    fall back to the live template rather than resolve to None.
    """
    base = dict(getattr(eval_template, "config", None) or {})
    if resolved_version is None:
        return base
    snapshot = getattr(resolved_version, "config_snapshot", None)
    if isinstance(snapshot, dict):
        base.update(snapshot)
    return base


def pinned_attr(eval_template, resolved_version, name):
    """Column-level field, preferring the pinned version.

    Order: the version's own column -> the version's config_snapshot ->
    the live template. Empty string and None both count as "not carried by
    this version" so pre-snapshot rows fall through to the template.
    """
    if resolved_version is not None:
        value = getattr(resolved_version, name, None)
        if value is not None and value != "":
            return value
        snapshot = getattr(resolved_version, "config_snapshot", None)
        if isinstance(snapshot, dict):
            value = snapshot.get(name)
            if value is not None and value != "":
                return value
    return getattr(eval_template, name, None)


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


def resolve_pass_threshold(
    eval_template,
    runtime_config: dict | None = None,
    resolved_version=None,
) -> float:
    """Priority: runtime_config[run_config][pass_threshold] > runtime_config[pass_threshold] > resolved_version.pass_threshold > eval_template.pass_threshold > 0.5."""
    if isinstance(runtime_config, dict):
        run_config = runtime_config.get("run_config") or {}
        if isinstance(run_config, dict) and run_config.get("pass_threshold") is not None:
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


def _prepare_futureagi_config(
    config,
    eval_template,
    model="turing_large",
    kb_id=None,
    template_config=None,
    resolved_version=None,
):
    """
    Prepare configuration for FutureAGI (DeterministicEvaluator, RankingEvaluator).

    Extracted from EvaluationRunner._prepare_futureagi_config (eval_runner.py:1883).
    """
    from model_hub.models.choices import OwnerChoices

    tpl_cfg = (
        template_config
        if template_config is not None
        else effective_template_config(eval_template, resolved_version)
    )

    config["api_key"] = None
    if kb_id:
        config["knowledge_base_id"] = str(kb_id)

    if eval_template.owner == OwnerChoices.USER.value:
        pinned_model = pinned_attr(eval_template, resolved_version, "model")
        model = pinned_model if pinned_model else model

    llm_model, provider = _get_futureagi_model_config(model=model)
    config["model"] = llm_model
    config["provider"] = provider

    eval_type_id = tpl_cfg.get("eval_type_id", "")

    if eval_type_id == "DeterministicEvaluator":
        if "rule_prompt" not in config:
            config["choices"] = pinned_attr(
                eval_template, resolved_version, "choices"
            )
            config["rule_prompt"] = pinned_attr(
                eval_template, resolved_version, "criteria"
            )
            config["multi_choice"] = pinned_attr(
                eval_template, resolved_version, "multi_choice"
            )
            config["custom_eval"] = tpl_cfg.get("custom_eval", False)

        config["model_type"] = model

        if "param_modalities" in tpl_cfg:
            config["param_modalities"] = tpl_cfg["param_modalities"]
        if "required_keys" in tpl_cfg:
            config["required_keys"] = tpl_cfg["required_keys"]

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
    template_config=None,
    resolved_version=None,
):
    """
    Prepare evaluation configuration based on eval type.

    Returns (config, criteria) where criteria may be extracted from config.

    Extracted from EvaluationRunner._prepare_eval_config (eval_runner.py:1760).
    """
    from model_hub.models.choices import ModelChoices

    tpl_cfg = (
        template_config
        if template_config is not None
        else effective_template_config(eval_template, resolved_version)
    )
    pinned_choice_scores = pinned_attr(
        eval_template, resolved_version, "choice_scores"
    )
    pinned_choices = pinned_attr(eval_template, resolved_version, "choices")

    eval_type_id = tpl_cfg.get("eval_type_id", "")

    # CustomCodeEval — only needs the code string
    if eval_type_id == "CustomCodeEval":
        config = {
            "code": tpl_cfg.get("code") or config.get("code", ""),
        }
        return config, criteria_override

    # AgentEvaluator — multi-turn reasoning via Falcon AI AgentLoop
    if eval_type_id == "AgentEvaluator":
        config["rule_prompt"] = config["rule_prompt"] if "rule_prompt" in config else tpl_cfg.get(
            "rule_prompt"
        )
        config["model"] = model or tpl_cfg.get("model")
        raw_output = tpl_cfg.get("output")
        if pinned_choice_scores and raw_output != "Pass/Fail":
            config["output_type"] = "choices"
        else:
            config["output_type"] = raw_output
        config["choices"] = pinned_choices or (
            list(pinned_choice_scores.keys())
            if pinned_choice_scores
            else []
        )
        config["choice_scores"] = pinned_choice_scores
        _pinned_threshold = pinned_attr(
            eval_template, resolved_version, "pass_threshold"
        )
        config["pass_threshold"] = (
            _pinned_threshold if _pinned_threshold is not None else 0.5
        )
        config["reverse_output"] = bool(
            tpl_cfg.get("reverse_output", False)
        )
        config["check_internet"] = tpl_cfg.get("check_internet", False)
        config["knowledge_base_id"] = tpl_cfg.get("knowledge_base_id")
        config["agent_mode"] = tpl_cfg.get("agent_mode", "agent")
        config["tools"] = tpl_cfg.get("tools", {})
        config["knowledge_bases"] = tpl_cfg.get("knowledge_bases", [])
        config["data_injection"] = tpl_cfg.get("data_injection", {})
        config["summary"] = tpl_cfg.get("summary", {"type": "concise"})
        config["multi_choice"] = bool(
            pinned_attr(eval_template, resolved_version, "multi_choice")
        )
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
        config["provider"] = tpl_cfg.get("provider")
        config["rule_prompt"] = config["rule_prompt"] if "rule_prompt" in config else tpl_cfg.get(
            "rule_prompt"
        )
        config["system_prompt"] = config["system_prompt"] if "system_prompt" in config else tpl_cfg.get(
            "system_prompt"
        )
        raw_output = tpl_cfg.get("output")
        if pinned_choice_scores and raw_output != "Pass/Fail":
            config["output_type"] = "choices"
        else:
            config["output_type"] = raw_output
        # Multi-message and few-shot support
        if tpl_cfg.get("messages"):
            config["messages"] = tpl_cfg.get("messages")
        if tpl_cfg.get("few_shot_examples"):
            from model_hub.utils.few_shot_examples import (
                expand_static_few_shot_examples,
            )

            config["few_shot_examples"] = expand_static_few_shot_examples(
                tpl_cfg.get("few_shot_examples"),
                organization=eval_template.organization,
            )

        # Resolve model
        raw_model = model or tpl_cfg.get("model")
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
            if not config.get("provider"):
                from model_hub.utils.llm_providers import get_provider_for_model

                config["provider"] = get_provider_for_model(raw_model, org_id, ws_id)

        config["check_internet"] = tpl_cfg.get("check_internet", False)
        config["multi_choice"] = tpl_cfg.get("multi_choice")
        config["choices"] = pinned_choices or (
            list(pinned_choice_scores.keys())
            if pinned_choice_scores
            else []
        )
        config["choice_scores"] = pinned_choice_scores
        config["multi_choice"] = bool(
            pinned_attr(eval_template, resolved_version, "multi_choice")
        )

    # FutureAGI evals (DeterministicEvaluator, RankingEvaluator)
    if is_futureagi:
        config, extracted_criteria = _prepare_futureagi_config(
            config,
            eval_template,
            model=model,
            kb_id=kb_id,
            template_config=tpl_cfg,
            resolved_version=resolved_version,
        )
        if extracted_criteria:
            criteria_override = extracted_criteria

    return config, criteria_override


_VERSION_AUDIT_FIELDS = (
    "rule_prompt",
    "system_prompt",
    "model",
    "pass_threshold",
    "output_type",
    "choices",
    "choice_scores",
    "multi_choice",
    "check_internet",
    "required_keys",
    "few_shot_examples",
    "messages",
    "summary",
    "agent_mode",
    "data_injection",
    "tools",
    "knowledge_bases",
)

_VERSION_AUDIT_ALIASES = {"output_type": "output"}


def _audit_value(value, limit=120):
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        return "<unreprable>"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def log_version_application(
    eval_template, resolved_version, version_number, config, runtime_config=None
):
    """Record where each evaluator kwarg came from, just before instantiation.

    Classifies every audited field as version / run_config / template /
    derived so a pinned run can be checked without re-reading the code.
    """
    try:
        live = getattr(eval_template, "config", None) or {}
        snapshot = (
            getattr(resolved_version, "config_snapshot", None) or {}
            if resolved_version is not None
            else {}
        )
        run_config = (runtime_config or {}).get("run_config") or {}
        running = config if isinstance(config, dict) else {}

        origins = {}
        for field in _VERSION_AUDIT_FIELDS:
            if field not in running:
                continue
            value = running[field]
            snap_key = _VERSION_AUDIT_ALIASES.get(field, field)
            if field in run_config and value == run_config[field]:
                origin = "run_config"
            elif snap_key in snapshot and value == snapshot[snap_key]:
                origin = "version"
            elif resolved_version is not None and value == getattr(
                resolved_version, field, object()
            ):
                origin = "version"
            elif snap_key in live and value == live[snap_key]:
                origin = "template"
            else:
                origin = "derived"
            origins[field] = origin

        logger.debug(
            "eval_version_applied",
            eval_template_id=str(getattr(eval_template, "id", "")),
            eval_type_id=live.get("eval_type_id", ""),
            requested_version_number=version_number,
            resolved_version_id=(
                str(resolved_version.id) if resolved_version else None
            ),
            resolved_version_number=(
                resolved_version.version_number if resolved_version else None
            ),
            snapshot_keys=len(snapshot),
            prompt_messages_count=len(
                getattr(resolved_version, "prompt_messages", None) or []
            ),
            origins=origins,
            from_version=sorted(k for k, v in origins.items() if v == "version"),
            from_run_config=sorted(
                k for k, v in origins.items() if v == "run_config"
            ),
            from_template=sorted(k for k, v in origins.items() if v == "template"),
            values={k: _audit_value(running.get(k)) for k in origins},
        )
    except Exception:
        logger.debug("eval_version_logging_failed", exc_info=True)


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
    template_config = effective_template_config(eval_template, resolved_version)

    # Prepare config based on eval type
    config, criteria = prepare_eval_config(
        eval_template=eval_template,
        config=config,
        model=model,
        organization_id=organization_id,
        workspace_id=workspace_id,
        kb_id=kb_id,
        is_futureagi=is_futureagi,
        template_config=template_config,
        resolved_version=resolved_version,
    )

    # Apply version overrides
    config, criteria = apply_version_overrides(config, resolved_version, criteria)

    from agentic_eval.core_evals.fi_evals.eval_type import is_function_eval

    if not template_config.get("function_eval") and not is_function_eval(
        template_config.get("eval_type_id", "")
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
    eval_type_id_for_overrides = template_config.get("eval_type_id", "")
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
    eval_type_id_check = template_config.get("eval_type_id", "")
    if (
        has_function_params_schema(template_config)
        and eval_type_id_check != "CustomCodeEval"
    ):
        normalized = normalize_eval_runtime_config(template_config, runtime_config)
        config = normalized.get("params", {})
    if (
        template_config.get("function_eval")
        and not has_function_params_schema(template_config)
        and eval_type_id_check != "CustomCodeEval"
    ):
        config = template_config.get("config")

    # Add knowledge base if provided
    if kb_id and config:
        config["knowledge_base_id"] = str(kb_id) if kb_id else None

    # Remove org/workspace for evaluators that don't accept them
    eval_type_id = template_config.get("eval_type_id", "")
    if config and eval_type_id != "AgentEvaluator":
        config.pop("organization_id", None)
        config.pop("workspace_id", None)
        config.pop("user_id", None)

    # Pass template_format only to evaluators that support it (via **kwargs)
    if config and eval_type_id in ("AgentEvaluator", "CustomPromptEvaluator"):
        if "template_format" not in config:
            config["template_format"] = template_config.get(
                "template_format", "mustache"
            )
    elif config:
        config.pop("template_format", None)

    log_version_application(
        eval_template, resolved_version, version_number, config, runtime_config
    )

    # Instantiate
    if not config and not is_futureagi:
        return eval_class(), criteria

    return eval_class(**config), criteria
