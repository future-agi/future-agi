"""Environment releases, suite releases, agent bindings and run manifests.

`api_contracts/harness/environment-releases.md`. Every row is content-addressed and insert-only:
the digest is the identity, computed by ALK for releases and by `canonical_digest` for bindings
and manifests. A release with no organization is a system release any organization may use.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class HarnessEnvironmentRelease(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="harness_environment_releases",
    )
    digest = models.CharField(max_length=71)
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="extensions",
    )
    object_key = models.CharField(max_length=1024)
    manifest = models.JSONField()
    tools = models.JSONField(default=list)
    policy = models.JSONField(default=dict)

    class Meta:
        db_table = "simulate_harness_environment_release"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "digest"], name="harness_env_release_org_digest"
            ),
            models.UniqueConstraint(
                fields=["digest"],
                condition=Q(organization__isnull=True),
                name="harness_env_release_system_digest",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} {self.digest[:19]}"


class HarnessSuiteRelease(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="harness_suite_releases",
    )
    digest = models.CharField(max_length=71)
    name = models.CharField(max_length=255)
    environment = models.ForeignKey(
        HarnessEnvironmentRelease, on_delete=models.PROTECT, related_name="suites"
    )
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="derived"
    )
    object_key = models.CharField(max_length=1024)
    manifest = models.JSONField()
    # The scenario records the platform creates for a composition (`provision.json`).
    personas = models.JSONField(default=list)

    class Meta:
        db_table = "simulate_harness_suite_release"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "digest"],
                name="harness_suite_release_org_digest",
            ),
            models.UniqueConstraint(
                fields=["digest"],
                condition=Q(organization__isnull=True),
                name="harness_suite_release_system_digest",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} {self.digest[:19]}"


class HarnessAgentBinding(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="harness_agent_bindings"
    )
    digest = models.CharField(max_length=71)
    agent_version = models.ForeignKey(
        "simulate.AgentVersion",
        on_delete=models.PROTECT,
        related_name="harness_bindings",
    )
    environment = models.ForeignKey(
        HarnessEnvironmentRelease, on_delete=models.PROTECT, related_name="bindings"
    )
    mode = models.CharField(max_length=32)
    document = models.JSONField()

    class Meta:
        db_table = "simulate_harness_agent_binding"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "digest"], name="harness_binding_org_digest"
            )
        ]


class HarnessRunManifest(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="harness_run_manifests"
    )
    digest = models.CharField(max_length=71)
    document = models.JSONField()
    job = models.OneToOneField(
        "simulate.HostedHarnessJob",
        on_delete=models.PROTECT,
        related_name="run_manifest",
    )

    class Meta:
        db_table = "simulate_harness_run_manifest"
        indexes = [models.Index(fields=["organization", "digest"])]
