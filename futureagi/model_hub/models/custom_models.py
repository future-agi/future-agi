import uuid

from django.db import models

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.api_key import ApiKey, validate_model_provider_choice
from tfc.utils.base_model import BaseModel


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

    @property
    def actual_json(self):
        return self._actual_json

    class Meta:
        unique_together = ["organization", "user_model_id", "deleted"]

    def __str__(self):
        return str(self.id)
