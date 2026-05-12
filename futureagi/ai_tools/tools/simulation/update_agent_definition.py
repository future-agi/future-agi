from uuid import UUID

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


class UpdateAgentDefinitionInput(PydanticBaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_id: UUID = Field(description="The UUID of the agent definition to update")
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
    # Accept both snake_case and camelCase inputs.
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
    commit_message: str = Field(
        default="", description="Commit message for the new version"
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
            # AgentDefinition model choices only allow api_key.
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
class UpdateAgentDefinitionTool(BaseTool):
    name = "update_agent_definition"
    description = (
        "Updates an existing agent definition and creates a new version. "
        "Only provided fields will be changed. "
        "A new active version is created after the update."
    )
    category = "simulation"
    input_model = UpdateAgentDefinitionInput

    def execute(
        self, params: UpdateAgentDefinitionInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.agent_version import AgentVersion

        try:
            agent = AgentDefinition.objects.get(
                id=params.agent_id,
                organization=context.organization,
                deleted=False,
            )
        except AgentDefinition.DoesNotExist:
            return ToolResult.not_found("Agent", str(params.agent_id))

        updated_fields = []

        # Simple field updates
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

        # Handle boolean field (inbound)
        if params.inbound is not None:
            agent.inbound = params.inbound
            updated_fields.append("inbound")

        # Handle authentication_method update
        if params.authentication_method is not None:
            agent.authentication_method = params.authentication_method
            updated_fields.append("authentication_method")

        # Handle agent_type update
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

        if not updated_fields:
            return ToolResult.needs_input(
                "No update fields were provided. Include at least one field such as "
                "`agent_name`, `description`, `provider`, `model`, `language`, "
                "`agent_type`, `websocket_url`, or `knowledge_base`.",
                missing_fields=["update_fields"],
                data={
                    "agent_id": str(agent.id),
                    "available_update_fields": [
                        "agent_name",
                        "description",
                        "provider",
                        "model",
                        "model_details",
                        "language",
                        "languages",
                        "contact_number",
                        "assistant_id",
                        "api_key",
                        "authentication_method",
                        "agent_type",
                        "websocket_url",
                        "websocket_headers",
                        "knowledge_base",
                    ],
                },
            )

        # Cross-field validation on the resulting state
        observability_enabled = params.observability_enabled

        # Voice agents require provider and contact_number
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

            # Backend/UI parity: require authentication_method when:
            # - provider != "others", and
            # - observability is enabled (inbound voice) OR this is an outbound agent (inbound=false).
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

            # Ensure phone format/length parity even when contact_number isn't updated.
            if agent.contact_number:
                try:
                    validate_contact_number(agent.contact_number)
                except ValueError as e:
                    return ToolResult.validation_error(str(e))

        # Outbound agents require provider, api_key and assistant_id
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

        # Observability with non-"others" provider requires api_key and assistant_id
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

        agent.save(update_fields=updated_fields + ["updated_at"])

        # Handle observability provider (matches CreateAgentVersionView logic)
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

        # Create a new active version (matches CreateAgentVersionView flow)
        version = agent.create_version(
            description=agent.description,
            commit_message=params.commit_message
            or f"Updated fields: {', '.join(updated_fields)}",
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        info = key_value_block(
            [
                ("ID", f"`{agent.id}`"),
                ("Name", agent.agent_name),
                ("Type", agent.agent_type),
                ("Provider", agent.provider or "—"),
                ("Model", agent.model or "—"),
                ("Updated Fields", ", ".join(updated_fields)),
                ("Version", version.version_name),
                ("Version Number", str(version.version_number)),
                ("Updated At", format_datetime(agent.updated_at)),
            ]
        )

        content = section("Agent Updated", info)

        return ToolResult(
            content=content,
            data={
                "id": str(agent.id),
                "name": agent.agent_name,
                "updated_fields": updated_fields,
                "version_id": str(version.id),
                "version_number": version.version_number,
                "version_name": version.version_name,
            },
        )
