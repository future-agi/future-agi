import base64
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization
from agentcc.services.credential_manager import ENCRYPTED_PREFIX

# For the URLs the MCP models store: a CIMD client_id is an HTTPS URL, and an
# OAuth resource is a virtual server's canonical URI.
URL_MAX_LENGTH = 2048

# A Fernet token: version byte 0x80, 8-byte timestamp, 16-byte IV, AES-CBC
# ciphertext of one or more 16-byte blocks, 32-byte HMAC; urlsafe base64.
_FERNET_VERSION = b"\x80"
_FERNET_OVERHEAD = 1 + 8 + 16 + 32
_AES_BLOCK = 16


def _is_fernet_token(text):
    try:
        raw = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):  # binascii.Error is a ValueError
        return False
    ciphertext = len(raw) - _FERNET_OVERHEAD
    return (
        raw[:1] == _FERNET_VERSION
        and ciphertext >= _AES_BLOCK
        and ciphertext % _AES_BLOCK == 0
    )


class EncryptedAtRestModel(models.Model):
    """Refuses to store a clear-text value in any field named in ``ENCRYPTED_FIELDS``.

    Each such field holds ``enc::`` plus a Fernet token, written through
    ``agentcc.services.credential_manager.encrypt_token`` (the same
    ``CredentialManager`` provider credentials use). The check is structural
    and needs no key: ``encrypt_token`` passes through any value that already
    starts with ``enc::``, so the prefix alone does not prove encryption.
    ``None`` is allowed for a nullable field. The error never carries the
    value: it may be a secret.
    """

    ENCRYPTED_FIELDS: tuple[str, ...] = ()

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        self._refuse_unencrypted(self.ENCRYPTED_FIELDS)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        self._refuse_unencrypted(
            name
            for name in self.ENCRYPTED_FIELDS
            if update_fields is None or name in update_fields
        )
        super().save(*args, **kwargs)

    def _refuse_unencrypted(self, names):
        errors = {}
        for name in names:
            value = getattr(self, name)
            if value is None and self._meta.get_field(name).null:
                continue
            if (
                not isinstance(value, str)
                or not value.startswith(ENCRYPTED_PREFIX)
                or not _is_fernet_token(value[len(ENCRYPTED_PREFIX) :])
            ):
                errors[name] = (
                    f"{name} must be written through CredentialManager "
                    "(agentcc.services.credential_manager.encrypt_token)."
                )
        if errors:
            raise ValidationError(errors)


class AgentccMCPCredential(EncryptedAtRestModel):
    """An upstream MCP server credential, held in the Django vault.

    Shared credentials are assembled into the gateway push only; per-user
    credentials (``scope=user``) are fetched by the gateway just in time.
    Neither is ever returned by a read. At most one active
    credential exists per org, server, scope, user and account label.
    """

    ENCRYPTED_FIELDS = ("encrypted_secret", "encrypted_refresh")

    class Scope(models.TextChoices):
        SHARED = "shared"
        USER = "user"

    class Kind(models.TextChoices):
        BEARER = "bearer"
        API_KEY = "api_key"
        HEADER = "header"
        OAUTH = "oauth"
        OAUTH_CLIENT = "oauth_client"

    class Status(models.TextChoices):
        ACTIVE = "active"
        EXPIRED = "expired"
        REVOKED = "revoked"
        NEEDS_REAUTH = "needs_reauth"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_credentials",
    )
    server_id = models.CharField(max_length=64)
    scope = models.CharField(max_length=8, choices=Scope.choices, default=Scope.SHARED)
    # Set exactly when scope is "user" (the check constraint below).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_credentials",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    header_name = models.CharField(max_length=128, blank=True, default="")
    # The origin (scheme://host:port) its secret was entered for, together
    # with its server's URL (a console save, a create, the secret migration).
    # A shared credential is pushed and probed only for its server at this
    # origin, and a version that puts its server anywhere else is not
    # activated. "" binds it to none: it is sent nowhere until re-entered. A
    # rotation keeps it while a config sends the credential and clears it
    # otherwise. The database default keeps an insert from code
    # that predates the column working during a rolling deploy.
    origin = models.CharField(
        max_length=URL_MAX_LENGTH, blank=True, default="", db_default=""
    )
    encrypted_secret = models.TextField()
    encrypted_refresh = models.TextField(null=True, blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    oauth_issuer = models.CharField(max_length=512, blank=True, default="")
    oauth_client_id = models.CharField(
        max_length=URL_MAX_LENGTH, blank=True, default=""
    )
    # Exact RFC 8707 resource used at authorization/code exchange. NULL means
    # a legacy grant with unknown resource; "" means the grant omitted it.
    # Never infer or normalize this identifier from the MCP transport URL.
    oauth_resource = models.CharField(
        max_length=URL_MAX_LENGTH, null=True, blank=True, default=None, db_default=None
    )
    # Recorded when the grant is stored, from its connect: issued through the
    # org's own app registration for a K2 pilot (connector_k2). It is revoked
    # only as that registration, never a platform client, whatever the
    # server's configuration says by then.
    oauth_org_only = models.BooleanField(default=False, db_default=False)
    scopes = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    version = models.PositiveIntegerField(default=1)
    # A shared credential's grant revision within its org and server: every
    # new or rotated secret takes a larger one (under the org lock). It is
    # pushed with the secret, and an eviction revokes a slot through one, so
    # a gateway replica holding an older config can never admit that grant.
    grant_revision = models.PositiveBigIntegerField(default=0, db_default=0)
    label = models.CharField(max_length=255, blank=True, default="")
    # Tells apart two accounts of one user or org on the same server.
    account_label = models.CharField(max_length=64, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expiry_alerted_at = models.DateTimeField(null=True, blank=True)
    # Durable cleanup obligations; a locally revoked account remains manageable
    # until vendor revocation (when supported) and gateway eviction finish.
    oauth_revoke_pending = models.BooleanField(default=False, db_default=False)
    gateway_purge_pending = models.BooleanField(default=False, db_default=False)
    # Empty while connected/never attempted; terminal outcomes survive cleanup.
    oauth_revoke_outcome = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("confirmed", "Confirmed"),
            ("unconfirmed", "Unconfirmed"),
        ],
        blank=True,
        default="",
        db_default="",
    )

    class Meta:
        db_table = "agentcc_mcp_credential"
        ordering = ["-created_at"]
        constraints = [
            # nulls_distinct=False so two shared credentials (user NULL) collide.
            models.UniqueConstraint(
                fields=["organization", "server_id", "scope", "user", "account_label"],
                condition=models.Q(status="active"),
                name="agentcc_mcp_one_active_credential",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scope="shared", user__isnull=True)
                    | models.Q(scope="user", user__isnull=False)
                ),
                name="agentcc_mcp_credential_user_scope",
            ),
        ]

    def __str__(self):
        return (
            f"{self.server_id} {self.scope} {self.kind} v{self.version} ({self.status})"
        )

    def __repr__(self):
        return f"<AgentccMCPCredential {self.pk} org={self.organization_id} {self}>"
