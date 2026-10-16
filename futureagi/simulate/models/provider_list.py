import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class ProviderList(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sdk_version = models.CharField(max_length=32, unique=True)
    document = models.JSONField()
    content_hash = models.CharField(max_length=71)
    last_published_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "simulate_provider_list"
