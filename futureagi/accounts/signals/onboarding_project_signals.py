from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.services.onboarding.observe_events import (
    record_observe_project_created,
)
from tracer.models.project import Project


@receiver(post_save, sender=Project)
def record_observe_project_activation(sender, instance, created, **kwargs):
    if not created or instance.trace_type != "observe":
        return
    if not instance.user_id:
        return

    record_observe_project_created(
        project=instance,
        user=instance.user,
        source=f"project_{instance.source or 'created'}",
    )
