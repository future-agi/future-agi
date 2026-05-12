import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.agents._utils import resolve_agent
from ai_tools.validators import (
    get_valid_language_codes,
    validate_contact_number,
    validate_languages,
    validate_provider,
)

_PROVIDER_ALIASES = {
    "anthropic": "others",
    "claude": "others",
    "openai": "others",
    "gpt": "others",
    "gemini": "others",
    "google": "others",
    "bedrock": "others",
    "aws": "others",
    "custom": "others",
    "other": "others",
}


def _normalize_contact_number(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10 or len(digits) > 12:
        return None
    normalized = f"+{digits}" if raw.startswith("+") else digits
    try:
        return validate_contact_number(normalized)
    except ValueError:
        return None


def _normalize_languages(value: Any) -> list[str] | None:
    if value is None:
        return None
    values = value if isinstance(value, list) else [value]
    try:
        valid_codes = get_valid_language_codes()
    except Exception:
        valid_codes = []
    by_lower = {str(code).lower(): code for code in valid_codes}
    common_aliases = {
        "en-us": "en",
        "en_us": "en",
        "english": "en",
        "es": "es",
        "spanish": "es",
        "fr": "fr",
        "french": "fr",
        "de": "de",
        "german": "de",
        "ja": "ja",
        "japanese": "ja",
        "ko": "ko",
        "korean": "ko",
        "pt": "pt",
        "portuguese": "pt",
        "hi": "hi",
        "hindi": "hi",
    }
    normalized = []
    for item in values:
        raw = str(item or "").strip()
        if not raw:
            continue
        lowered = raw.lower()
        candidate = by_lower.get(lowered)
        if candidate is None:
            alias = common_aliases.get(lowered)
            candidate = by_lower.get(alias) if alias else None
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    if not normalized:
        return None
    try:
        return validate_languages(normalized)
    except ValueError:
        return None


class CreateAgentVersionInput(PydanticBaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    agent_id: str = Field(
        default="",
        description="Agent definition name or UUID",
    )
    commit_message: str = Field(
        default="Falcon AI update",
        min_length=1,
        description="Commit message for the version",
    )
    release_notes: str | None = Field(
        default=None, description="Detailed release notes"
    )

    # Agent update fields (matches AgentDefinitionUpdateSerializer fields)
    agent_name: str | None = Field(
        default=None, max_length=255, description="New name for the agent"
    )
    description: str | None = Field(default=None, description="New description")
    provider: str | None = Field(default=None, description="New provider")
    model: str | None = Field(default=None, max_length=255, description="New model")
    model_details: dict | None = Field(
        default=None, description="New model details JSON object"
    )
    language: str | None = Field(default=None, description="New primary language code")
    languages: list[str] | None = Field(
        default=None, description="New list of language codes"
    )
    contact_number: str | None = Field(
        default=None,
        alias="contactNumber",
        max_length=50,
        description="New contact number",
    )
    inbound: bool | None = Field(
        default=None, description="Whether the agent handles inbound calls"
    )
    assistant_id: str | None = Field(
        default=None, max_length=255, description="New assistant ID from the provider"
    )
    api_key: str | None = Field(
        default=None, max_length=255, description="New API key for the provider"
    )
    authentication_method: str | None = Field(
        default=None,
        description="Authentication method (e.g., api_key)",
    )
    agent_type: str | None = Field(
        default=None, description="Agent type (e.g., voice, text)"
    )
    observability_enabled: bool = Field(
        default=False, description="Enable/disable observability"
    )
    websocket_url: str | None = Field(default=None, description="New WebSocket URL")
    websocket_headers: dict | None = Field(
        default=None, description="New WebSocket headers (must be a dict)"
    )
    knowledge_base: UUID | None = Field(
        default=None,
        description="UUID of the knowledge base file (set to null UUID to clear)",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_agentic_inputs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if not normalized.get("agent_id"):
            normalized["agent_id"] = (
                normalized.get("agent")
                or normalized.get("agent_definition_id")
                or normalized.get("agent_name")
                or normalized.get("id")
                or ""
            )
        provider = normalized.get("provider")
        if provider is not None:
            provider_text = str(provider).strip()
            provider_key = provider_text.lower()
            normalized["provider"] = _PROVIDER_ALIASES.get(
                provider_key, provider_key or provider_text
            )
            model_details = normalized.get("model_details")
            if provider_key in _PROVIDER_ALIASES:
                if not isinstance(model_details, dict):
                    model_details = {}
                model_details.setdefault("requested_provider", provider_text)
                normalized["model_details"] = model_details
        contact = normalized.get("contact_number", normalized.get("contactNumber"))
        if contact is not None:
            normalized["contact_number"] = _normalize_contact_number(contact)
        if "languages" in normalized:
            normalized["languages"] = _normalize_languages(normalized.get("languages"))
        auth = normalized.get("authentication_method")
        if auth is not None and str(auth).strip() != "api_key":
            normalized["authentication_method"] = None
        return normalized

    @field_validator("languages")
    @classmethod
    def check_languages(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            return _normalize_languages(v)
        return v

    @field_validator("provider")
    @classmethod
    def check_provider(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            try:
                return validate_provider(v)
            except ValueError:
                return _PROVIDER_ALIASES.get(v.strip().lower(), "others")
        return v

    @field_validator("authentication_method")
    @classmethod
    def check_authentication_method(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            v = v.strip()
            if v != "api_key":
                return None
            return v
        return v

    @field_validator("contact_number")
    @classmethod
    def check_contact_number(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            return _normalize_contact_number(v)
        return v


def _requirements_result(
    title: str,
    requirements: list[str],
    data: dict | None = None,
) -> ToolResult:
    body = "Cannot create a new agent version until these requirements are met:"
    body += "\n\n" + "\n".join(f"- `{item}`" for item in requirements)
    payload = {"requires_fields": requirements}
    if data:
        payload.update(data)
    return ToolResult(content=section(title, body), data=payload)


@register_tool
class CreateAgentVersionTool(BaseTool):
    name = "create_agent_version"
    description = (
        "Updates an agent definition and creates a new version. "
        "Agent fields are updated first, then a new active version is created "
        "with a configuration snapshot. Version number is auto-incremented."
    )
    category = "simulation"
    input_model = CreateAgentVersionInput

    def execute(
        self, params: CreateAgentVersionInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.agent_version import AgentVersion

        agent, unresolved = resolve_agent(
            params.agent_id,
            context,
            title="Agent Required To Create Version",
        )
        if unresolved:
            return unresolved

        # --- Update agent definition fields (matches AgentDefinitionUpdateSerializer) ---
        updated_fields = []

        simple_fields = {
            "agent_name": params.agent_name,
            "description": params.description,
            "provider": params.provider,
            "model": params.model,
            "model_details": params.model_details,
            "language": params.language,
            "languages": params.languages,
            "contact_number": params.contact_number,
            "assistant_id": params.assistant_id,
            "api_key": params.api_key,
            "websocket_url": params.websocket_url,
            "websocket_headers": params.websocket_headers,
        }

        for field_name, value in simple_fields.items():
            if value is not None:
                setattr(agent, field_name, value)
                updated_fields.append(field_name)

        if params.inbound is not None:
            agent.inbound = params.inbound
            updated_fields.append("inbound")

        if params.authentication_method is not None:
            agent.authentication_method = params.authentication_method
            updated_fields.append("authentication_method")

        if params.agent_type is not None:
            agent.agent_type = params.agent_type
            updated_fields.append("agent_type")

        # Handle knowledge_base lookup
        if params.knowledge_base is not None:
            from model_hub.models.develop_dataset import KnowledgeBaseFile

            try:
                kb = KnowledgeBaseFile.objects.get(
                    id=params.knowledge_base,
                    organization=context.organization,
                )
                agent.knowledge_base = kb
                updated_fields.append("knowledge_base")
            except KnowledgeBaseFile.DoesNotExist:
                return ToolResult.not_found(
                    "Knowledge Base", str(params.knowledge_base)
                )

        # --- Cross-field validation ---
        observability_enabled = params.observability_enabled

        if agent.agent_type == "voice":
            if not agent.provider or not agent.provider.strip():
                return _requirements_result(
                    "Agent Version Requirements",
                    ["provider"],
                    {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                )
            if not agent.contact_number or not agent.contact_number.strip():
                return _requirements_result(
                    "Agent Version Requirements",
                    ["contact_number"],
                    {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                )

            should_require_auth = agent.provider != "others" and (
                observability_enabled or not agent.inbound
            )
            if should_require_auth:
                if (
                    not agent.authentication_method
                    or not agent.authentication_method.strip()
                ):
                    return _requirements_result(
                        "Agent Version Requirements",
                        ["authentication_method"],
                        {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                    )
                if agent.authentication_method != "api_key":
                    return _requirements_result(
                        "Agent Version Requirements",
                        ["authentication_method=api_key"],
                        {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                    )

            if agent.contact_number:
                try:
                    validate_contact_number(agent.contact_number)
                except ValueError as e:
                    return _requirements_result(
                        "Agent Version Requirements",
                        [f"valid contact_number ({str(e)})"],
                        {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                    )

        if not agent.inbound:
            if not agent.provider or not agent.provider.strip():
                return _requirements_result(
                    "Agent Version Requirements",
                    ["provider"],
                    {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                )
            if not agent.api_key:
                return _requirements_result(
                    "Agent Version Requirements",
                    ["api_key"],
                    {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                )
            if not agent.assistant_id:
                return _requirements_result(
                    "Agent Version Requirements",
                    ["assistant_id"],
                    {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                )

        if observability_enabled and agent.provider != "others" and agent.inbound:
            if not agent.api_key:
                return _requirements_result(
                    "Agent Version Requirements",
                    ["api_key"],
                    {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                )
            if not agent.assistant_id:
                return _requirements_result(
                    "Agent Version Requirements",
                    ["assistant_id"],
                    {"agent_id": str(agent.id), "agent_name": agent.agent_name},
                )

        # Save agent updates
        if updated_fields:
            agent.save(update_fields=updated_fields + ["updated_at"])
        else:
            agent.save()

        # --- Handle observability provider (matches CreateAgentVersionView logic) ---
        provider = agent.observability_provider
        if provider:
            is_project_deleted = provider.project.deleted
            if is_project_deleted:
                agent.observability_provider = None
                agent.save()
            else:
                provider.enabled = observability_enabled
                provider.save()
        else:
            if observability_enabled:
                from tracer.utils.observability_provider import (
                    create_observability_provider,
                )

                new_provider = create_observability_provider(
                    enabled=True,
                    user_id=str(context.user.id),
                    organization=context.organization,
                    workspace=context.workspace,
                    project_name=agent.agent_name,
                    provider=agent.provider,
                )
                if new_provider and not isinstance(new_provider, dict):
                    agent.observability_provider = new_provider
                    agent.save()

        # --- Create new active version ---
        version = agent.create_version(
            description=agent.description,
            commit_message=params.commit_message,
            release_notes=params.release_notes,
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        info = key_value_block(
            [
                ("Version ID", f"`{version.id}`"),
                ("Agent", agent.agent_name),
                ("Version", version.version_name),
                ("Version Number", str(version.version_number)),
                ("Status", version.status),
                ("Commit Message", params.commit_message),
                (
                    "Updated Fields",
                    ", ".join(updated_fields) if updated_fields else "—",
                ),
                ("Created", format_datetime(version.created_at)),
            ]
        )

        content = section("Agent Version Created", info)

        return ToolResult(
            content=content,
            data={
                "id": str(version.id),
                "agent_id": str(agent.id),
                "version_number": version.version_number,
                "version_name": version.version_name,
                "status": version.status,
                "updated_fields": updated_fields,
            },
        )
