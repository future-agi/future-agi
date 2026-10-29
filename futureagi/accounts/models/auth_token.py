import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class AuthTokenType(models.TextChoices):
    ACCESS = "access"
    REFRESH = "refresh"


class AuthTokenOrigin(models.TextChoices):
    SAML = "saml"


class AuthToken(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="auth_tokens"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    auth_type = models.CharField(
        max_length=255, blank=True, null=True, choices=AuthTokenType.choices
    )
    is_active = models.BooleanField(default=True)
    auth_origin = models.CharField(
        max_length=16, choices=AuthTokenOrigin.choices, null=True, blank=True
    )
    scoped_organization = models.ForeignKey(
        "accounts.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="scoped_auth_tokens",
    )
    origin_idp = models.ForeignKey(
        "saml2_auth.SAMLMetadataModel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="saml_auth_tokens",
    )
    origin_idp_generation = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"Auth Token {self.id}"

    class Meta:
        db_table = "accounts_auth_token"
        ordering = ["-created_at"]


AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES = 2 * 24 * 60  # 2 days
REFRESH_TOKEN_EXPIRATION_TIME_IN_SECONDS = 7 * 24 * 60 * 60  # 7 days
