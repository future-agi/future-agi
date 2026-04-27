import uuid

from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class KnowledgeBase(BaseModel):
    class EmbeddingModelChoices(models.TextChoices):
        BGE_SMALL_EN_1_5 = "BAAI/bge-small-en-v1.5", "BAAI/bge-small-en-v1.5"
        # Add more embedding model choices as needed

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=2000)
    embedding_model = models.CharField(
        max_length=50,
        choices=EmbeddingModelChoices.choices,
        default=EmbeddingModelChoices.BGE_SMALL_EN_1_5,
    )
    chunk_size = models.PositiveIntegerField()
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="knowledge_bases",
        default=1,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="knowledge_bases",
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.name
