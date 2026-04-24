import base64
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import LiteLlmModelProvider
from tfc.settings import settings
from tfc.utils.base_model import BaseModel


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


class ApiKey(BaseModel):
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
    key = models.CharField(max_length=2500, null=True, blank=True)
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

    def encrypt_key(self, key):
        if not key:
            return None
        # Use a secret salt from settings
        salt = settings.SECRET_KEY[:16].encode()
        # Combine salt and key, then encode
        salted = salt + key.encode()
        encoded = base64.b64encode(salted).decode()
        return encoded

    def decrypt_key(self):
        if not self.key:
            return None
        try:
            # Decode the stored value
            decoded = base64.b64decode(self.key)
            # Remove the salt (first 16 bytes)
            actual_key = decoded[16:].decode()
            return actual_key
        except Exception:
            return None

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
        if self.key and not self.key.startswith(b"gAAAAA".decode()):
            self._actual_key = self.key
            self.key = self.encrypt_key(self.key)
        if self.config_json and not all(
            v.startswith(b"gAAAAA".decode()) for v in self.config_json.values()
        ):
            self._actual_json = self.config_json
            self.config_json = self.encrypt_json(self.config_json)
        self.full_clean()
        super().save(*args, **kwargs)

    def encrypt_json(self, config_json):
        encrypted_json = {}
        for key in config_json.keys():
            encrypted_json[key] = self._encrypt_value(config_json[key])
        return encrypted_json

    def _encrypt_value(self, value):
        """Recursively encrypt values, handling nested structures"""
        if isinstance(value, dict):
            # Recursively encrypt nested dictionaries
            encrypted_dict = {}
            for k, v in value.items():
                encrypted_dict[k] = self._encrypt_value(v)
            return encrypted_dict
        elif isinstance(value, list):
            # Recursively encrypt nested lists
            return [self._encrypt_value(item) for item in value]
        elif isinstance(value, str):
            # Only encrypt string values
            return self.encrypt_key(value)
        else:
            # For other types (int, float, bool, etc.), return as-is
            return value

    def get_decrypted_json_key(self, json_key):
        if not json_key:
            return None
        try:
            # Decode the stored value
            decoded = base64.b64decode(json_key)
            # Remove the salt (first 16 bytes)
            actual_key = decoded[16:].decode()
            return actual_key
        except Exception:
            return None

    def decrypt_json(self, json_key=None):
        actual_key = {}
        if not json_key:
            json_key = self.config_json
        if json_key is not None:
            for key in json_key.keys():
                key_val = self._decrypt_value(json_key[key])
                actual_key.update({key: key_val})
        return actual_key

    def _decrypt_value(self, value):
        """Recursively decrypt values, handling nested structures"""
        if isinstance(value, dict):
            # Recursively decrypt nested dictionaries
            decrypted_dict = {}
            for k, v in value.items():
                decrypted_dict[k] = self._decrypt_value(v)
            return decrypted_dict
        elif isinstance(value, list):
            # Recursively decrypt nested lists
            return [self._decrypt_value(item) for item in value]
        elif isinstance(value, str):
            # Only decrypt encrypted string values
            return self.get_decrypted_json_key(value)
        else:
            # For other types (int, float, bool, etc.), return as-is
            return value


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


class SecretModel(BaseModel):
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

    key = models.CharField(max_length=2500, null=True, blank=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._actual_key = None
        if self.key:
            self._actual_key = self.decrypt_key()

    def encrypt_key(self, key):
        if not key:
            return None
        # Use a secret salt from settings
        salt = settings.SECRET_KEY[:16].encode()
        # Combine salt and key, then encode
        salted = salt + key.encode()
        encoded = base64.b64encode(salted).decode()
        return encoded

    def decrypt_key(self):
        if not self.key:
            return None
        try:
            # Decode the stored value
            decoded = base64.b64decode(self.key)
            # Remove the salt (first 16 bytes)
            actual_key = decoded[16:].decode()
            return actual_key
        except Exception:
            return None

    @property
    def actual_key(self):
        return self._actual_key

    def clean(self):
        super().clean()
        validate_secret_type(self.secret_type)

    def save(self, *args, **kwargs):
        if self.key and not self.key.startswith(b"gAAAAA".decode()):
            self._actual_key = self.key
            self.key = self.encrypt_key(self.key)
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
