"""Evidence of what an agent and its environment support, as ALK wrote it. Rows are immutable: the status shown is
computed from them at read time, never stored on them."""

from __future__ import annotations

import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class HarnessCapabilityEvidence(BaseModel):
    """One capture of capability evidence, from a build's certification, an agent preflight or a run."""

    class Origin(models.TextChoices):
        PREFLIGHT = "preflight", "Preflight"
        CERTIFICATION = "certification", "Certification"
        STRUCTURAL = "structural", "Structural"
        RUN = "run", "Run"

    class CaptureStatus(models.TextChoices):
        COMPLETE = "complete", "Complete"
        ERRORED = "errored", "Errored"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    workspace = models.ForeignKey(Workspace, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    agent_version = models.ForeignKey(
        "simulate.AgentVersion", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    environment_version = models.ForeignKey(
        "simulate.HarnessEnvironmentVersion", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    job = models.ForeignKey(
        "simulate.HostedHarnessJob", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    origin = models.CharField(max_length=16, choices=Origin.choices)
    capture_status = models.CharField(max_length=16, choices=CaptureStatus.choices, default=CaptureStatus.COMPLETE)
    capture_error = models.TextField(null=True, blank=True)
    catalog_version = models.CharField(max_length=16)
    # The catalogue the evidence was written against, in order: id, label, depends_on, fix_type.
    catalogue = models.JSONField(default=list, blank=True)
    inputs = models.JSONField(default=dict, blank=True)
    capabilities = models.JSONField(default=dict, blank=True)
    artefacts = models.JSONField(default=list, blank=True)
    # Per scenario key, what it needs (capabilities, tools, sub-goal evidence), as the build sealed it.
    scenario_requirements = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    captured_at = models.DateTimeField()

    class Meta:
        db_table = "simulate_harness_capability_evidence"
        constraints = [
            models.UniqueConstraint(fields=["job", "origin"], name="capability_evidence_one_per_job_origin"),
        ]
        indexes = [
            models.Index(
                fields=["agent_version", "environment_version", "-captured_at"],
                name="capability_evidence_pair_idx",
            ),
        ]
