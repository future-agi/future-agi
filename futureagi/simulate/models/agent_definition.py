import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from accounts.models import Organization
from model_hub.models.develop_dataset import KnowledgeBaseFile
from tfc.utils.base_model import BaseModel
from tracer.models.observability_provider import ObservabilityProvider


class AgentDefinitionAuthenticationChoices(models.TextChoices):
    API_KEY = "api_key", "Api Key"


class AgentTypeChoices(models.TextChoices):
    VOICE = "voice", "Voice"
    TEXT = "text", "Text"


class AgentDefinition(BaseModel):
    """
    Model to store AI agent definitions for simulation
    """

    class LanguageChoices(models.TextChoices):
        ARABIC = "ar", "Arabic"
        BULGARIAN = "bg", "Bulgarian"
        CHINESE_SIMPLIFIED = "zh", "Chinese Simplified"
        CZECH = "cs", "Czech"
        DANISH = "da", "Danish"
        DUTCH = "nl", "Dutch"
        ENGLISH = "en", "English"
        FINNISH = "fi", "Finnish"
        FRENCH = "fr", "French"
        GERMAN = "de", "German"
        GREEK = "el", "Greek"
        HINDI = "hi", "Hindi"
        HUNGARIAN = "hu", "Hungarian"
        INDONESIAN = "id", "Indonesian"
        ITALIAN = "it", "Italian"
        JAPANESE = "ja", "Japanese"
        KOREAN = "ko", "Korean"
        MALAY = "ms", "Malay"
        NORWEGIAN = "no", "Norwegian"
        POLISH = "pl", "Polish"
        PORTUGUESE = "pt", "Portuguese"
        ROMANIAN = "ro", "Romanian"
        RUSSIAN = "ru", "Russian"
        SLOVAK = "sk", "Slovak"
        SPANISH = "es", "Spanish"
        SWEDISH = "sv", "Swedish"
        TURKISH = "tr", "Turkish"
        UKRAINIAN = "uk", "Ukrainian"
        VIETNAMESE = "vi", "Vietnamese"

    # Alias for backwards compatibility (many call sites use AgentDefinition.AgentTypeChoices)
    AgentTypeChoices = AgentTypeChoices

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    agent_name = models.CharField(max_length=255, help_text="Name of the AI agent")

    agent_type = models.CharField(
        max_length=255,
        choices=AgentTypeChoices.choices,
        default=AgentTypeChoices.VOICE,
    )

    contact_number = models.CharField(
        max_length=50,
        help_text="Phone number associated with the AI agent",
        blank=True,
        null=True,
    )

    inbound = models.BooleanField(help_text="Whether the agent handles inbound calls")

    description = models.TextField(
        help_text="Detailed description of the AI agent's purpose and capabilities"
    )

    assistant_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="External identifier for the assistant",
    )

    provider = models.CharField(
        max_length=255, blank=True, null=True, help_text="Provider of the AI agent"
    )

    language = models.CharField(
        max_length=2,
        choices=LanguageChoices.choices,
        help_text="Language of the agent",
        null=True,
        blank=True,
    )

    languages = ArrayField(
        models.CharField(
            max_length=2,
            choices=LanguageChoices.choices,
            help_text="Language of the agent",
        ),
        blank=True,
        null=True,
    )

    websocket_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text="WebSocket URL for real-time communication with the agent",
    )

    websocket_headers = models.JSONField(
        blank=True,
        null=True,
        default=dict,
        help_text="Headers to be sent to the websocket server",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agent_definitions",
        help_text="Organization this agent definition belongs to",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="agent_definitions",
        null=True,
        blank=True,
    )

    knowledge_base = models.ForeignKey(
        KnowledgeBaseFile, on_delete=models.CASCADE, null=True, blank=True
    )

    api_key = models.CharField(
        max_length=255, null=True, blank=True, help_text="API key for the agent"
    )

    observability_provider = models.OneToOneField(
        ObservabilityProvider,
        on_delete=models.CASCADE,
        related_name="agent_definition",
        blank=True,
        null=True,
    )

    authentication_method = models.CharField(
        choices=AgentDefinitionAuthenticationChoices,
        default=AgentDefinitionAuthenticationChoices.API_KEY,
        blank=True,
        null=True,
    )

    model = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Model of the agent",
    )

    model_details = models.JSONField(
        blank=True,
        null=True,
        help_text="Details of the model",
        default=dict,
    )

    class Meta:
        db_table = "simulate_agent_definition"
        verbose_name = "Agent Definition"
        verbose_name_plural = "Agent Definitions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.agent_name} - {self.contact_number}"

    @property
    def active_version(self):
        """Get the currently active version of this agent"""
        return self.versions.filter(status="active").first()

    @property
    def latest_version(self):
        """Get the latest version of this agent"""
        return self.versions.order_by("-version_number").first()

    @property
    def version_count(self):
        """Get the total number of versions for this agent"""
        return self.versions.count()

    def get_version(self, id):
        """Get the currently active version of this agent"""
        return self.versions.filter(id=id).first()

    def create_version(
        self, description, commit_message, release_notes=None, status="draft"
    ):
        """Create a new version of this agent"""
        from .agent_version import AgentVersion

        # Create a new version
        version = AgentVersion.objects.create(
            agent_definition=self,
            organization=self.organization,
            commit_message=commit_message,
            description=description,
            release_notes=release_notes,
            status=status,
            workspace=self.workspace,
        )

        return version

    def get_version_history(self):
        """Get all versions of this agent ordered by version number"""
        return self.versions.order_by("-version_number")


