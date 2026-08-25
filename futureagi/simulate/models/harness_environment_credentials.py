import base64
import json
import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from tfc.utils.base_model import BaseModel


class HarnessCredentialFile(BaseModel):
    """Encrypted, platform-owned credential file for an RL environment.

    The execution provider receives a fresh attempt-scoped copy. It never owns
    the durable credential and therefore remains free to delete its copy when
    an attempt finishes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="harness_credential_files"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="harness_credential_files",
        null=True,
        blank=True,
    )
    environment_name = models.CharField(max_length=255)
    filename = models.CharField(max_length=255, default="credential")
    content_type = models.CharField(
        max_length=255, default="application/octet-stream"
    )
    encrypted_content = models.TextField()
    size = models.PositiveIntegerField()

    class Meta:
        db_table = "simulate_harness_credential_file"

    def set_content(self, content: bytes) -> None:
        self.encrypted_content = encrypt_token(base64.b64encode(content).decode("ascii"))
        self.size = len(content)

    def get_content(self) -> bytes:
        return base64.b64decode(decrypt_token(self.encrypted_content).encode("ascii"))


class HarnessEnvironmentCredentials(BaseModel):
    """Durable encrypted credentials bound to one saved harness environment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    harness_job_id = models.CharField(max_length=255, unique=True, db_index=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="harness_environment_credentials",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="harness_environment_credentials",
        null=True,
        blank=True,
    )
    encrypted_environment = models.TextField(default="")
    credential_file_ids = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "simulate_harness_environment_credentials"

    def set_environment(self, values: dict[str, str]) -> None:
        serialized = json.dumps(values, sort_keys=True, separators=(",", ":"))
        self.encrypted_environment = encrypt_token(serialized)

    def get_environment(self) -> dict[str, str]:
        if not self.encrypted_environment:
            return {}
        value = json.loads(decrypt_token(self.encrypted_environment))
        return {str(name): str(item) for name, item in value.items()}
