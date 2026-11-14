import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class EvidenceImport(BaseModel):
    """Observe sessions or calls a person chose to turn into tests of one
    environment. The ids are frozen when the import is made. What they hold is
    read again on every view, so evidence deleted later reads as unavailable
    instead of living on here."""

    class SourceKind(models.TextChoices):
        SESSION = "session", "Session"
        CALL = "call", "Call"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    environment = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.CASCADE,
        related_name="evidence_imports",
    )
    project = models.ForeignKey(
        "tracer.Project", on_delete=models.CASCADE, related_name="+"
    )
    # The environment's agent version when the import was made.
    agent_version = models.ForeignKey(
        "simulate.AgentVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source_kind = models.CharField(max_length=16, choices=SourceKind.choices)
    # The throwaway job whose builder chat is drafting cases now; it and its chat
    # are deleted once the turn settles.
    drafting_job = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evidence_drafting",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_evidence_import"
        indexes = [
            models.Index(
                fields=["environment", "-created_at"],
                name="idx_evidence_import_env",
            ),
        ]


class EvidenceImportCase(BaseModel):
    """One session or call of an import: its id, what it held when the import
    was made, and what became of it."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        UNAVAILABLE = "unavailable", "Unavailable"
        DRAFTED = "drafted", "Drafted"
        BLOCKED = "blocked", "Blocked"
        ADDED = "added", "Added to the suite"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evidence_import = models.ForeignKey(
        EvidenceImport, on_delete=models.CASCADE, related_name="cases"
    )
    # Where the person put it in the selection.
    position = models.PositiveSmallIntegerField()
    source_id = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    reason = models.CharField(max_length=255, blank=True, default="")
    # SHA-256 of what the source held when the import was made; blank when it
    # held nothing the project still keeps.
    digest = models.CharField(max_length=64, blank=True, default="")
    # The scenario the builder drafted for the case: its key and title, its
    # folder (suite files only, never the environment's) and the environment it
    # was proved on.
    scenario_key = models.CharField(max_length=255, blank=True, default="")
    scenario_name = models.CharField(max_length=255, blank=True, default="")
    proposal_object_key = models.CharField(max_length=512, blank=True, default="")
    environment_hash = models.CharField(max_length=128, blank=True, default="")
    # Checks the builder added to the environment's catalogue for this scenario,
    # as catalogue entries (ALK's SubGoal): new names only, never an edit.
    new_sub_goals = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "simulate_evidence_import_case"
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=["evidence_import", "source_id"],
                name="uniq_evidence_import_case_source",
            ),
        ]