class ProviderCredentials(BaseModel):
    """Encrypted credential storage for voice providers.

    Centralises all provider credentials (VAPI, Retell, LiveKit) in one
    table with Fernet encryption at rest. One-to-one with AgentDefinition.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class ProviderType(models.TextChoices):
        VAPI = "vapi", "Vapi"
        RETELL = "retell", "Retell"
        LIVEKIT = "livekit", "LiveKit"

    agent_definition = models.OneToOneField(
        AgentDefinition,
        on_delete=models.CASCADE,
        related_name="credentials",
    )
    provider_type = models.CharField(
        max_length=50,
        choices=ProviderType.choices,
    )

    # Encrypted credential fields (stored as enc::-prefixed ciphertext)
    api_key = models.TextField(blank=True, default="")
    api_secret = models.TextField(blank=True, default="")

    # Non-secret fields (plaintext)
    assistant_id = models.CharField(max_length=255, blank=True, default="")
    server_url = models.URLField(max_length=500, blank=True, default="")
    agent_name = models.CharField(max_length=255, blank=True, default="")
    config_json = models.JSONField(blank=True, null=True, default=dict)
    max_concurrency = models.PositiveIntegerField(blank=True, null=True, default=5)

    class Meta:
        db_table = "simulate_provider_credentials"
        verbose_name = "Provider Credentials"
        verbose_name_plural = "Provider Credentials"

    def __str__(self):
        return f"{self.provider_type} credentials for {self.agent_definition}"

    def save(self, *args, **kwargs):
        """Encrypt secrets before saving."""
        from agentcc.services.credential_manager import encrypt_token

        if self.api_key and not self.api_key.startswith("enc::"):
            self.api_key = encrypt_token(self.api_key)
        if self.api_secret and not self.api_secret.startswith("enc::"):
            self.api_secret = encrypt_token(self.api_secret)
        super().save(*args, **kwargs)

    def get_api_key(self) -> str:
        """Return decrypted API key."""
        from agentcc.services.credential_manager import decrypt_token

        return decrypt_token(self.api_key)

    def get_api_secret(self) -> str:
        """Return decrypted API secret."""
        from agentcc.services.credential_manager import decrypt_token

        return decrypt_token(self.api_secret)

    def get_masked_api_key(self) -> str:
        """Return masked API key for display."""
        from agentcc.services.credential_manager import mask_key

        return mask_key(self.get_api_key())

    def get_masked_api_secret(self) -> str:
        """Return masked API secret for display."""
        return "********" if self.api_secret else ""
