
from django.conf import settings
from pydantic import BaseModel


class AgentConfigurationSnapshot(BaseModel):
    """
    Schema for `AgentVersion.configuration_snapshot`.
    """

    # Core fields
    inbound: bool
    languages: list[str] = []
    provider: str | None
    agent_name: str
    commit_message: str
    contact_number: str | None
    authentication_method: str
    language: str | None = "en"
    observability_enabled: bool = False
    description: str | None = None
    assistant_id: str | None = None
    knowledge_base: str | None = None
    model: str | None = None
    model_details: dict | None = None
    agent_type: str | None = None
    livekit_url: str | None = None
    livekit_agent_name: str | None = None
    livekit_config_json: dict | None = None
    livekit_max_concurrency: int | None = settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
