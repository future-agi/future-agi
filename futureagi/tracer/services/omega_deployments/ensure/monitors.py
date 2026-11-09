"""Set up a link's two default monitors once per Observe project (SPEC 2.7).

One concern: creating or adopting the defaults of the link's kind
(``defaults.default_monitors_for``: a deployment's production pair, or a Console
agent's unfiltered pair). It locks the project row first, so two ensure calls on
one project (the same deployment or agent twice, or two deployments sharing a
name) set the defaults up once.

- A link whose ``alerts_created_at`` is set is left alone: a monitor the user
  deleted or muted stays that way.
- Another link in the workspace that already set the defaults up on the same
  project (a deployment re-created under a reused name) lends its monitors, so
  a project never gets a second pair.
- Otherwise both are created, if the plan has room for both
  (``quota.defaults_deferred``), through the same serializer Observe -> Alerts
  uses, so they validate, render and edit like monitors made by hand, and email
  the person who deployed. Without room, nothing is created, ``alerts_created_at``
  stays empty and the next ensure asks again. Each deferral is counted on the
  link (``alerts_deferred_count``, ``alerts_last_deferred_at``, kept after the
  pair is created) and logged as ``omega_deployment_observe_alerts_deferred``
  (``omega_agent_observe_alerts_deferred`` for an agent link).
"""

import structlog
from django.db.models import F
from django.utils import timezone

from accounts.models import User
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.serializers.monitor import UserAlertMonitorSerializer
from tracer.services.omega_deployments.ensure.defaults import (
    EE_ID_LOG_FIELD_BY_LINK_KIND,
    LINK_FIELD_BY_KIND,
    LOG_PREFIX_BY_LINK_KIND,
    MESSAGE_PREFIX_BY_LINK_KIND,
    MonitorSpec,
    default_monitors_for,
    filters_for,
)
from tracer.services.omega_deployments.ensure.quota import (
    AlertsDeferred,
    defaults_deferred,
)

logger = structlog.get_logger(__name__)

_LINK_FIELDS = list(LINK_FIELD_BY_KIND.values())


def _ee_id_field(link: OmegaDeploymentObserveLink) -> dict[str, str]:
    """The link's EE id as its log lines name it (``ee_deployment_id`` or ``ee_agent_id``)."""
    return {EE_ID_LOG_FIELD_BY_LINK_KIND[link.kind]: link.ee_id}


def ensure_default_monitors(
    link: OmegaDeploymentObserveLink, user: User
) -> AlertsDeferred | None:
    """Point ``link`` at the defaults on its project, creating them the first time.

    Returns why they were not created when the plan has no room for them, else ``None``.
    """
    if link.alerts_created_at is not None:
        return None
    # Wait for any other ensure on this project to commit, then read its pair.
    # The base manager: no workspace join, so only the project row is locked.
    list(
        Project._base_manager.select_for_update()
        .filter(pk=link.project_id)
        .values_list("pk", flat=True)
    )
    lender = (
        OmegaDeploymentObserveLink.no_workspace_objects.filter(
            workspace_id=link.workspace_id,
            project_id=link.project_id,
            alerts_created_at__isnull=False,
        )
        .exclude(pk=link.pk)
        .order_by("alerts_created_at")
        .first()
    )
    specs = default_monitors_for(link.kind)
    if lender is None:
        deferred = defaults_deferred(
            str(link.organization_id),
            len(specs),
            message_prefix=MESSAGE_PREFIX_BY_LINK_KIND[link.kind],
        )
        if deferred is not None:
            _record_deferral(link, deferred)
            return deferred
    for spec in specs:
        field = LINK_FIELD_BY_KIND[spec.kind]
        if lender is not None:
            monitor_id = getattr(lender, f"{field}_id")
        else:
            monitor_id = _create_monitor(spec, link, user).id
        setattr(link, f"{field}_id", monitor_id)
    link.alerts_created_at = timezone.now()
    link.save(update_fields=[*_LINK_FIELDS, "alerts_created_at", "updated_at"])
    logger.info(
        f"{LOG_PREFIX_BY_LINK_KIND[link.kind]}_monitors_ready",
        **_ee_id_field(link),
        project_id=str(link.project_id),
        adopted_from=str(lender.pk) if lender is not None else None,
    )
    return None


