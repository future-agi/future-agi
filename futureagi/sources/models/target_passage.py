import uuid
from django.db import models
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from sources.models.scan_page import ScanPage


class TargetPassage(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scan_page = models.ForeignKey(
        ScanPage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="passages",
    )
    text_content = models.TextField()
    modernized_text = models.TextField(blank=True, default="")
    line_range = models.CharField(max_length=50, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="target_passages",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="target_passages",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "sources_target_passage"
        verbose_name = "Target Passage"
        verbose_name_plural = "Target Passages"

    def __str__(self):
        snippet = self.text_content[:50]
        return f"Passage: {snippet}..." if len(self.text_content) > 50 else f"Passage: {self.text_content}"
