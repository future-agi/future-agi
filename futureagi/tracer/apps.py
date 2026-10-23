from django.apps import AppConfig


class SdkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tracer"

    def ready(self):
        # Import all model modules so Django discovers them.
        # Required for cross-app FK resolution (model_hub → tracer).
        from django.db.models.signals import post_save

        from accounts.models.workspace import Workspace
        from tracer.models import (
            custom_eval_config,  # noqa: F401
            dashboard,  # noqa: F401
            eval_ci_cd,  # noqa: F401
            eval_task,  # noqa: F401
            external_eval_config,  # noqa: F401
            feed_alert,  # noqa: F401
            feed_escalation,  # noqa: F401
            imagine_analysis,  # noqa: F401
            monitor,  # noqa: F401
            observability_provider,  # noqa: F401
            observation_span,  # noqa: F401
            project,  # noqa: F401
            project_version,  # noqa: F401
            replay_session,  # noqa: F401
            saved_view,  # noqa: F401
            shared_link,  # noqa: F401
            span_notes,  # noqa: F401
            trace,  # noqa: F401
            trace_annotation,  # noqa: F401
            trace_error_analysis,  # noqa: F401
            trace_error_analysis_task,  # noqa: F401
            trace_grouping,  # noqa: F401
            trace_investigation,  # noqa: F401
            trace_scan,  # noqa: F401
            trace_session,  # noqa: F401
        )
        from tracer.services.feed_escalation_settings import provision_policy

        post_save.connect(
            provision_policy,
            sender=Workspace,
            dispatch_uid="tracer.feed_escalation_policy",
            weak=False,
        )