def _record_deferral(
    link: OmegaDeploymentObserveLink, deferred: AlertsDeferred
) -> None:
    """Count the deferral on the link and log it; the ensure's transaction commits both."""
    now = timezone.now()
    OmegaDeploymentObserveLink.no_workspace_objects.filter(pk=link.pk).update(
        alerts_deferred_count=F("alerts_deferred_count") + 1,
        alerts_last_deferred_at=now,
        updated_at=now,
    )
    link.refresh_from_db(fields=["alerts_deferred_count", "alerts_last_deferred_at"])
    logger.info(
        f"{LOG_PREFIX_BY_LINK_KIND[link.kind]}_alerts_deferred",
        organization_id=str(link.organization_id),
        workspace_id=str(link.workspace_id),
        **_ee_id_field(link),
        project_id=str(link.project_id),
        code=deferred.code,
        limit=deferred.limit,
        current_usage=deferred.current_usage,
        needed=deferred.needed,
        deferred_count=link.alerts_deferred_count,
    )


def live_default_monitors(
    link: OmegaDeploymentObserveLink,
) -> list[tuple[str, UserAlertMonitor]]:
    """The defaults the link still has, in its kind's order; deleted ones are left out."""
    specs = default_monitors_for(link.kind)
    ids = {
        spec.kind: getattr(link, f"{LINK_FIELD_BY_KIND[spec.kind]}_id")
        for spec in specs
    }
    live = {
        monitor.id: monitor
        for monitor in UserAlertMonitor.no_workspace_objects.filter(
            id__in=[monitor_id for monitor_id in ids.values() if monitor_id],
            project_id=link.project_id,
        )
    }
    return [
        (spec.kind, live[ids[spec.kind]]) for spec in specs if ids[spec.kind] in live
    ]


def clear_default_monitors(link: OmegaDeploymentObserveLink) -> None:
    """Forget the defaults when the link moves to another project (saved by the caller)."""
    for field in _LINK_FIELDS:
        setattr(link, f"{field}_id", None)
    link.alerts_created_at = None


def _free_name(spec: MonitorSpec, link: OmegaDeploymentObserveLink) -> str:
    """The default's name, or the name plus the link's EE id if a monitor already has it."""
    taken = UserAlertMonitor.no_workspace_objects.filter(
        project_id=link.project_id, name=spec.name
    ).exists()
    return f"{spec.name} [{link.ee_id}]" if taken else spec.name


def _create_monitor(
    spec: MonitorSpec, link: OmegaDeploymentObserveLink, user: User
) -> UserAlertMonitor:
    email = user.email
    serializer = UserAlertMonitorSerializer(
        data={
            "name": _free_name(spec, link),
            "metric_type": spec.metric_type,
            "project": str(link.project_id),
            "organization": str(link.organization_id),
            "workspace": str(link.workspace_id),
            "created_by": str(user.pk),
            "threshold_operator": spec.threshold_operator,
            "threshold_type": spec.threshold_type,
            "critical_threshold_value": spec.critical_threshold_value,
            "warning_threshold_value": spec.warning_threshold_value,
            "alert_frequency": spec.alert_frequency,
            "auto_threshold_time_window": spec.auto_threshold_time_window,
            "notification_emails": [email] if email else [],
            "filters": filters_for(link.kind),
        }
    )
    # A refusal here is a bug in the defaults, not user input: let it raise.
    serializer.is_valid(raise_exception=True)
    monitor: UserAlertMonitor = serializer.save()
    return monitor
