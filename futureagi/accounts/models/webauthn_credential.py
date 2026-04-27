import uuid

from django.db import models

from accounts.models.user import User
from tfc.utils.base_model import BaseModel


class WebAuthnCredential(BaseModel):
    """Stores a registered WebAuthn/passkey credential.
    A user can have multiple credentials (e.g., laptop + phone + security key).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="webauthn_credentials"
    )
    name = models.CharField(max_length=255, default="")
    credential_id = models.TextField(unique=True)
    public_key = models.TextField()
    sign_count = models.PositiveIntegerField(default=0)
    transports = models.JSONField(default=list)
    aaguid = models.CharField(max_length=36, blank=True, default="")
    backup_eligible = models.BooleanField(default=False)
    backup_state = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_webauthn_credential"
