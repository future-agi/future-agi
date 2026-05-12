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
from ai_tools.validators import (
    validate_agent_type,
    validate_contact_number,
    validate_languages,
    validate_provider,
)


class CreateAgentDefinitionInput(PydanticBaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_name: str = Field(
        min_length=1, max_length=255, description="Name of the agent"
    )
    agent_type: str = Field(default="text", description="Type of agent: voice or text")
    description: str = Field(
        default="General-purpose assistant agent.",
        description="Description of the agent's purpose",
    )
    commit_message: str = Field(
        default="Initial version",
        min_length=1,
        description="Commit message for the initial version",
    )
    languages: list[str] = Field(
        default_factory=lambda: ["en"],
        description="List of language codes (e.g., ['en', 'es'])",
    )
    language: str | None = Field(
        default=None, description="Primary language code (e.g., en, es, fr)"
    )
    provider: str | None = Field(
        default=None,
        description="Provider of the AI agent (e.g., vapi, retell, others). Required for voice agents.",
    )
    model: str | None = Field(
        default=None, max_length=255, description="Model to use for the agent"
    )
    model_details: dict | None = Field(
        default=None, description="Model details JSON object"
    )
    # Accept both snake_case (tool API) and camelCase (frontend/UI) inputs.
    contact_number: str | None = Field(
        default=None,
        alias="contactNumber",
        max_length=50,
        description="Phone number for voice agents",
    )
    inbound: bool = Field(
        default=True, description="Whether the agent handles inbound calls"
    )
    observability_enabled: bool = Field(
        default=False,
        description="Enable observability for this agent (requires provider and assistant_id)",
    )
    assistant_id: str | None = Field(
        default=None, max_length=255, description="Assistant ID from the provider"
    )
    api_key: str | None = Field(
        default=None, max_length=255, description="API key for the provider"
    )
    authentication_method: str | None = Field(
        default="api_key",
        description="Authentication method for the agent (default: api_key)",
    )
    websocket_url: str | None = Field(
        default=None, description="WebSocket URL for real-time communication"
    )
    websocket_headers: dict | None = Field(
        default=None, description="Headers for WebSocket connection (must be a dict)"
    )
    knowledge_base: UUID | None = Field(
        default=None, description="UUID of the knowledge base file to associate"
    )
    replay_session_id: UUID | None = Field(
        default=None,
        description="Optional UUID of a replay session to link this agent definition to",
    )

    @field_validator("agent_name")
    @classmethod
    def check_agent_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Agent name is required")
        return v

    @field_validator("description")
    @classmethod
    def check_description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Description is required")
        return v

    @field_validator("commit_message")
    @classmethod
    def check_commit_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Commit message is required")
        return v

    @field_validator("language")
    @classmethod
    def check_language(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None

        # Mirror AgentDefinitionSerializer.validate_language / LanguageChoices.
        from simulate.models.agent_definition import AgentDefinition

        valid = {choice[0] for choice in AgentDefinition.LanguageChoices.choices}
        if v not in valid:
            raise ValueError(f"Invalid language '{v}'")
        return v

    @field_validator("authentication_method")
    @classmethod
    def check_authentication_method(cls, v: str | None) -> str | None:
        # Backend model choices for AgentDefinitionAuthenticationChoices only allow api_key.
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("authentication_method is required")
        if v != "api_key":
            raise ValueError("authentication_method must be 'api_key'")
        return v

    @field_validator("agent_type")
    @classmethod
    def check_agent_type(cls, v: str) -> str:
        return validate_agent_type(v)

    @field_validator("languages")
    @classmethod
    def check_languages(cls, v: list[str]) -> list[str]:
        return validate_languages(v)

    @field_validator("provider")
    @classmethod
    def check_provider(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            return validate_provider(v)
        return v

    @field_validator("contact_number")
    @classmethod
    def check_contact_number(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            return validate_contact_number(v)
        return v

    @model_validator(mode="after")
    def check_voice_agent_requirements(self) -> "CreateAgentDefinitionInput":
        # Voice agents require provider
        if self.agent_type == "voice":
            if not self.provider or not self.provider.strip():
                raise ValueError("provider is required for voice agents")
            if not self.contact_number or not self.contact_number.strip():
                raise ValueError("contact_number is required for voice agents")

        # Outbound calls require api_key and assistant_id
        if not self.inbound:
            if self.agent_type == "voice" and (
                not self.provider or not self.provider.strip()
            ):
                raise ValueError("provider is required for outbound voice agents")
            if not self.api_key or not self.api_key.strip():
                raise ValueError("api_key is required for outbound agents")
            if not self.assistant_id or not self.assistant_id.strip():
                raise ValueError("assistant_id is required for outbound agents")

        # Observability with non-"others" provider requires api_key and assistant_id
        if self.observability_enabled and self.provider != "others" and self.inbound:
            if not self.api_key or not self.api_key.strip():
                raise ValueError("api_key is required when observability is enabled")
            if not self.assistant_id or not self.assistant_id.strip():
                raise ValueError(
                    "assistant_id is required when observability is enabled"
                )

        # UI requirement: when provider != others and we're not purely inbound-without-observability,
        # authentication_method must be present (backend enforces allowed choices).
        if (
            self.agent_type == "voice"
            and self.provider != "others"
            and (self.observability_enabled or not self.inbound)
        ):
            if not self.authentication_method or not self.authentication_method.strip():
                raise ValueError(
                    "authentication_method is required for configured voice agents"
                )

        return self


@register_tool
class CreateAgentDefinitionTool(BaseTool):
    name = "create_agent_definition"
    description = (
        "Creates a new agent definition in the current workspace. "
        "Specify agent name, type (voice/text), provider, model, and other settings."
    )
    category = "simulation"
    input_model = CreateAgentDefinitionInput

    def execute(
        self, params: CreateAgentDefinitionInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.agent_version import AgentVersion
        from tracer.models.replay_session import ReplaySession
        from tracer.utils.otel import ResourceLimitError
        from tracer.utils.replay_session import link_agent_to_replay_session

        # Create observability provider if enabled
        observability_provider = None
        if (
            params.observability_enabled
            and params.assistant_id
            and params.api_key
            and params.provider
        ):
            from tracer.models.observability_provider import ProviderChoices
            from tracer.utils.observability_provider import (
                create_observability_provider,
            )

            supported_providers = [
                ProviderChoices.VAPI,
                ProviderChoices.RETELL,
                ProviderChoices.OTHERS,
            ]
            # Align with backend view: only create when provider is supported.
            if params.provider and params.provider in supported_providers:
                try:
                    observability_provider = create_observability_provider(
                        enabled=True,
                        user_id=str(context.user.id),
                        organization=context.organization,
                        workspace=context.workspace,
                        project_name=params.agent_name,
                        provider=params.provider,
                    )
                except ResourceLimitError:
                    return ToolResult.validation_error("PROJECT CREATION LIMIT REACHED")

        # Resolve knowledge base if provided
        knowledge_base = None
        if params.knowledge_base:
            from model_hub.models.develop_dataset import KnowledgeBaseFile

            try:
                knowledge_base = KnowledgeBaseFile.objects.get(
                    id=params.knowledge_base,
                    organization=context.organization,
                )
            except KnowledgeBaseFile.DoesNotExist:
                return ToolResult.not_found(
                    "Knowledge Base", str(params.knowledge_base)
                )

        # Fetch prompt from provider when api_key and assistant_id are provided
        fetched_description = params.description
        if params.api_key and params.assistant_id and params.provider:
            from tracer.models.observability_provider import ProviderChoices

            if params.provider == ProviderChoices.VAPI:
                try:
                    from ee.voice.services.vapi_service import VapiService

                    vapi_service = VapiService(api_key=params.api_key)
                    assistant_json = vapi_service.get_assistant(
                        assistant_id=params.assistant_id
                    )
                    model_info = assistant_json.get("model", {})
                    messages = model_info.get("messages", [])
                    system_messages = [m for m in messages if m.get("role") == "system"]
                    if system_messages:
                        fetched_description = system_messages[0].get(
                            "content", params.description
                        )
                except Exception:
                    return ToolResult.validation_error(
                        "Failed to fetch assistant from Vapi. "
                        "Please recheck your API key and assistant ID."
                    )

            elif params.provider == ProviderChoices.RETELL:
                try:
                    import json as _json

                    from retell import Retell

                    client = Retell(api_key=params.api_key)
                    assistant_raw = client.agent.retrieve(
                        agent_id=params.assistant_id
                    ).model_dump_json()
                    assistant_json = _json.loads(assistant_raw)
                    response_engine = assistant_json.get("response_engine", {})
                    llm_id = response_engine.get("llm_id")
                    if llm_id:
                        response_engine_raw = client.llm.retrieve(
                            llm_id=llm_id
                        ).model_dump_json()
                        response_engine_json = _json.loads(response_engine_raw)
                        fetched_description = response_engine_json.get(
                            "general_prompt", params.description
                        )
                except Exception:
                    return ToolResult.validation_error(
                        "Failed to fetch assistant from Retell. "
                        "Please recheck your API key and assistant ID."
                    )

        agent = AgentDefinition(
            agent_name=params.agent_name,
            agent_type=params.agent_type,
            provider=params.provider,
            model=params.model,
            model_details=params.model_details or {},
            description=fetched_description,
            language=params.language,
            languages=params.languages,
            contact_number=params.contact_number,
            inbound=params.inbound,
            organization=context.organization,
            workspace=context.workspace,
            observability_provider=observability_provider,
            assistant_id=params.assistant_id,
            api_key=params.api_key,
            authentication_method=params.authentication_method,
            websocket_url=params.websocket_url,
            websocket_headers=params.websocket_headers or {},
            knowledge_base=knowledge_base,
        )
        agent.save()

        # Create the first version (matches CreateAgentDefinitionView.post behavior)
        agent.create_version(
            description=fetched_description,
            commit_message=params.commit_message,
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        # Optional: link agent definition to a replay session (matches CreateAgentDefinitionView.post).
        if params.replay_session_id:
            try:
                link_agent_to_replay_session(
                    replay_session_id=str(params.replay_session_id),
                    agent=agent,
                    organization=context.organization,
                )
            except ReplaySession.DoesNotExist:
                return ToolResult.not_found(
                    "Replay Session", str(params.replay_session_id)
                )

        info = key_value_block(
            [
                ("ID", f"`{agent.id}`"),
                ("Name", agent.agent_name),
                ("Type", agent.agent_type),
                ("Provider", agent.provider or "—"),
                ("Model", agent.model or "—"),
                ("Languages", ", ".join(agent.languages) if agent.languages else "—"),
                ("Contact Number", agent.contact_number or "—"),
                ("Observability", "Enabled" if observability_provider else "—"),
                ("Created", format_datetime(agent.created_at)),
            ]
        )

        content = section("Agent Created", info)

        return ToolResult(
            content=content,
            data={
                "id": str(agent.id),
                "name": agent.agent_name,
                "type": agent.agent_type,
                "provider": agent.provider,
                "model": agent.model,
            },
        )
