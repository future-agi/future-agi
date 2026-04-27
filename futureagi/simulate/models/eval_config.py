import uuid

from django.db import models

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.base_model import BaseModel
from tracer.models.custom_eval_config import ModelChoices

from .run_test import RunTest


class SimulateEvalConfig(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_template = models.ForeignKey(
        EvalTemplate,
        on_delete=models.CASCADE,
        related_name="simulate_eval_configs",
        blank=False,
        null=False,
    )
    name = models.CharField(max_length=255, blank=True, null=True)
    config = models.JSONField(default=dict, blank=True, null=True)
    mapping = models.JSONField(default=dict, blank=True, null=True)
    run_test = models.ForeignKey(
        RunTest, on_delete=models.CASCADE, related_name="simulate_eval_configs"
    )
    filters = models.JSONField(default=dict, blank=True, null=True)
    error_localizer = models.BooleanField(default=False)
    kb_id = models.ForeignKey(
        KnowledgeBaseFile, on_delete=models.CASCADE, null=True, blank=True
    )
    model = models.CharField(
        max_length=255, choices=ModelChoices.choices, blank=True, null=True
    )
    status = models.CharField(
        max_length=50,
        choices=StatusType.get_choices(),
        default=StatusType.COMPLETED.value,
    )

    eval_group = models.ForeignKey(
        EvalGroup, on_delete=models.CASCADE, null=True, blank=True
    )

    def __str__(self):
        return f"Run Test Eval Config {self.id}"

    class Meta:
        db_table = "simulate_eval_config"
        verbose_name = "Simulate Eval Config"
        verbose_name_plural = "Simulate Eval Configs"
        ordering = ["-created_at"]
