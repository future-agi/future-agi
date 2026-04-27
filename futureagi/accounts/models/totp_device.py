import uuid

from django.db import models

from accounts.models.user import User
from tfc.utils.base_model import BaseModel


class UserTOTPDevice(BaseModel):
    """Stores a user's TOTP secret for authenticator-app-based 2FA.
    One device per user (unique constraint on user).
    Secret is Fernet-encrypted at rest.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="totp_device"
    )
    secret_encrypted = models.TextField()
    confirmed = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_user_totp_device"
