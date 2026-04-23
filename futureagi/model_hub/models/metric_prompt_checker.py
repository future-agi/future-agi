import uuid

from django.db import models


class PromptChecker(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_prompt = models.TextField(default=None, null=True, blank=True)
    ai_prompt = models.TextField(default=None, null=True, blank=True)
    explanation = models.TextField(default=None, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    version = models.CharField(max_length=100, default="v1")
    model_name = models.CharField(
        max_length=100, default="anthropic/claude-3.5-sonnet:beta"
    )
    ambiguity = models.BooleanField(default=True)
    deleted = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    choices = models.JSONField(default=list, blank=True)
    multi_choice = models.BooleanField(default=False)

    def __str__(self):
        return str(self.id)
