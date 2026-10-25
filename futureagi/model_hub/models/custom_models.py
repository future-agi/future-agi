import uuid

from django.db import models

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import KeysAtRestMixin, validate_model_provider_choice
from model_hub.services import key_at_rest
from tfc.utils.base_model import BaseModel


class CustomAIModel(KeysAtRestMixin, BaseModel):
    class EnvTypes(models.TextChoices):
        PRODUCTION = "Production", "Production"
        TRAINING = "Training", "Training"
        VALIDATION = "Validation", "Validation"
        CORPUS = "Corpus", "Corpus"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    user_model_id = models.CharField(max_length=255, null=False)
    deleted = models.BooleanField(default=False)
    # Every string ``enc::`` plus Fernet ciphertext.
    key_config = models.JSONField(null=True, blank=True)
    provider = models.CharField(
        max_length=50, validators=[validate_model_provider_choice], null=False
    )
    input_token_cost = models.FloatField(null=False)
    output_token_cost = models.FloatField(null=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    default_metric = models.ForeignKey(
        "Metric",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_defaulted_by",
    )
    baseline_model_environment = models.CharField(
        max_length=100,
        choices=EnvTypes.choices,
        default=None,
        blank=True,
        null=True,
    )
    baseline_model_version = models.CharField(
        max_length=255, default=None, blank=True, null=True
    )

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="custom_ai_models"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="custom_ai_models",
        null=True,
        blank=True,
    )

    secret_fields = ("key_config",)

    @property
    def actual_json(self):
        """The clear credentials, for a provider call only; ``KeyUnreadable``
        (a ``ValueError``) when this deployment cannot decrypt them."""
        if not self.key_config:
            return {}
        return self._clear("key_config", f"key of custom model {self.user_model_id}")

    @property
    def masked_json(self):
        """What every response shows: the masked credentials, a placeholder for
        a value that cannot be decrypted, never a key or its ciphertext."""
        if not self.key_config:
            return {}
        return key_at_rest.mask(
            self.key_config, self._stored_snapshot().get("key_config")
        )

    class Meta:
        unique_together = ["organization", "user_model_id", "deleted"]

    def __str__(self):
        return str(self.id)
