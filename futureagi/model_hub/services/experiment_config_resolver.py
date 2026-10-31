"""
Bridge between new ExperimentPromptConfig/ExperimentAgentConfig models
and the existing execution engine.

resolve_prompt_config() produces a dict that the execution engine can
consume directly — one model per config, no inner loop needed.
"""


def get_snapshot_template_format(snapshot):
    """Return configuration.template_format from a prompt_config_snapshot."""
    if isinstance(snapshot, list):
        snapshot = snapshot[0] if snapshot else None
    if not isinstance(snapshot, dict):
        return None
    configuration = snapshot.get("configuration")
    if not isinstance(configuration, dict):
        return None
    return configuration.get("template_format") or None


def _get_saved_template_format(prompt_version):
    """Return configuration.template_format from a prompt version snapshot."""
    if prompt_version is None:
        return None
    return get_snapshot_template_format(prompt_version.prompt_config_snapshot)


def resolve_template_format(exp_prompt_config):
    """
    Template format to render an ExperimentPromptConfig's messages with.

    The experiment's own configuration.template_format wins. Otherwise LLM
    messages, which are locked from the prompt version, use the format that
    version was saved with. Older experiment configs default to Mustache.
    """
    configured = (exp_prompt_config.configuration or {}).get("template_format")
    return (
        configured
        or _get_saved_template_format(exp_prompt_config.prompt_version)
        or "mustache"
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
    configuration = dict(exp_prompt_config.configuration or {})
    template_format = resolve_template_format(exp_prompt_config)
    if template_format:
        configuration["template_format"] = template_format

    config = {
        "name": exp_prompt_config.name,
        "messages": exp_prompt_config.get_messages(),
        "model": exp_prompt_config.model,
        "model_display_name": exp_prompt_config.model_display_name,
        "model_config": exp_prompt_config.model_config,
        "model_params": exp_prompt_config.model_params,
        "configuration": configuration,
        "output_format": exp_prompt_config.output_format,
    }

    # STT-specific
    if exp_prompt_config.voice_input_column_id:
        config["voice_input_column"] = str(exp_prompt_config.voice_input_column_id)

    return config


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
