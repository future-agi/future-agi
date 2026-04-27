import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from simulate.pydantic_schemas.agent_version import AgentConfigurationSnapshot
from tfc.utils.base_model import BaseModel


class AgentVersion(BaseModel):
    """
    Model to store different versions of agent definitions
    """

    class StatusChoices(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"
        DEPRECATED = "deprecated", "Deprecated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Version information
    version_number = models.PositiveIntegerField(
        help_text="Version number of the agent"
    )

    version_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Human-readable version name (e.g., 'v1.2.3')",
    )

    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.DRAFT,
        help_text="Current status of this version",
    )

    # Performance metrics
    score = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        blank=True,
        null=True,
        help_text="Performance score (0.0 to 10.0)",
    )

    test_count = models.PositiveIntegerField(
        default=0, help_text="Number of tests run for this version"
    )

    pass_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Test pass rate percentage",
    )

    # Version details
    description = models.TextField(help_text="Description of changes in this version")

    commit_message = models.TextField(
        help_text="Commit message for the agent version",
        null=True,
        blank=True,
    )

    release_notes = models.TextField(
        blank=True, null=True, help_text="Detailed release notes for this version"
    )

    # Relationships
    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition",
        on_delete=models.CASCADE,
        related_name="versions",
        help_text="Parent agent definition",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agent_versions",
        help_text="Organization this version belongs to",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="agent_versions",
        null=True,
        blank=True,
    )

    # Configuration snapshot (JSON field to store the exact configuration at this version)
    configuration_snapshot = models.JSONField(
        default=dict, help_text="Snapshot of agent configuration at this version"
    )

    class Meta:
        db_table = "simulate_agent_version"
        verbose_name = "Agent Version"
        verbose_name_plural = "Agent Versions"
        ordering = ["-version_number"]
        unique_together = ["agent_definition", "version_number"]

    def __str__(self):
        return f"{self.agent_definition.agent_name} - v{self.version_number}"

    def save(self, *args, **kwargs):
        # Auto-generate version number if not provided
        if not self.version_number:
            latest_version = (
                AgentVersion.objects.filter(agent_definition=self.agent_definition)
                .order_by("-version_number")
                .first()
            )

            if latest_version:
                self.version_number = latest_version.version_number + 1
            else:
                self.version_number = 1

        # Auto-generate version name if not provided
        if not self.version_name:
            self.version_name = f"v{self.version_number}"

        # Create a snapshot of the current configuration
        if not self.configuration_snapshot:
            self.configuration_snapshot = self.create_snapshot(
                commit_message=self.commit_message
            )

        super().save(*args, **kwargs)

    @property
    def is_active(self):
        """Check if this version is currently active"""
        return self.status == self.StatusChoices.ACTIVE

    @property
    def is_latest(self):
        """Check if this is the latest version"""
        latest = (
            AgentVersion.objects.filter(agent_definition=self.agent_definition)
            .order_by("-version_number")
            .first()
        )
        return self == latest if latest else False

    def activate(self):
        """Activate this version and deactivate others"""
        # Deactivate all other versions of this agent
        AgentVersion.objects.filter(agent_definition=self.agent_definition).exclude(
            id=self.id
        ).update(status=self.StatusChoices.ARCHIVED)

        # Activate this version
        self.status = self.StatusChoices.ACTIVE
        self.save()

    def create_snapshot(self, commit_message):
        """Create a snapshot of the current agent configuration"""
        agent = self.agent_definition

        knowledge_base_value = (
            str(agent.knowledge_base_id) if agent.knowledge_base_id else None
        )

        # Read credentials from ProviderCredentials (decrypted),
        # fall back to AgentDefinition fields for backward compat.
        api_key = agent.api_key or getattr(self.agent_definition, "api_key", "")
        assistant_id = agent.assistant_id or ""
        livekit_url = ""
        livekit_api_key = ""
        livekit_api_secret = ""
        livekit_agent_name = ""
        livekit_config_json = None
        livekit_max_concurrency = settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY

        try:
            creds = self.agent_definition.credentials
            if creds:
                decrypted_key = creds.get_api_key()
                if decrypted_key:
                    api_key = decrypted_key
                if creds.assistant_id:
                    assistant_id = creds.assistant_id
                if creds.provider_type == "livekit":
                    livekit_url = creds.server_url or ""
                    livekit_api_key = decrypted_key or ""
                    livekit_api_secret = creds.get_api_secret() or ""
                    livekit_agent_name = creds.agent_name or ""
                    livekit_config_json = creds.config_json
                    livekit_max_concurrency = (
                        creds.max_concurrency
                        or settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
                    )
        except self.agent_definition.__class__.credentials.RelatedObjectDoesNotExist:
            pass

        schema = AgentConfigurationSnapshot(
            api_key=agent.api_key or "",
            inbound=agent.inbound,
            language=agent.language,
            languages=agent.languages,
            provider=agent.provider,
            agent_name=agent.agent_name,
            agent_type=agent.agent_type,
            description=agent.description,
            assistant_id=agent.assistant_id,
            authentication_method=agent.authentication_method or "",
            commit_message=commit_message,
            contact_number=agent.contact_number,
            knowledge_base=knowledge_base_value,
            observability_enabled=getattr(
                agent.observability_provider, "enabled", False
            ),
            model=agent.model,
            model_details=agent.model_details,
            livekit_url=livekit_url,
            livekit_api_key=livekit_api_key,
            livekit_api_secret=livekit_api_secret,
            livekit_agent_name=livekit_agent_name,
            livekit_config_json=livekit_config_json or None,
            livekit_max_concurrency=livekit_max_concurrency,
        )

        snake = schema.model_dump(exclude_none=True)
        return snake

    def restore_from_snapshot(self):
        """Restore agent definition from this version's snapshot"""
        if not self.configuration_snapshot:
            raise ValueError("No configuration snapshot available")

        # Snapshot stores FK values as plain strings (UUIDs); map them to the
        # attname (_id suffix) so Django doesn't raise ValueError on assignment.
        _FK_REMAP = {
            "knowledge_base": "knowledge_base_id",
            "organization": "organization_id",
            "workspace": "workspace_id",
        }

        skip = {"id", "created_at", "updated_at", "deleted", "deleted_at"}

        for field, value in self.configuration_snapshot.items():
            attr = _FK_REMAP.get(field, field)
            if attr in skip:
                continue
            if hasattr(self.agent_definition, attr):
                setattr(self.agent_definition, attr, value)

        self.agent_definition.save()
        return self.agent_definition
