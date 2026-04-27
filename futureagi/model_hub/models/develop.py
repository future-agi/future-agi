import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class DevelopAI(BaseModel):
    class DevelopType(models.TextChoices):
        RAG = "rag", "RAG"
        PROMPT = "prompt", "Prompt"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    develop_type = models.CharField(max_length=10, choices=DevelopType.choices)
    knowledge_base = models.ForeignKey("KnowledgeBase", on_delete=models.CASCADE)
    unique_data_columns = ArrayField(
        models.CharField(max_length=255), blank=True, null=True
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="develop_ais"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="develop_ais",
        null=True,
        blank=True,
    )
