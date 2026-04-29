import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.base_model import BaseModel
from tracer.models.custom_eval_config import ModelChoices


class PlatformChoices(models.TextChoices):
    LANGFUSE = "langfuse"


class StatusChoices(models.TextChoices):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ExternalEvalConfig(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="external_eval_configs",
        null=True,
        blank=True,
    )

    eval_template = models.ForeignKey(EvalTemplate, on_delete=models.CASCADE)
    name = models.CharField(
        max_length=255, help_text="Name of the external eval config"
    )
    config = models.JSONField(default=dict, blank=True, null=True)
    mapping = models.JSONField(default=dict)
    model = models.CharField(
        max_length=255, choices=ModelChoices.choices, blank=True, null=True
    )

    eval_results = models.JSONField(default=dict, blank=True, null=True)
    error_message = models.TextField(null=True, blank=True)
    logs = models.JSONField(default=list, blank=True, null=True)

    platform = models.CharField(max_length=255, choices=PlatformChoices.choices)
    credentials = models.JSONField(default=dict)
    status = models.CharField(max_length=255, choices=StatusChoices.choices)

    @classmethod
    def get_required_credentials(cls, platform):
        """
        Returns the required credentials for a given platform.
        Override this method to add new platforms and their required credentials.
        """
        required_credentials = {
            PlatformChoices.LANGFUSE: [
                "langfuse_secret_key",
                "langfuse_public_key",
                "langfuse_host",
            ],
            # Add more platforms here as needed:
        }
        return required_credentials.get(platform, [])

    def clean(self):
        super().clean()
        if self.platform:
            required_keys = self.get_required_credentials(self.platform)
            if required_keys:
                missing_keys = [
                    key for key in required_keys if key not in self.credentials
                ]
                if missing_keys:
                    raise ValidationError(
                        {
                            "credentials": f"Missing required credentials for {self.platform} platform: {', '.join(missing_keys)}"
                        }
                    )

                invalid_value_keys = [
                    key for key in required_keys if not self.credentials.get(key)
                ]
                if invalid_value_keys:
                    raise ValidationError(
                        {
                            "credentials": f"Values for these credentials cannot be null or empty for {self.platform} platform: {', '.join(invalid_value_keys)}"
                        }
                    )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"External Eval Config {self.id}"

    class Meta:
        db_table = "tracer_external_eval_config"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["platform"]),
            models.Index(fields=["organization"]),
        ]
        ordering = ["-created_at"]
