from django.apps import AppConfig


class SdkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tracer"

    def ready(self):
        # Import all model modules so Django discovers them.
        # Required for cross-app FK resolution (model_hub → tracer).
        from tracer.models import (
            custom_eval_config,  # noqa: F401
            eval_ci_cd,  # noqa: F401
            eval_task,  # noqa: F401
            external_eval_config,  # noqa: F401
            monitor,  # noqa: F401
            observability_provider,  # noqa: F401
            observation_span,  # noqa: F401
            project,  # noqa: F401
            project_version,  # noqa: F401
            replay_session,  # noqa: F401
            shared_link,  # noqa: F401
            span_notes,  # noqa: F401
            trace,  # noqa: F401
            trace_annotation,  # noqa: F401
            trace_error_analysis,  # noqa: F401
            trace_error_analysis_task,  # noqa: F401
            trace_session,  # noqa: F401
        )

        # CDC-off replacement: mirror eval verdicts PG -> CH on write so the
        # CH-only eval read-back (eval columns/panels) is populated without
        # PeerDB. Gated by dual_write_enabled() inside the mirror.
        from tracer.services.clickhouse.v2.eval_logger_writer import (
            connect_eval_logger_mirror,
        )

        connect_eval_logger_mirror()

        # CDC-off replacement: mirror unified-annotation Scores PG -> CH on write
        # so the observe annotation filters (has_annotation / annotator /
        # per-label value) resolve without PeerDB. Gated by dual_write_enabled().
        from tracer.services.clickhouse.v2.score_writer import (
            connect_score_mirror,
        )

        connect_score_mirror()
