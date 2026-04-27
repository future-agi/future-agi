import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class ModelChoices(models.TextChoices):
    TURING_LARGE = "turing_large", "Turing Large"
    TURING_SMALL = "turing_small", "Turing Small"
    PROTECT = "protect", "Protect"
    PROTECT_FLASH = "protect_flash", "Protect Flash"
    TURING_FLASH = "turing_flash", "Turing Flash"


class EvalOutputType(models.TextChoices):
    PASS_FAIL = "Pass/Fail", "Pass/Fail"
    SCORE = "score", "Score"
    CHOICES = "choices", "Choices"


class CustomEvalConfig(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_template = models.ForeignKey(
        EvalTemplate,
        on_delete=models.CASCADE,
        related_name="custom_eval_configs",
        blank=False,
        null=False,
    )
    name = models.CharField(max_length=255, blank=True, null=True)
    config = models.JSONField(default=dict, blank=True, null=True)
    mapping = models.JSONField(default=dict, blank=True, null=True)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="custom_eval_configs"
    )
    filters = models.JSONField(default=dict, blank=True, null=True)
    error_localizer = models.BooleanField(default=False)
    kb_id = models.ForeignKey(
        KnowledgeBaseFile, on_delete=models.CASCADE, null=True, blank=True
    )
    model = models.CharField(
        max_length=255, choices=ModelChoices.choices, blank=True, null=True
    )
    eval_group = models.ForeignKey(
        EvalGroup, on_delete=models.CASCADE, null=True, blank=True
    )

    def __str__(self):
        return f"Custom Eval Config {self.id}"

    class Meta:
        db_table = "tracer_custom_eval_config"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "project"],
                condition=models.Q(deleted=False),
                name="unique_name_project_idx",
            )
        ]
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["eval_template"]),
            models.Index(fields=["project", "name"]),
        ]


class InLineEvalStatus(models.TextChoices):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class InlineEval(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="inline_evals",
        null=True,
        blank=True,
    )
    span_id = models.CharField(max_length=255)
    custom_eval_name = models.CharField(max_length=255)
    evaluation = models.ForeignKey(
        Evaluation,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="inline_evals",
    )
    status = models.CharField(
        max_length=255,
        choices=InLineEvalStatus.choices,
        default=InLineEvalStatus.PENDING,
    )

    class Meta:
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["span_id"]),
            models.Index(fields=["status", "span_id"]),
        ]
