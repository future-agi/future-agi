"""Mistake analysis on a Console agent's Observe project (learning-service PLAN section 1,
stage 2 "Mistakes by class"; OD5).

One concern: the project's ``TraceScanConfig``, set up once by the agent's ensure.

- The Error Feed engine claims a project only when its config is enabled, its rate is
  above 0 and its version is the engine's (``trace_investigation.claim_due_investigations``),
  and a config's default rate is 0. Without this step no agent run is ever analysed.
- Every analysis is billed (``charge_trace_investigation``) and Error Feed is a paid
  capability (``error_feed``: 402 without it). Whether Console agents get it, and at what
  price, is the owner's (OD5, packet 60). So the rate is set only when the organization
  already has ``error_feed``; otherwise nothing is written, the answer says why
  (``not_on_plan`` with the capability's reason code), and the next ensure asks again.
- Once set up (``link.scan_configured_at``) the config is the user's: a rate or switch
  changed in the project's settings stays as the user left it, and the answer reports
  what the config says (``turned_off`` when it analyses nothing).
"""

from dataclasses import dataclass

import structlog
from django.utils import timezone

from tfc.capabilities import service as capability_service
from tfc.utils.error_codes import get_error_message
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.models.trace_scan import TraceScanConfig

logger = structlog.get_logger(__name__)

ERROR_FEED = "error_feed"
# OD5's working value (learning-service PLAN section 8): a fixed low share of an agent's
# runs. The owner sets the entitlement and its price (packet 60); a user changes the rate
# in the project's settings.
AGENT_MISTAKES_SAMPLING_RATE = 0.1

ON = "on"
OFF = "off"
NOT_ON_PLAN = "not_on_plan"
TURNED_OFF = "turned_off"


@dataclass(frozen=True)
class MistakeAnalysis:
    """Whether the agent's runs are analysed for mistakes, and if not, why."""

    status: str
    sampling_rate: float | None = None
    # Set when ``status`` is off: ``not_on_plan`` or ``turned_off``.
    reason: str | None = None
    # The capability's denial code, for ``not_on_plan``.
    reason_code: str | None = None
    message: str | None = None


def ensure_mistake_analysis(link: OmegaDeploymentObserveLink) -> MistakeAnalysis:
    """Set the analysis up the first time the plan allows it, then report it."""
    if link.scan_configured_at is None:
        decision = capability_service.check(
            ERROR_FEED,
            org_id=str(link.organization_id),
            workspace_id=str(link.workspace_id),
        )
        if not decision.allowed:
            logger.info(
                "omega_agent_observe_mistakes_not_on_plan",
                organization_id=str(link.organization_id),
                workspace_id=str(link.workspace_id),
                ee_agent_id=link.ee_agent_id,
                project_id=str(link.project_id),
                reason_code=decision.reason_code,
            )
            return MistakeAnalysis(
                status=OFF,
                reason=NOT_ON_PLAN,
                reason_code=decision.reason_code,
                message=get_error_message("OMEGA_AGENT_OBSERVE_MISTAKES_NOT_ON_PLAN"),
            )
        _set_up(link)
    return _current(link)


def _set_up(link: OmegaDeploymentObserveLink) -> None:
    """Give the project the agent's rate, unless a rate above 0 is already there."""
    # Two first ensures on one project wait here; the second finds the first's config.
    # The base manager: no workspace join, so only the project row is locked.
    list(
        Project._base_manager.select_for_update()
        .filter(pk=link.project_id)
        .values_list("pk", flat=True)
    )
    # The project id is unique on the table whether or not a row is soft-deleted.
    config = TraceScanConfig.all_objects.filter(project_id=link.project_id).first()
    if config is None:
        TraceScanConfig.all_objects.create(
            project_id=link.project_id,
            sampling_rate=AGENT_MISTAKES_SAMPLING_RATE,
            enabled=True,
        )
    elif config.deleted or config.sampling_rate <= 0:
        config.deleted = False
        config.deleted_at = None
        config.sampling_rate = AGENT_MISTAKES_SAMPLING_RATE
        config.save(
            update_fields=["deleted", "deleted_at", "sampling_rate", "updated_at"]
        )
    link.scan_configured_at = timezone.now()
    link.save(update_fields=["scan_configured_at", "updated_at"])
    logger.info(
        "omega_agent_observe_mistakes_ready",
        ee_agent_id=link.ee_agent_id,
        project_id=str(link.project_id),
    )


def _current(link: OmegaDeploymentObserveLink) -> MistakeAnalysis:
    config = TraceScanConfig.no_workspace_objects.filter(
        project_id=link.project_id
    ).first()
    if config is None or not config.enabled or config.sampling_rate <= 0:
        return MistakeAnalysis(
            status=OFF,
            sampling_rate=config.sampling_rate if config is not None else None,
            reason=TURNED_OFF,
            message=get_error_message("OMEGA_AGENT_OBSERVE_MISTAKES_TURNED_OFF"),
        )
    return MistakeAnalysis(status=ON, sampling_rate=config.sampling_rate)


def clear_mistake_analysis(link: OmegaDeploymentObserveLink) -> None:
    """Forget the set-up when the link moves to another project (saved by the caller)."""
    link.scan_configured_at = None
