from typing import Optional
from uuid import UUID

from django.db import transaction
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.validators import (
    validate_contact_number,
    validate_languages,
    validate_provider,
)


class CreateAgentVersionInput(PydanticBaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_id: UUID = Field(description="The UUID of the agent definition")
    commit_message: str = Field(
        min_length=1, description="Commit message for the version"
    )
    release_notes: Optional[str] = Field(
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

    @field_validator("languages")
    @classmethod
    def check_languages(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            if len(v) == 0:
                raise ValueError("At least one language is required")
            return validate_languages(v)
        return v

    @field_validator("provider")
    @classmethod
    def check_provider(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            return validate_provider(v)
        return v

    @field_validator("authentication_method")
    @classmethod
    def check_authentication_method(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            v = v.strip()
            if v != "api_key":
                raise ValueError("authentication_method must be 'api_key'")
            return v
        return v

    @field_validator("contact_number")
    @classmethod
    def check_contact_number(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            return validate_contact_number(v)
        return v


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

        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.agent_version import AgentVersion
        from simulate.services.agent_definition import (
            is_masked,
            sync_provider_credentials,
        )
        from simulate.services.types.agent_definition import (
            ProviderCredentialsInput,
        )
        from tracer.services.observability_switch import (
            ObservabilityRefused,
            refusal_body,
            set_agent_observability,
        )

        try:
            agent = AgentDefinition.objects.get(
                id=params.agent_id,
                organization=context.organization,
                deleted=False,
            )
        except AgentDefinition.DoesNotExist:
            return ToolResult.not_found("Agent", str(params.agent_id))

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
                return ToolResult.validation_error(
                    "provider is required for voice agents. "
                    "Provide provider in this update or ensure it is already set on the agent."
                )
            if not agent.contact_number or not agent.contact_number.strip():
                return ToolResult.validation_error(
                    "contact_number is required for voice agents. "
                    "Provide contact_number in this update or ensure it is already set on the agent."
                )

            should_require_auth = agent.provider != "others" and (
                observability_enabled or not agent.inbound
            )
            if should_require_auth:
                if (
                    not agent.authentication_method
                    or not agent.authentication_method.strip()
                ):
                    return ToolResult.validation_error(
                        "authentication_method is required for configured voice agents"
                    )
                if agent.authentication_method != "api_key":
                    return ToolResult.validation_error(
                        "authentication_method must be 'api_key'"
                    )

            if agent.contact_number:
                try:
                    validate_contact_number(agent.contact_number)
                except ValueError as e:
                    return ToolResult.validation_error(str(e))

        if not agent.inbound:
            if not agent.provider or not agent.provider.strip():
                return ToolResult.validation_error(
                    "provider is required for outbound agents. "
                    "Provide provider in this update or ensure it is already set on the agent."
                )
            if not agent.api_key:
                return ToolResult.validation_error(
                    "api_key is required for outbound agents. "
                    "Provide api_key in this update or ensure it is already set on the agent."
                )
            if not agent.assistant_id:
                return ToolResult.validation_error(
                    "assistant_id is required for outbound agents. "
                    "Provide assistant_id in this update or ensure it is already set on the agent."
                )

        if observability_enabled and agent.provider != "others" and agent.inbound:
            if not agent.api_key:
                return ToolResult.validation_error(
                    "api_key is required when observability is enabled. "
                    "Provide api_key in this update or ensure it is already set on the agent."
                )
            if not agent.assistant_id:
                return ToolResult.validation_error(
                    "assistant_id is required when observability is enabled. "
                    "Provide assistant_id in this update or ensure it is already set on the agent."
                )

        try:
            with transaction.atomic():
                # Only these columns: a full save would write back this
                # copy's stale observability link.
                if updated_fields:
                    agent.save(update_fields=updated_fields + ["updated_at"])

                # --- Create new active version ---
                version = agent.create_version(
                    description=agent.description,
                    commit_message=params.commit_message,
                    release_notes=params.release_notes,
                    status=AgentVersion.StatusChoices.ACTIVE,
                )
                if params.api_key and not is_masked(params.api_key):
                    sync_provider_credentials(
                        version,
                        ProviderCredentialsInput(
                            provider=agent.provider or "",
                            api_key=params.api_key,
                            assistant_id=agent.assistant_id or None,
                            provider_was_provided=params.provider is not None,
                        ),
                    )
                set_agent_observability(
                    agent_definition=agent,
                    enabled=observability_enabled,
                    user_id=str(context.user.id),
                    organization=context.organization,
                    workspace=context.workspace,
                )
                version.configuration_snapshot = version.create_snapshot(
                    commit_message=version.commit_message or ""
                )
                version.save(update_fields=["configuration_snapshot"])
        except ObservabilityRefused as error:
            return ToolResult.validation_error(refusal_body(error)["detail"])

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
