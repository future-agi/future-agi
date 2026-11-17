import uuid

from django.db import models

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.base_model import BaseModel

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
    model = models.CharField(max_length=255, blank=True, null=True)
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


class SimulateEvalConfigRemovedKeys(models.Model):
    """What the evaluator-key clean-up took out of an eval attachment's nested config.

    A plain model: no soft delete, no foreign key (so creating the table takes no lock
    on ``simulate_eval_config``). ``removed`` holds the removed pairs except
    ``api_key`` and except keys whose value was ``None``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_config_id = models.UUIDField(db_index=True)
    removed = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    restored_keys = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "simulate_eval_config_removed_keys"
