from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.services.onboarding.observe_events import (
    record_observe_first_loop_completed,
    record_observe_project_created,
)
from tfc.middleware.workspace_context import get_current_user
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.dashboard import Dashboard
from tracer.models.monitor import UserAlertMonitor
from tracer.models.project import Project
from tracer.models.saved_view import SavedView


@receiver(post_save, sender=Project)
def record_observe_project_activation(sender, instance, created, **kwargs):
    if not created or instance.trace_type != "observe":
        return
    user = instance.user if instance.user_id else get_current_user()

    record_observe_project_created(
        project=instance,
        user=user,
        source=f"project_{instance.source or 'created'}",
    )


@receiver(post_save, sender=CustomEvalConfig)
def record_observe_evaluator_loop_completion(sender, instance, created, **kwargs):
    if not created or not getattr(instance, "project_id", None):
        return

    record_observe_first_loop_completed(
        organization=instance.project.organization,
        workspace=instance.project.workspace,
        user=get_current_user(),
        artifact_type="custom_eval_config",
        artifact_id=instance.id,
        project=instance.project,
        source="observe_evaluator_created",
    )


@receiver(post_save, sender=Dashboard)
def record_observe_dashboard_loop_completion(sender, instance, created, **kwargs):
    if not created:
        return

    record_observe_first_loop_completed(
        organization=instance.workspace.organization,
        workspace=instance.workspace,
        user=instance.created_by or get_current_user(),
        artifact_type="dashboard",
        artifact_id=instance.id,
        source="observe_dashboard_created",
    )


@receiver(post_save, sender=UserAlertMonitor)
def record_observe_alert_loop_completion(sender, instance, created, **kwargs):
    if not created or not getattr(instance, "project_id", None):
        return

    record_observe_first_loop_completed(
        organization=instance.organization,
        workspace=instance.workspace,
        user=instance.created_by or get_current_user(),
        artifact_type="alert",
        artifact_id=instance.id,
        project=instance.project,
        source="observe_alert_created",
    )


@receiver(post_save, sender=SavedView)
def record_observe_saved_view_loop_completion(sender, instance, created, **kwargs):
    if not created or not getattr(instance, "project_id", None):
        return

    record_observe_first_loop_completed(
        organization=instance.workspace.organization,
        workspace=instance.workspace,
        user=instance.created_by or get_current_user(),
        artifact_type="saved_view",
        artifact_id=instance.id,
        project=instance.project,
        source="observe_saved_view_created",
    )
