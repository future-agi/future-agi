import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from accounts.models.organization import Organization
from tfc.utils.base_model import BaseModel

# from tfc.utils.custom_metrics import QueryMixin
# from tfc.utils.functions import base36encode, random_alphanumeric


class SAMLMetadataModel(BaseModel):
    IDENTITY_AWS = 1
    IDENTITY_OKTA = 2
    IDENTITY_GOOGLE = 3
    IDENTITY_CHOICES = (
        (
            IDENTITY_AWS,
            "AWS",
        ),
        (
            IDENTITY_OKTA,
            "OKTA",
        ),
        (
            IDENTITY_GOOGLE,
            "Google",
        ),
    )

    id = models.CharField(
        max_length=100, primary_key=True, default=uuid.uuid4, editable=False
    )
    name = models.CharField(max_length=250, default=None, blank=True, null=True)
    # meta = CompressedTextField(compress_level=9)  # Range from 0 to 9, 9 being the highest compression.
    identity_type = models.PositiveSmallIntegerField(choices=IDENTITY_CHOICES)
    relay_state = models.CharField(max_length=100, unique=True, null=False, blank=False)
    is_enabled = models.BooleanField(default=False)
    security_generation = models.IntegerField(default=1)
    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, unique=True
    )
    meta = models.TextField(blank=True, help_text="To store content of XML file.")

    # objects = QueryMixin('saml_meta').as_manager()

    class Meta:
        db_table = "saml_meta"
        verbose_name = _("SAML Meta")
        verbose_name_plural = _("SAML Meta")
        ordering = ("-created_at",)

    @property
    def get_identity_type(self):
        return dict(self.IDENTITY_CHOICES).get(self.identity_type)

    # def save(self, **kwargs):
    #     if not self.uuid:
    #         """Base 36 of current timestamp with some random char of 4 digits to make it unique always."""
    #         self.uuid = base36encode(int(timezone.now().strftime('%s'))).lower() + random_alphanumeric(4)
    #     super(SAMLMetadataModel, self).save(**kwargs)

    @staticmethod
    def get_attributes(identity_type) -> list:
        # if identity_type == SAMLMetadataModel.IDENTITY_AWS:
        #     return ['email', 'name', ]
        if identity_type == SAMLMetadataModel.IDENTITY_OKTA:
            return [
                "email",
                "first_name",
                "last_name",
            ]
        elif identity_type == SAMLMetadataModel.IDENTITY_GOOGLE:
            return [
                "email",
                "first_name",
                "last_name",
            ]


class SamlLoginAttempt(models.Model):
    """A single browser-bound SP-initiated SAML login attempt."""

    class State(models.TextChoices):
        PENDING = "pending"
        CLAIMED = "claimed"
        CONSUMED = "consumed"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    relay_key = models.CharField(max_length=64, unique=True)
    request_id = models.CharField(max_length=128, unique=True)
    idp = models.ForeignKey(SAMLMetadataModel, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    user_hint = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="saml_login_attempts",
    )
    binder_hash = models.CharField(max_length=64)
    next_path = models.CharField(max_length=512)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    idp_meta_sha256 = models.CharField(max_length=64, blank=True, default="")
    idp_generation = models.IntegerField(default=1)
    candidate_count = models.PositiveSmallIntegerField(default=0)
    deny_reason = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        db_table = "saml_login_attempt"
        indexes = [
            models.Index(fields=["state", "expires_at"]),
            models.Index(fields=["user_hint", "created_at"]),
            models.Index(fields=["expires_at"]),
        ]


class SamlResponseCandidate(models.Model):
    """A short-lived raw SAML response, readable only during TX-A."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    candidate_key = models.CharField(max_length=64, unique=True)
    attempt = models.ForeignKey(SamlLoginAttempt, on_delete=models.CASCADE)
    payload = models.BinaryField()
    payload_bytes = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "saml_response_candidate"
        indexes = [
            models.Index(fields=["attempt", "expires_at"]),
            models.Index(fields=["expires_at"]),
        ]
