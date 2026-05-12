"""Service layer for prompt operations — shared by views and ai_tools."""

import re
import uuid
from dataclasses import dataclass

import structlog
from django.db import transaction

logger = structlog.get_logger(__name__)


@dataclass
class ServiceError:
    message: str
    code: str = "ERROR"


def get_next_version_number(template_id, organization_id):
    """Get the next version number atomically to prevent race conditions.

    Uses database-level locking to ensure uniqueness.
    Gets the latest created version and increments its number.
    """
    from model_hub.models.run_prompt import PromptVersion

    with transaction.atomic():
        latest_version = (
            PromptVersion.objects.filter(
                original_template_id=template_id,
                original_template__organization_id=organization_id,
                deleted=False,
            )
            .order_by("-created_at")
            .first()
        )

        if latest_version:
            try:
                current_version_num = int(latest_version.template_version.lstrip("v"))
                return current_version_num + 1
            except (ValueError, AttributeError):
                return 1
        else:
            return 1


def _normalize_message_content(messages):
    """Normalize message content from plain strings to list-of-objects format.

    Converts {"role": "user", "content": "hello"} to
    {"role": "user", "content": [{"text": "hello", "type": "text"}]}.
    """
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = [{"text": content, "type": "text"}]
    return messages


def _ensure_model_detail(config):
    """Ensure modelDetail is present in configuration.

    When prompts are created via MCP, the frontend's modelDetail is not set.
    This generates it from the model name so the prompt_config_snapshot
    matches the format expected by the frontend and API serializers.
    """
    if not isinstance(config, dict) or "configuration" not in config:
        return
    configuration = config["configuration"]
    if "model" not in configuration or "modelDetail" in configuration:
        return

    model_name = configuration["model"]
    if not model_name:
        return

    try:
        from agentic_eval.core_evals.run_prompt.litellm_models import (
            LiteLLMModelManager,
        )
        from model_hub.models.choices import ProviderLogoUrls
        from model_hub.utils.utils import get_model_mode

        model_manager = LiteLLMModelManager(model_name=model_name, organization_id=None)
        provider = model_manager.get_provider(model_name=model_name)
        configuration["model_detail"] = {
            "logo_url": ProviderLogoUrls.get_url_by_provider(provider),
            "providers": provider,
            "model_name": model_name,
            "is_available": True,
            "type": get_model_mode(model_name),
        }
    except Exception as e:
        logger.warning(f"Could not generate model_detail for {model_name}: {e}")
        configuration["model_detail"] = {
            "logo_url": None,
            "providers": "unknown",
            "model_name": model_name,
            "is_available": False,
            "type": "chat",
        }


def _normalize_prompt_config(prompt_config):
    """Normalize all messages in a prompt config list to use structured content."""
    if not prompt_config or not isinstance(prompt_config, list):
        return prompt_config
    for cfg in prompt_config:
        messages = cfg.get("messages", [])
        _normalize_message_content(messages)
        _ensure_model_detail(cfg)
    return prompt_config


def _extract_variables(prompt_config):
    """Extract {{variable}} names from prompt config messages.

    When template_format is "jinja", delegates to the Jinja2 AST-based extractor
    so that loop-scoped / set-scoped variables are correctly excluded.
    """
    variables = set()
    if not prompt_config or not isinstance(prompt_config, list):
        return list(variables)

    # Determine template_format from the first config entry
    template_format = None
    if prompt_config:
        template_format = (
            prompt_config[0].get("configuration", {}).get("template_format")
        )

    use_jinja = template_format in ("jinja", "jinja2")

    for cfg in prompt_config:
        messages = cfg.get("messages", [])
        for msg in messages:
            content = msg.get("content", "")
            texts = []
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
            elif isinstance(content, str):
                texts.append(content)

            if use_jinja:
                from model_hub.utils.jinja_variables import extract_jinja_variables
            for text in texts:
                if use_jinja:
                    variables.update(extract_jinja_variables(text))
                else:
                    variables.update(re.findall(r"\{\{(\w+)\}\}", text))
    return sorted(variables)


