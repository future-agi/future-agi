import uuid

from django.core.cache import cache
from django.db import models, transaction

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import ApiKey, validate_model_provider_choice
from tfc.utils.base_model import BaseModel


def custom_model_pricing_cache_key(organization_id, user_model_id) -> str:
    return f"custom_model_pricing:{organization_id}:{user_model_id}"


def invalidate_custom_model_pricing_cache(organization_id, *user_model_ids) -> None:
    """Drop cached pricing (including "not found") once the write commits."""
    keys = [
        custom_model_pricing_cache_key(organization_id, name)
        for name in user_model_ids
        if name
    ]
    # robust: a cache outage must not fail a write that already committed.
    transaction.on_commit(lambda: cache.delete_many(keys), robust=True)


class CustomAIModel(BaseModel):
    class EnvTypes(models.TextChoices):
        PRODUCTION = "Production", "Production"
        TRAINING = "Training", "Training"
        VALIDATION = "Validation", "Validation"
        CORPUS = "Corpus", "Corpus"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    user_model_id = models.CharField(max_length=255, null=False)
    deleted = models.BooleanField(default=False)
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._actual_json = {}
        if self.key_config:
            self._actual_json = ApiKey().decrypt_json(self.key_config)

    def save(self, *args, **kwargs):
        # A rename must also drop the pricing cached under the old name.
        previous_user_model_id = (
            type(self)
            ._base_manager.filter(pk=self.pk)
            .values_list("user_model_id", flat=True)
            .first()
        )
        # if self.key_config and not all(
        #     [v.startswith(b"gAAAAA".decode()) for v in self.key_config.values()]
        # ):
        if self.key_config:
            self._actual_json = self.key_config
            self.key_config = ApiKey().encrypt_json(self.key_config)
        else:
            self._actual_json = {}
        self.full_clean()
        super().save(*args, **kwargs)
        invalidate_custom_model_pricing_cache(
            self.organization_id, self.user_model_id, previous_user_model_id
        )

    @property
    def actual_json(self):
        return self._actual_json

    class Meta:
        unique_together = ["organization", "user_model_id", "deleted"]

    def __str__(self):
        return str(self.id)
