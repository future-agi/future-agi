import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class Prompt(BaseModel):
    class UsedIn(models.TextChoices):
        DEVELOP = "develop", "Develop"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    common_id = models.UUIDField(default=uuid.uuid4, editable=False)
    content = models.TextField()
    variables = models.JSONField(default=list)
    is_current = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    used_in = models.CharField(
        max_length=20, choices=UsedIn.choices, default=UsedIn.DEVELOP
    )
    develop = models.ForeignKey(
        "DevelopAI",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prompts",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Prompt {self.id} (v{self.version})"

    def save(self, *args, **kwargs):
        if not self.pk:  # New prompt
            super().save(*args, **kwargs)
        else:  # Existing prompt
            old_instance = Prompt.objects.get(pk=self.pk)
            if (
                old_instance.content != self.content
                or old_instance.variables != self.variables
            ):
                # Create a new version
                new_version = Prompt(
                    content=self.content,
                    variables=self.variables,
                    common_id=self.common_id,
                    is_current=True,
                    version=self.version + 1,
                    used_in=self.used_in,
                    develop=self.develop,
                )
                new_version.save()

                # Update the old version
                self.is_current = False
                super().save(*args, **kwargs)
            else:
                super().save(*args, **kwargs)

    def get_current_version(self):
        return Prompt.objects.filter(common_id=self.common_id, is_current=True).first()

    def get_version_history(self):
        return Prompt.objects.filter(common_id=self.common_id).order_by("-version")
