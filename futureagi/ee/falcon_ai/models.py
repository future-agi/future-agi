import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class Skill(BaseModel):
    """A behavior modifier for the Falcon AI agent loop.

    Skills extend the system prompt with domain-specific instructions,
    add specific tools to the loaded tool set, and provide few-shot
    trajectories showing the agent how to accomplish the task.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Nullable — a NULL organization means this is a global builtin skill
    # visible to every org. Custom (user-created) skills always have a
    # non-null organization. See Meta.constraints for the uniqueness rule
    # that prevents duplicate global builtins.
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="falcon_skills",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="falcon_skills",
    )

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description = models.TextField(default="")
    icon = models.CharField(max_length=50, default="mdi:star")

    is_builtin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    instructions = models.TextField(default="")  # Injected into system prompt
    tool_names = models.JSONField(default=list)  # List of tool names to load
    example_trajectories = models.JSONField(default=list)  # Few-shot examples
    trigger_phrases = models.JSONField(default=list)  # Auto-suggest keywords

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-is_builtin", "name"]
        unique_together = [("organization", "slug")]
        constraints = [
            # Postgres treats NULLs as distinct in unique_together, which
            # would allow multiple global rows with the same slug. Enforce
            # global uniqueness explicitly via a partial unique index.
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(organization__isnull=True),
                name="unique_global_skill_slug",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({'builtin' if self.is_builtin else 'custom'})"


class Conversation(BaseModel):
    """A chat conversation between a user and Falcon AI."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="falcon_conversations",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="falcon_conversations",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="falcon_conversations",
    )
    title = models.CharField(max_length=255, default="New conversation")
    context_page = models.CharField(max_length=500, blank=True, default="")
    mode = models.CharField(max_length=30, blank=True, default="")
    metadata = models.JSONField(default=dict)
    active_skill = models.ForeignKey(
        "falcon_ai.Skill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # Context compaction — LLM-generated summary of older conversation turns
    context_summary = models.TextField(blank=True, default="")
    # Approximate total tokens used in this conversation (updated after each turn)
    total_tokens = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Conversation({self.id}, {self.title})"


class Message(BaseModel):
    """A single message within a Falcon AI conversation."""

    ROLE_CHOICES = [
        ("user", "User"),
        ("assistant", "Assistant"),
        ("system", "System"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField(default="")
    thoughts = models.JSONField(default=list)
    tool_calls = models.JSONField(default=list)
    completion_card = models.JSONField(null=True, blank=True)
    files = models.JSONField(
        default=list, blank=True
    )  # [{id, name, size, content_type, url}]
    feedback = models.CharField(max_length=20, blank=True, default="")
    token_count = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    model_used = models.CharField(max_length=100, blank=True, default="")
    latency_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message({self.id}, {self.role})"


class FalconUsage(models.Model):
    """Tracks token usage and costs for Falcon AI interactions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="usage_records",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="falcon_usage",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="falcon_usage",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="falcon_usage",
    )
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    tool_calls_count = models.PositiveIntegerField(default=0)
    model_used = models.CharField(max_length=100)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"FalconUsage({self.id}, {self.model_used})"


class FalconMemory(BaseModel):
    """Stores workspace-scoped key-value memories for Falcon AI.

    Memories persist across conversations and are injected into the
    system prompt to give the agent context about the user's setup.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="falcon_memories",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="falcon_memories",
    )

    key = models.CharField(max_length=200)
    value = models.TextField()
    source = models.CharField(
        max_length=20,
        choices=[
            ("user", "User requested"),
            ("agent", "Agent auto-saved"),
            ("init", "From /init command"),
        ],
        default="agent",
    )

    conversation = models.ForeignKey(
        "falcon_ai.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="falcon_memories",
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("workspace", "key")]

    def __str__(self):
        return f"{self.key}: {self.value[:50]}"


class MCPConnector(BaseModel):
    """An external MCP-compatible server connected to Falcon AI.

    Users can connect external MCP-compatible servers to extend
    the Falcon AI tool set with additional capabilities.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="falcon_mcp_connectors",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="falcon_mcp_connectors",
    )

    name = models.CharField(max_length=100)
    server_url = models.URLField()
    transport = models.CharField(
        max_length=20,
        choices=[
            ("sse", "Server-Sent Events"),
            ("streamable_http", "Streamable HTTP"),
        ],
        default="streamable_http",
    )

    auth_type = models.CharField(
        max_length=20,
        choices=[
            ("none", "No Authentication"),
            ("api_key", "API Key"),
            ("bearer", "Bearer Token"),
            ("oauth", "OAuth 2.1"),
        ],
        default="none",
    )
    auth_header_name = models.CharField(
        max_length=100, blank=True, default="Authorization"
    )
    auth_header_value = models.TextField(
        blank=True, default=""
    )  # encrypted in production

    # OAuth 2.1 fields
    oauth_client_id = models.CharField(max_length=500, blank=True, default="")
    oauth_client_secret = models.TextField(blank=True, default="")
    oauth_server_metadata = models.JSONField(default=dict, blank=True)
    oauth_access_token = models.TextField(blank=True, default="")
    oauth_refresh_token = models.TextField(blank=True, default="")
    oauth_token_expires_at = models.DateTimeField(null=True, blank=True)
    oauth_code_verifier = models.CharField(max_length=200, blank=True, default="")
    oauth_state = models.CharField(max_length=200, blank=True, default="")

    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)

    discovered_tools = models.JSONField(
        default=list
    )  # Cached tool schemas from discovery
    enabled_tool_names = models.JSONField(default=list)  # User-selected subset

    last_discovery_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("workspace", "name")]

    def __str__(self):
        return f"{self.name} ({self.server_url})"


class FalconFile(models.Model):
    """A file uploaded by a user in a Falcon AI conversation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="falcon_files",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="falcon_files",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="falcon_files",
    )

    name = models.CharField(max_length=500)  # original filename
    size = models.PositiveIntegerField(default=0)  # bytes
    content_type = models.CharField(max_length=100, default="")
    storage_key = models.CharField(max_length=500)  # MinIO object key
    storage_url = models.URLField(max_length=1000, blank=True, default="")

    # Extracted text content for LLM context
    text_content = models.TextField(blank=True, default="")

    conversation = models.ForeignKey(
        "falcon_ai.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_files",
    )
    message = models.ForeignKey(
        "falcon_ai.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_files",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name
