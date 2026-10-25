import copy
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import LiteLlmModelProvider
from model_hub.services import key_at_rest
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


class KeysAtRestMixin:
    """Stores a model's key fields encrypted at rest (see
    ``model_hub.services.key_at_rest``) and decrypts them only on use.

    A row remembers the stored values it loaded (``from_db``,
    ``refresh_from_db``) and last saved. On save a value unchanged since then
    keeps its ciphertext (an old-encoding one is upgraded); any other value is
    one a caller set and is encrypted, whatever it looks like. Reads go through
    ``_clear``: a stored value is decrypted, a value set in memory and not yet
    saved is already clear.
    """

    secret_fields = ()

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._remember_stored(cls.secret_fields)
        return instance

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        names = self.secret_fields if fields is None else fields
        self._remember_stored([n for n in names if n in self.secret_fields])

    def _stored_snapshot(self):
        return self.__dict__.setdefault("_keys_at_rest_loaded", {})

    def _remember_stored(self, names):
        snapshot = self._stored_snapshot()
        for name in names:
            if name in self.__dict__:  # a deferred field was not loaded
                snapshot[name] = copy.deepcopy(self.__dict__[name])

    def _clear(self, name, what):
        """The clear value of a key field; ``KeyUnreadable`` when it cannot be
        decrypted."""
        value = getattr(self, name)
        return key_at_rest.reveal_tree(
            value, what, loaded=self._stored_snapshot().get(name)
        )

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        deferred = self.get_deferred_fields()
        names = [
            n
            for n in self.secret_fields
            if n not in deferred and (update_fields is None or n in update_fields)
        ]
        before = {n: getattr(self, n) for n in names}
        snapshot = self._stored_snapshot()
        for name, value in before.items():
            if value:
                setattr(self, name, key_at_rest.encrypt_tree(value, snapshot.get(name)))
        try:
            self.full_clean()
            super().save(*args, **kwargs)
        except BaseException:
            for name, value in before.items():
                setattr(self, name, value)
            raise
        self._remember_stored(names)
        if any(before.values()):
            # Without a stable key the key was stored in the old encoding: say
            # so and how to fix it (never the key, never the variable's value).
            key_at_rest.warn_if_not_encrypted(
                model=type(self).__name__, row_id=str(self.pk)
            )


class ApiKey(KeysAtRestMixin, BaseModel):
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
    # ``enc::`` plus Fernet ciphertext; text, because the ciphertext of
    # a key of the 2500 characters the API accepts is longer than that.
    key = models.TextField(null=True, blank=True)
    # The same, for every string of a JSON key.
    config_json = models.JSONField(null=True, blank=True)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, default=None
    )

    secret_fields = ("key", "config_json")

    @property
    def _what(self):
        return f"{self.provider or 'provider'} key"

    @property
    def actual_key(self):
        """The clear key, for a provider call only; ``KeyUnreadable`` (a
        ``ValueError``) when this deployment cannot decrypt it."""
        return self._clear("key", self._what) if self.key else None

    @property
    def actual_json(self):
        """The clear JSON key, for a provider call only; ``KeyUnreadable`` when
        any of it cannot be decrypted."""
        return self._clear("config_json", self._what) if self.config_json else {}

    @property
    def masked_actual_key(self):
        """What every response shows; never the key or its ciphertext, and a
        placeholder for a key that cannot be decrypted."""
        for name in ("key", "config_json"):
            value = getattr(self, name)
            if value:
                return key_at_rest.mask(value, self._stored_snapshot().get(name))
        return None

    def clean(self):
        super().clean()
        validate_model_provider_choice(self.provider)


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


class SecretModel(KeysAtRestMixin, BaseModel):
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

    # ``enc::`` plus Fernet ciphertext, like ApiKey.key.
    key = models.TextField(null=True, blank=True)

    secret_fields = ("key",)

    @property
    def actual_key(self):
        """The clear secret, for the call that uses it only; ``KeyUnreadable``
        (a ``ValueError``) when this deployment cannot decrypt it."""
        return self._clear("key", f"secret {self.name!r}") if self.key else None

    @property
    def masked_key(self):
        """What the secrets list shows: the first and last four characters around
        bullets (only the first two for a secret of eight or fewer), a
        placeholder for a secret that cannot be decrypted, never the secret
        or its ciphertext."""
        try:
            clear = self.actual_key
        except key_at_rest.KeyUnreadable:
            return key_at_rest.UNREADABLE_MASK
        if not clear:
            return None
        if len(clear) <= 8:
            return clear[:2] + "•" * max(len(clear) - 2, 4)
        return clear[:4] + "•" * (len(clear) - 8) + clear[-4:]

    def clean(self):
        super().clean()
        validate_secret_type(self.secret_type)

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
