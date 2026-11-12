"""
Bridge between new ExperimentPromptConfig/ExperimentAgentConfig models
and the existing execution engine.

resolve_prompt_config() produces a dict that the execution engine can
consume directly — one model per config, no inner loop needed.
"""

from copy import copy

PARAM_ALIASES = {
    "maxTokens": "max_tokens",
    "topP": "top_p",
    "presencePenalty": "presence_penalty",
    "frequencyPenalty": "frequency_penalty",
    "responseFormat": "response_format",
    "toolChoice": "tool_choice",
}
VERSION_PARAMS = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "response_format",
        "reasoning",
    }
)


def normalize_prompt_params(params):
    """Null means unset, not an override. Canonical keys win alias collisions."""
    params = params or {}
    normalized = {
        PARAM_ALIASES.get(key, key): value
        for key, value in params.items()
        if value is not None
    }
    normalized.update(
        {
            key: value
            for key, value in params.items()
            if key not in PARAM_ALIASES and value is not None
        }
    )
    return normalized


def _version_configuration(epc):
    """Check ownership before reading a snapshot, including worker/rerun paths.

    Mirror the request's default-workspace rule without depending on request
    thread-local state in a Temporal worker. A default workspace can also use
    unassigned prompts and other default workspaces of the same organization.
    """
    if not epc.prompt_version_id:
        return {}
    version = epc.prompt_version
    template = version.original_template
    dataset = epc.experiment_dataset.experiment.dataset
    if (
        not dataset.organization_id
        or str(template.organization_id) != str(dataset.organization_id)
        or str(version.original_template_id) != str(epc.prompt_template_id)
        or template.deleted
    ):
        raise ValueError("Prompt version is not accessible to this experiment")
    workspace = dataset.workspace
    same_workspace = template.workspace_id == dataset.workspace_id
    default_scope = (
        workspace
        and workspace.is_default
        and (
            template.workspace_id is None
            or (
                template.workspace.is_default
                and template.workspace.organization_id == dataset.organization_id
            )
        )
    )
    if workspace and not (same_workspace or default_scope):
        raise ValueError("Prompt version is not accessible to this experiment")
    snapshot = version.prompt_config_snapshot or {}
    if isinstance(snapshot, list):
        snapshot = snapshot[0] if snapshot else {}
    configuration = (
        snapshot.get("configuration", {}) if isinstance(snapshot, dict) else {}
    )
    return normalize_prompt_params(
        configuration if isinstance(configuration, dict) else {}
    )


def resolve_prompt_config(exp_prompt_config):
    """
    Convert an ExperimentPromptConfig record into the dict shape expected
    by the existing experiment execution engine.

    Works for ALL experiment types (llm, tts, stt, image).
    - LLM: messages come LOCKED from prompt_version.prompt_config_snapshot
    - TTS/STT/Image: messages stored inline on exp_prompt_config.messages

    Args:
        exp_prompt_config: ExperimentPromptConfig instance.

    Returns:
        dict with snake_case keys matching the backend execution engine.
    """
    saved = _version_configuration(exp_prompt_config)
    configuration = normalize_prompt_params(exp_prompt_config.configuration)
    model_config = normalize_prompt_params(exp_prompt_config.model_config)
    effective = {key: value for key, value in saved.items() if key in VERSION_PARAMS}
    # Legacy clients can put sampling settings in configuration or model.config.
    # Explicit per-model request overrides have final precedence in both paths
    # passed to RunPrompt (its direct arguments otherwise shadow run_prompt_config).
    for source in (model_config, configuration):
        effective.update(
            {key: value for key, value in source.items() if key in VERSION_PARAMS}
        )
    effective.update(normalize_prompt_params(exp_prompt_config.model_params))
    configuration.update(
        {key: value for key, value in effective.items() if key in VERSION_PARAMS}
    )

    # Legacy forms send {tools: [], toolChoice: 'auto'} even when untouched.
    # Preserve the version's tool selection in that case; 'none' is an explicit
    # opt-out, and an actual request tool list takes precedence over the version.
    if not configuration.get("tools") and configuration.get("tool_choice") != "none":
        if saved.get("tools"):
            configuration["tools"] = saved["tools"]
            if configuration.get("tool_choice") in (None, "", "auto"):
                configuration["tool_choice"] = saved.get("tool_choice", "auto")
    elif configuration.get("tools") and not configuration.get("tool_choice"):
        configuration["tool_choice"] = saved.get("tool_choice", "auto")

    config = {
        "name": exp_prompt_config.name,
        "messages": exp_prompt_config.get_messages(),
        "model": exp_prompt_config.model,
        "model_display_name": exp_prompt_config.model_display_name,
        "model_config": model_config,
        "model_params": effective,
        "configuration": configuration,
        "output_format": exp_prompt_config.output_format,
    }

    # STT-specific
    if exp_prompt_config.voice_input_column_id:
        config["voice_input_column"] = str(exp_prompt_config.voice_input_column_id)

    return config


def prompt_settings_changed(epc, model_config, model_params, configuration):
    """Compare what would run, not the request shape or JSON numeric spelling.

    The worker persists effective settings for audit. An unchanged editor can
    echo those settings, or send the original request with inherited values
    omitted. Resolve both through the same scoped version before comparing.
    This is called only after template/version/model identity has been checked.
    """
    candidate = copy(epc)
    candidate.model_config = model_config
    candidate.model_params = model_params
    candidate.configuration = configuration

    def settings(record):
        resolved = resolve_prompt_config(record)
        config = dict(resolved["configuration"])
        # An empty tool list with auto selection is the legacy form's default,
        # equivalent to omission if the version also has no tools to inherit.
        if not config.get("tools") and config.get("tool_choice") in (None, "", "auto"):
            config.pop("tools", None)
            config.pop("tool_choice", None)
        return resolved["model_config"], resolved["model_params"], config

    # Python's numeric comparison keeps 0 == 0.0 after DRF FloatField validation,
    # unlike json.dumps, without rounding away real parameter changes.
    return settings(epc) != settings(candidate)


def resolve_agent_config(exp_agent_config):
    """
    Convert an ExperimentAgentConfig record into a dict for agent execution.

    No configuration_snapshot needed — GraphVersion is immutable by design.
    The workflow passes graph_version_id directly to ExecuteGraphInput.

    Args:
        exp_agent_config: ExperimentAgentConfig instance.

    Returns:
        dict with keys: name, graph_id, graph_version_id, config_type.
    """
    return {
        "name": exp_agent_config.name,
        "graph_id": str(exp_agent_config.graph_id),
        "graph_version_id": str(exp_agent_config.graph_version_id),
        "config_type": "agent",
    }
