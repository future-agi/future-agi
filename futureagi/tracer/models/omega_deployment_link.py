"""The Observe project an Omega deployment or Console agent reports into (SPEC 5.3).

One concern: mapping an EE id to its Observe project in the workspace that owns it,
and remembering what the link set up there. Writes happen in
``tracer.services.omega_deployments.ensure``. Two kinds share the one mechanism
(learning-service PLAN section 1, stage 1; ruling 158.2):

- ``deployment``: an EE deployment id to ``deploy/<name>``. A rename moves the link
  to the new name's project.
- ``agent``: an EE Console agent id to ``agent/<slug>-<shortid>``. The project is
  named once, when the link is made, and keeps that name: a rename only updates
  ``agent_name``, so the history stays on one project (trace ingest finds projects
  by name, so a renamed project would split a running agent's spans).

Each live EE id is linked once across every workspace. Exactly one of the two id
columns is set, as the kind says.
"""

import uuid

from django.db import models

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from tracer.models.monitor import UserAlertMonitor
from tracer.models.project import Project


class ObserveLinkKind(models.TextChoices):
    DEPLOYMENT = "deployment", "Deployment"
    AGENT = "agent", "Console agent"


class OmegaDeploymentObserveLink(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="omega_deployment_observe_links",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="omega_deployment_observe_links",
    )
    kind = models.CharField(
        max_length=20,
        choices=ObserveLinkKind.choices,
        default=ObserveLinkKind.DEPLOYMENT,
    )
    # The control plane's id (``dep-`` and 16 hex digits today). A deployment
    # belongs to one workspace, so a live id is unique across all of them.
    ee_deployment_id = models.CharField(max_length=255, null=True, blank=True)
    deployment_name = models.CharField(max_length=40, blank=True, default="")
    # The control plane's Console agent id (``workforce_agent_<uuid>`` today). An
    # agent belongs to one workspace too.
    ee_agent_id = models.CharField(max_length=255, null=True, blank=True)
    # The agent's current name as EE last sent it; cleared when the link is removed.
    agent_name = models.CharField(max_length=200, blank=True, default="")
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="omega_deployment_observe_links",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    errors_monitor = models.ForeignKey(
        UserAlertMonitor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    daily_tokens_monitor = models.ForeignKey(
        UserAlertMonitor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # When the default monitors were set up on ``project``. A monitor the user
    # deletes afterwards stays deleted; a new project starts this over.
    alerts_created_at = models.DateTimeField(null=True, blank=True)
    # How many ensures found no room on the plan for the defaults, and when the last
    # did (``ensure.monitors``). Kept once the pair is created, as the record.
    alerts_deferred_count = models.PositiveIntegerField(default=0)
    alerts_last_deferred_at = models.DateTimeField(null=True, blank=True)
    # Agent links: when mistake analysis (the project's ``TraceScanConfig``) was set
    # up on ``project`` (``ensure.mistakes``). A rate the user changes afterwards in
    # the project's settings stays as the user left it; a new project starts over.
    scan_configured_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_omega_deployment_observe_link"
        constraints = [
            models.UniqueConstraint(
                fields=["ee_deployment_id"],
                condition=models.Q(deleted=False),
                name="unique_live_omega_deployment_observe_link",
            ),
            models.UniqueConstraint(
                fields=["ee_agent_id"],
                condition=models.Q(deleted=False),
                name="unique_live_omega_agent_observe_link",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind=ObserveLinkKind.DEPLOYMENT,
                        ee_deployment_id__isnull=False,
                        ee_agent_id__isnull=True,
                    )
                    | models.Q(
                        kind=ObserveLinkKind.AGENT,
                        ee_agent_id__isnull=False,
                        ee_deployment_id__isnull=True,
                    )
                ),
                name="omega_observe_link_one_ee_id_per_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "project"]),
        ]

    @property
    def ee_id(self) -> str:
        """The EE id this link maps, whichever kind it is."""
        if self.kind == ObserveLinkKind.AGENT:
            return self.ee_agent_id or ""
        return self.ee_deployment_id or ""

    def __str__(self):
        return f"{self.kind}:{self.ee_id} -> {self.project_id}"
