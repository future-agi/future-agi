import uuid

from django.db import models

from accounts.models.user import User
from tfc.utils.base_model import BaseModel


class RecoveryCode(BaseModel):
    """Single-use backup recovery codes for 2FA.
    Codes are hashed before storage (like passwords).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="recovery_codes"
    )
    code_hash = models.CharField(max_length=255)
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_recovery_code"