def create_prompt_template(
    *,
    name,
    organization,
    workspace,
    user,
    prompt_config=None,
    description="",
    folder_id=None,
    model=None,
    variable_values=None,
):
    """Create a prompt template with an initial version.

    Returns:
        dict with template, version, template_id, version_id, version_number, variable_names, prompt_config
        or ServiceError
    """
    from model_hub.models.run_prompt import PromptTemplate, PromptVersion

    # Check duplicate name
    if PromptTemplate.objects.filter(
        name=name,
        organization=organization,
        deleted=False,
    ).exists():
        return ServiceError(
            f"A prompt template named '{name}' already exists.", "DUPLICATE_NAME"
        )

    # Build default prompt config if not provided
    if not prompt_config:
        default_model = model or "gpt-4o"
        prompt_config = [
            {
                "messages": [
                    {
                        "role": "system",
                        "content": [
                            {"text": "You are a helpful assistant.", "type": "text"}
                        ],
                    },
                    {
                        "role": "user",
                        "content": [{"text": "{{input}}", "type": "text"}],
                    },
                ],
                "placeholders": [],
                "configuration": {
                    "model": default_model,
                    "temperature": 0.7,
                    "max_tokens": 1000,
                    "top_p": 1,
                    "presence_penalty": 0,
                    "frequency_penalty": 0,
                    "response_format": "text",
                },
            }
        ]

    # Normalize message content to list-of-objects format
    _normalize_prompt_config(prompt_config)

    variable_names = _extract_variables(prompt_config)

    # Build variable_values: merge extracted variable names with provided values
    resolved_variable_values = {}
    for v in variable_names:
        if variable_values and v in variable_values:
            resolved_variable_values[v] = variable_values[v]
        else:
            resolved_variable_values[v] = []

    # Create template
    create_kwargs = {
        "name": name,
        "description": description,
        "organization": organization,
        "workspace": workspace,
        "created_by": user,
        "variable_names": variable_names,
        "placeholders": resolved_variable_values,
    }

    if folder_id:
        try:
            from model_hub.models.run_prompt import PromptFolder

            folder = PromptFolder.objects.get(
                id=folder_id, organization=organization, deleted=False
            )
            create_kwargs["prompt_folder"] = folder
        except Exception:
            pass  # Folder not found, skip

    template = PromptTemplate.objects.create(**create_kwargs)
    template.collaborators.add(user)

    # Create initial version (v1, draft, default)
    version = PromptVersion.objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot=prompt_config[0],
        is_default=True,
        is_draft=True,
        variable_names=resolved_variable_values,
        placeholders=resolved_variable_values,
    )

    return {
        "template": template,
        "version": version,
        "template_id": str(template.id),
        "version_id": str(version.id),
        "version_number": "v1",
        "variable_names": variable_names,
        "variable_values": resolved_variable_values,
        "prompt_config": prompt_config,
    }


def create_prompt_version(
    *,
    template_id,
    organization,
    prompt_config,
    commit_message=None,
    set_default=False,
    metadata=None,
    variable_values=None,
):
    """Create a new version of a prompt template.

    Returns:
        dict with template, version, template_id, version_id, version_number,
        is_default, is_draft, variable_names or ServiceError
    """
    from model_hub.models.run_prompt import PromptTemplate, PromptVersion

    try:
        template = PromptTemplate.objects.get(
            id=template_id,
            organization=organization,
            deleted=False,
        )
    except PromptTemplate.DoesNotExist:
        return ServiceError(f"Prompt template {template_id} not found.", "NOT_FOUND")

    next_num = get_next_version_number(template.id, organization.id)
    version_str = f"v{next_num}"

    # Normalize message content to list-of-objects format
    _normalize_prompt_config(prompt_config)

    variable_names = _extract_variables(prompt_config)

    # Build variable_values: merge extracted names with provided values
    # Fall back to parent template's placeholders for unspecified variables
    resolved_variable_values = {}
    template_placeholders = template.placeholders or {}
    for v in variable_names:
        if variable_values and v in variable_values:
            resolved_variable_values[v] = variable_values[v]
        elif v in template_placeholders:
            resolved_variable_values[v] = template_placeholders[v]
        else:
            resolved_variable_values[v] = []

    create_version_kwargs = {
        "original_template": template,
        "template_version": version_str,
        "prompt_config_snapshot": prompt_config[0],
        "is_default": set_default,
        "is_draft": not bool(commit_message),
        "commit_message": commit_message or "",
        "variable_names": resolved_variable_values,
        "placeholders": resolved_variable_values,
    }
    if metadata is not None:
        create_version_kwargs["metadata"] = metadata

    version = PromptVersion.objects.create(**create_version_kwargs)

    if set_default:
        PromptVersion.objects.filter(
            original_template=template,
            deleted=False,
        ).exclude(id=version.id).update(is_default=False)

    return {
        "template": template,
        "version": version,
        "template_id": str(template.id),
        "version_id": str(version.id),
        "version_number": version_str,
        "is_default": version.is_default,
        "is_draft": version.is_draft,
        "variable_names": variable_names,
    }
