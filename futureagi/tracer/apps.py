from django.apps import AppConfig


class SdkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tracer"

    def ready(self):
        # Import all model modules so Django discovers them.
        # Required for cross-app FK resolution (model_hub → tracer).
        from tracer.models import custom_eval_config  # noqa: F401
        from tracer.models import dashboard  # noqa: F401
        from tracer.models import eval_ci_cd  # noqa: F401
        from tracer.models import eval_task  # noqa: F401
        from tracer.models import external_eval_config  # noqa: F401
        from tracer.models import imagine_analysis  # noqa: F401
        from tracer.models import monitor  # noqa: F401
        from tracer.models import observability_provider  # noqa: F401
        from tracer.models import observation_span  # noqa: F401
        from tracer.models import project  # noqa: F401
        from tracer.models import project_version  # noqa: F401
        from tracer.models import replay_session  # noqa: F401
        from tracer.models import saved_view  # noqa: F401
        from tracer.models import shared_link  # noqa: F401
        from tracer.models import span_notes  # noqa: F401
        from tracer.models import trace  # noqa: F401
        from tracer.models import trace_annotation  # noqa: F401
        from tracer.models import trace_error_analysis  # noqa: F401
        from tracer.models import trace_error_analysis_task  # noqa: F401
        from tracer.models import trace_session  # noqa: F401
