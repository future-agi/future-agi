import uuid

from django.db import models  # type: ignore[import-not-found]

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from simulate.models.test_execution import CallExecution
from tfc.utils.base_model import BaseModel


class ChatMessageModel(BaseModel):
    """
    Model to store chat messages for call executions.
    Stores both input and output messages from chat sessions.
    """

    class RoleChoices(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        TOOL = "tool", "Tool"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    call_execution = models.ForeignKey(
        CallExecution,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        help_text="The call execution this chat message belongs to",
    )

    role = models.CharField(
        max_length=20,
        choices=RoleChoices.choices,
        help_text="Role of the message sender (user or assistant)",
    )

    messages = models.JSONField(
        default=list,
        blank=True,
        help_text="List of extracted message content strings",
    )

    content = models.JSONField(
        default=list,
        blank=True,
        help_text="Full message content as list of dictionaries (ChatMessage objects)",
    )

    session_id = models.CharField(
        max_length=255,
        help_text="VAPI chat session ID",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        help_text="Organization this chat message belongs to",
    )

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        help_text="Workspace this chat message belongs to",
    )

    tool_calls = models.JSONField(
        default=list,
        blank=True,
        help_text="List of tool calls used for the message",
    )

    # Message-level metrics (individual columns for better query performance)
    tokens = models.IntegerField(
        null=True,
        blank=True,
        help_text="Token count for this message (calculated from content)",
    )
    latency_ms = models.IntegerField(
        null=True,
        blank=True,
        help_text="Latency in milliseconds for this message (from SDK)",
    )

    class Meta:
        db_table = "simulate_chat_message"
        verbose_name = "Chat Message"
        verbose_name_plural = "Chat Messages"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["call_execution", "created_at"],
                name="idx_chatmsg_exec_time",
            ),
            models.Index(
                fields=["session_id", "created_at"],
                name="idx_chatmsg_session_time",
            ),
            models.Index(fields=["role"], name="idx_chatmsg_role"),
        ]

    def __str__(self):
        return f"{self.role} - {self.call_execution_id} - {self.created_at}"
