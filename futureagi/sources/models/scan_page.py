import uuid
from django.db import models
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from sources.models.source_book import SourceBook


class ScanPage(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    book = models.ForeignKey(
        SourceBook,
        on_delete=models.CASCADE,
        related_name="pages",
    )
    page_number = models.IntegerField()
    page_label = models.CharField(max_length=50, blank=True, default="")
    iiif_url = models.URLField(blank=True, default="")
    raw_ocr_text = models.TextField(blank=True, default="")
    ocr_metadata = models.JSONField(default=dict, blank=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="scan_pages",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="scan_pages",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "sources_scan_page"
        verbose_name = "Scan Page"
        verbose_name_plural = "Scan Pages"
        ordering = ["page_number"]

    def __str__(self):
        label = self.page_label or f"Page {self.page_number}"
        return f"{self.book.title} - {label}"
