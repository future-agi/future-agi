import uuid
from django.db import models
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class SourceBook(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255, blank=True, default="")
    publication_year = models.IntegerField(null=True, blank=True)
    gallica_ark = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="source_books",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="source_books",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "sources_source_book"
        verbose_name = "Source Book"
        verbose_name_plural = "Source Books"

    def __str__(self):
        return f"{self.title} ({self.author})" if self.author else self.title
