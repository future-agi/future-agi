import uuid

from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class CodeArchive(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    repository = models.CharField(max_length=255)
    commit_sha = models.CharField(max_length=40)
    object_key = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.BigIntegerField()

    class Meta:
        db_table = "simulate_code_archive"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "repository", "commit_sha"],
                name="uniq_code_archive_commit",
            ),
        ]
