"""The workspace tag registry and the tags put on experiments.

A workspace keeps one list of tags. A tag's name is unique in its workspace
compared as `name_key` (trimmed, inner whitespace collapsed, lower case), as
the page compares it. Deleting a tag removes it from every experiment.
"""

import uuid

from django.conf import settings
from django.db import models

# The page's eight colour tokens (frontend tagModel.js TAG_PALETTE), in the
# order new tags take them.
TAG_COLORS = ("blue", "green", "amber", "violet", "red", "teal", "pink", "slate")
TAG_NAME_MAX = 64
TAG_DESCRIPTION_MAX = 500


def normalise_tag_name(name):
    """Trim and collapse inner whitespace, as the page does."""
    return " ".join(str(name).split())


def tag_name_key(name):
    """A name as compared: normalised, lower case."""
    return normalise_tag_name(name).lower()


class WorkspaceTag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace", on_delete=models.CASCADE, related_name="+"
    )
    name = models.CharField(max_length=TAG_NAME_MAX)
    name_key = models.CharField(max_length=TAG_NAME_MAX)
    color = models.CharField(max_length=16)
    description = models.TextField(blank=True, default="", max_length=TAG_DESCRIPTION_MAX)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "model_hub_workspace_tag"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name_key"], name="workspace_tag_name_unique"
            )
        ]


class ExperimentTag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tag = models.ForeignKey(
        WorkspaceTag, on_delete=models.CASCADE, related_name="experiment_links"
    )
    experiment = models.ForeignKey(
        "model_hub.ExperimentsTable", on_delete=models.CASCADE, related_name="tag_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "model_hub_experiment_tag"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "tag"], name="experiment_tag_unique"
            )
        ]
