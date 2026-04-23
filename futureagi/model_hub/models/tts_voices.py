import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class VoiceTypeChoices(models.TextChoices):
    SYSTEM = "system", "System"
    CUSTOM = "custom", "Custom"


class TTSVoice(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, validators=[MinLengthValidator(1)])
    description = models.TextField(max_length=255, blank=True, default="")

    voice_id = models.CharField(max_length=255, validators=[MinLengthValidator(1)])
    provider = models.CharField(max_length=255, validators=[MinLengthValidator(1)])
    model = models.CharField(max_length=255, validators=[MinLengthValidator(1)])
    voice_type = models.CharField(
        max_length=255,
        choices=VoiceTypeChoices.choices,
        default=VoiceTypeChoices.CUSTOM,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="tts_voices_org",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="tts_voices",
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.name
