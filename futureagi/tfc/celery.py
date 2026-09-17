import os
from logging.config import dictConfig

from celery import Celery
from celery.signals import worker_process_init
from django_structlog.celery.steps import DjangoStructLogInitStep
from django.conf import settings as django_settings
from kombu import Exchange, Queue

from tfc.logging import init_sentry
from tfc.settings import settings

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")

celery_app = Celery("tfc")

# Add django-structlog step for context propagation
# This ensures request_id, user_id, etc. flow from HTTP request to Celery task
celery_app.steps["worker"].add(DjangoStructLogInitStep)

# Ensure Celery uses Django's logging config. Skip under test settings: Django
# already applies it, and a mid-session re-apply wipes pytest's caplog handler.
# (`settings` here is the base module, which has no TESTING; use the active one.)
if not getattr(django_settings, "TESTING", False):
    dictConfig(settings.LOGGING)

# Import django-structlog Celery receivers for automatic task logging
# This logs task_started, task_succeeded, task_failed, etc. with context
from django_structlog.celery import receivers  # noqa: F401, E402

# =============================================================================
# OpenTelemetry Initialization for Celery Workers
# =============================================================================
# IMPORTANT: OpenTelemetry must be initialized AFTER the worker process starts.
# The worker_process_init signal ensures this happens at the right time.
# This prevents issues with threading and BatchSpanProcessor.
# =============================================================================


@worker_process_init.connect(weak=False)
def init_celery_tracing(*args, **kwargs):
    """
    Initialize OpenTelemetry tracing for Celery worker processes.

    This is called AFTER the worker process is forked, which is required
    for proper threading behavior with BatchSpanProcessor.

    Instruments: Celery, PostgreSQL, Redis, requests, httpx, urllib3, logging

    Reference:
    https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/celery/celery.html
    """
    import structlog

    logger = structlog.get_logger(__name__)

    try:
        from tfc.telemetry import init_telemetry, instrument_for_celery

        provider = init_telemetry(component="celery")
        if provider:
            instrument_for_celery()
            logger.info("celery_otel_initialized")
        else:
            logger.info("celery_otel_disabled")
    except ImportError as e:
        logger.warning("celery_otel_import_error", error=str(e))
    except Exception as e:
        logger.warning("celery_otel_init_failed", error=str(e))


# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
celery_app.config_from_object("django.conf:settings", namespace="CELERY")

celery_app.conf.task_default_queue = "celery"
celery_app.conf.task_default_exchange = "celery"
celery_app.conf.task_default_routing_key = "celery"
celery_app.conf.worker_soft_shutdown_timeout = 1150


celery_app.conf.task_queues = [
    Queue("celery", Exchange("celery"), routing_key="celery"),
    Queue(
        "trace_ingestion", Exchange("trace_ingestion"), routing_key="trace_ingestion"
    ),
    Queue("tasks_xl", Exchange("tasks_xl"), routing_key="tasks_xl"),
    Queue("tasks_l", Exchange("tasks_l"), routing_key="tasks_l"),
    Queue("tasks_s", Exchange("tasks_s"), routing_key="tasks_s"),
    Queue("agent_compass", Exchange("agent_compass"), routing_key="agent_compass"),
]

# Load task modules from all registered Django app configs.
celery_app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
celery_app.conf.beat_schedule = {
    # ==========================================================================
    # MODEL_HUB tasks - MIGRATED TO TEMPORAL
    # See: tfc/temporal/schedules/model_hub.py
    # To register Temporal schedules, run:
    #   python manage.py register_temporal_schedules
    # ==========================================================================
    # "eval_run_prompt": migrated to Temporal schedule "eval-run-prompt"
    # "eval_evaluation": migrated to Temporal schedule "eval-evaluation"
    # "error_localizer": migrated to Temporal schedule "error-localizer"
    # "process_pending_row_tasks": migrated to Temporal schedule "process-pending-row-tasks"
    # "optimization_runner": migrated to Temporal schedule "optimization-runner"
    # "delete_unused_compare_folder": migrated to Temporal schedule "delete-unused-compare-folder"
    # ==========================================================================
    # ==========================================================================
    # TRACER SCHEDULES - Migrated to Temporal (see tfc/temporal/schedules/tracer.py)
    # ==========================================================================
    # "process_in_line_evals": migrated to Temporal schedule "process-inline-evals"
    # "eval_task_cron": migrated to Temporal schedule "eval-task-cron"
    # "check_alerts": migrated to Temporal schedule "check-alerts"
    # "run_evals_on_spans": migrated to Temporal schedule "run-evals-on-spans"
    # "run_external_evals": migrated to Temporal schedule "process-external-evals"
    # "fetch_observability_logs": migrated to Temporal schedule "fetch-observability-logs"
    # "check-trace-errors": migrated to Temporal schedule "check-trace-errors"
    # ==========================================================================
    # ==========================================================================
    # SIMULATE SCHEDULES - Migrated to Temporal
    # ==========================================================================
    # See: tfc/temporal/schedules/simulate.py
    # - create-call-executions: Creates call executions for test runs
    # - monitor-test-executions: Monitors active test executions
    # Scenario workflows remain in Temporal (tfc/temporal/simulate/)
    # ==========================================================================
    # "sync_all_customers_stripe_subscription": {
    #     "task": "ee.usage.views.usage.sync_all_customers_stripe_subscription",
    #     "schedule": 3600,
    # },
    # "check_customers_for_auto_reload": {
    #     "task": "ee.usage.views.usage.check_customers_for_auto_reload",
    #     "schedule": 600,
    # },
    # "check_customers_for_monthly_credit_refill": {
    #     "task": "ee.usage.views.usage.check_customers_for_monthly_credit_refill",
    #     "schedule": 120,
    # },
    # ==========================================================================
    # SDK SCHEDULES - Migrated to Temporal (on-demand execution)
    # ==========================================================================
    # "run_async_evals": REMOVED - SDK evaluations now execute on-demand via
    # Temporal workflows instead of polling. See: tfc/temporal/evaluations/
    # ==========================================================================
}


init_sentry(component="celery")
