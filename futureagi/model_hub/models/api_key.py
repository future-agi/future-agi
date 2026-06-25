import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from integrations.services.credentials import CredentialManager
from model_hub.models.choices import LiteLlmModelProvider
from tfc.utils.base_model import BaseModel


class EncryptedSecretMixin:
    """Encrypt-on-save for models holding a single secret in ``key``.

    One construction site for the key crypto — ApiKey and SecretModel both reuse
    it instead of re-implementing the encrypt / legacy-upgrade block. All crypto
    lives in ``CredentialManager`` (the single secret interface); these methods are
    thin, typed delegations kept for existing callers/serializers."""

    def encrypt_key(self, key):
        return CredentialManager.encrypt_secret(key)

    def decrypt_key(self):
        return CredentialManager.decrypt_secret(self.key)

    def is_encrypted_key(self, key):
        return CredentialManager.is_encrypted(key)

    def _store_key(self):
        """Canonicalize ``self.key`` for storage; cache plaintext on ``_actual_key``.
        Upgrades a legacy row to Fernet in place and never double-encrypts."""
        self.key, self._actual_key = CredentialManager.prepare_secret_for_storage(
            self.key
        )


def validate_model_provider_choice(value):
    valid_choices = [choice.value for choice in LiteLlmModelProvider]
    if value not in valid_choices and not value == "custom":
        raise ValidationError(
            f"Invalid provider choice. Valid choices are: {', '.join(valid_choices)}"
        )


def mask_key(key):
    if isinstance(key, str):
        if key:
            if len(key) <= 8:
                visible_chars = min(4, len(key))
                return key[:visible_chars] + "*" * max(len(key) - visible_chars, 4)
            return key[:4] + "*" * min(max(len(key) - 8, 0), 10) + key[-4:]
        return key
    elif isinstance(key, dict):
        masked_json = {}
        for key_id, value in key.items():
            if isinstance(value, str) and value:
                masked_json[key_id] = value[0:4] + "*" * (6) + value[-4:]
            elif isinstance(value, dict):
                # Recursively mask nested dictionaries
                masked_json[key_id] = mask_key(value)
            elif isinstance(value, list):
                # Recursively mask nested lists
                masked_json[key_id] = [mask_key(item) for item in value]
            else:
                # For other types, keep as-is
                masked_json[key_id] = value
        return masked_json
    elif isinstance(key, list):
        # Handle lists by masking each item
        return [mask_key(item) for item in key]
    else:
        # For other types, return as-is
        return key


class ApiKey(EncryptedSecretMixin, BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="api_key_org",
        blank=True,
        null=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="api_keys",
        null=True,
        blank=True,
    )
    provider = models.CharField(
        max_length=50, validators=[validate_model_provider_choice]
    )
    key = models.TextField(null=True, blank=True)
    config_json = models.JSONField(null=True, blank=True)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, default=None
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._actual_key = None
        self._actual_json = {}
        if self.key:
            self._actual_key = self.decrypt_key()
        if self.config_json:
            self._actual_json = self.decrypt_json(self.config_json)

    @property
    def actual_key(self):
        return self._actual_key

    @property
    def actual_json(self):
        return self._actual_json

    @property
    def masked_actual_key(self):
        if not self.actual_key:
            if self.actual_json:
                return mask_key(self.actual_json)
            else:
                return None
        return mask_key(self.actual_key)

    def clean(self):
        super().clean()
        validate_model_provider_choice(self.provider)

    def save(self, *args, **kwargs):
        self._store_key()
        if self.config_json:
            self._actual_json = CredentialManager.decrypt_json(self.config_json)
            self.config_json = CredentialManager.encrypt_json(self.config_json)
        else:
            self._actual_json = {}
        self.full_clean()
        super().save(*args, **kwargs)

    def encrypt_json(self, config_json):
        return CredentialManager.encrypt_json(config_json)

    def decrypt_json(self, json_key=None):
        if not json_key:
            json_key = self.config_json
        return CredentialManager.decrypt_json(json_key) if json_key else {}

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        self._actual_key = self.decrypt_key() if self.key else None
        self._actual_json = (
            self.decrypt_json(self.config_json) if self.config_json else {}
        )


class SecretType(models.TextChoices):  # pragma: allowlist secret
    API_KEY = "API_KEY", "API Key"
    PASSWORD = "PASSWORD", "Password"  # pragma: allowlist secret
    TOKEN = "TOKEN", "Token"
    OTHER = "OTHER", "Other"


def validate_secret_type(value):
    valid_choices = [choice.value for choice in SecretType]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid secret type. Valid choices are: {', '.join(valid_choices)}"
        )


class SecretModel(EncryptedSecretMixin, BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="organization_secrets",
        blank=True,
        null=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="secrets",
        null=True,
        blank=True,
    )

    secret_type = models.CharField(
        max_length=50,
        choices=SecretType.choices,
        validators=[validate_secret_type],
        default=SecretType.OTHER,
    )

    key = models.TextField(null=True, blank=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._actual_key = None
        if self.key:
            self._actual_key = self.decrypt_key()

    @property
    def actual_key(self):
        return self._actual_key

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        self._actual_key = self.decrypt_key() if self.key else None

    def clean(self):
        super().clean()
        validate_secret_type(self.secret_type)

    def save(self, *args, **kwargs):
        self._store_key()
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        db_table = "secrets"
        ordering = ["-created_at"]
        unique_together = [
            "organization",
            "workspace",
            "name",
        ]  # Ensure unique names within an organization and workspace

    def __str__(self):
        return f"{self.name} ({self.organization.display_name if self.organization.display_name else self.organization.name if self.organization else 'No Org'})"
