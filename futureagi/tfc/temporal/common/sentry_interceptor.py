"""
Sentry interceptor for Temporal workflows and activities.

This interceptor provides:
1. Automatic exception capture with Temporal context (workflow_id, activity_id, etc.)
2. Sentry transactions for workflows (performance monitoring)
3. Sentry spans for activities (performance monitoring)
4. Breadcrumbs for activity start/end events

"""

from datetime import datetime
from typing import Any, Optional, Type

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    ExecuteWorkflowInput,
    Interceptor,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
)

# Non-retryable ApplicationError types that an activity raises to report an
# expected refusal rather than a fault. An evaluation usage or entitlement
# denial is the activity's answer: the workflow records it and stops, so it is
# not a Sentry issue. Any other type, or a retryable one, is still captured.
EXPECTED_ACTIVITY_FAILURE_TYPES = frozenset({"EvaluationDenied"})


def _is_expected_activity_failure(error: BaseException) -> bool:
    return (
        isinstance(error, ApplicationError)
        and error.non_retryable
        and error.type in EXPECTED_ACTIVITY_FAILURE_TYPES
    )


def _get_sentry_sdk():
    """Lazily import sentry_sdk to avoid import issues in workflow sandbox."""
    try:
        import sentry_sdk

        return sentry_sdk
    except ImportError:
        return None


def _safe_serialize_input(args: tuple) -> dict:
    """
    Safely serialize activity input for Sentry context.
    Handles dataclasses, dicts, and primitive types.
    Truncates large values to avoid Sentry payload limits.
    """
    MAX_STR_LEN = 500
    MAX_ITEMS = 10

    def _serialize_value(val, depth=0):
        if depth > 3:
            return "<nested>"
        if val is None:
            return None
        if isinstance(val, (str, int, float, bool)):
            if isinstance(val, str) and len(val) > MAX_STR_LEN:
                return val[:MAX_STR_LEN] + "..."
            return val
        if isinstance(val, (list, tuple)):
            if len(val) > MAX_ITEMS:
                return [_serialize_value(v, depth + 1) for v in val[:MAX_ITEMS]] + [
                    f"... +{len(val) - MAX_ITEMS} more"
                ]
            return [_serialize_value(v, depth + 1) for v in val]
        if isinstance(val, dict):
            return {
                k: _serialize_value(v, depth + 1)
                for k, v in list(val.items())[:MAX_ITEMS]
            }
        if hasattr(val, "__dataclass_fields__"):
            # Dataclass - convert to dict
            from dataclasses import asdict

            try:
                return _serialize_value(asdict(val), depth + 1)
            except Exception:
                return f"<{type(val).__name__}>"
        return f"<{type(val).__name__}>"

    if not args:
        return {}

    try:
        if len(args) == 1:
            return {"input": _serialize_value(args[0])}
        return {"inputs": [_serialize_value(a) for a in args]}
    except Exception:
        return {"input": "<serialization_error>"}


class SentryActivityInboundInterceptor(ActivityInboundInterceptor):
    """
    Intercepts activity execution to:
    - Create Sentry spans for performance monitoring
    - Capture exceptions with full Temporal context
    - Add breadcrumbs for activity lifecycle
    - Capture activity input for debugging
    """

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        sentry_sdk = _get_sentry_sdk()
        if sentry_sdk is None:
            return await super().execute_activity(input)

        # Get activity info for context
        try:
            info = activity.info()
            activity_context = {
                "activity_id": info.activity_id,
                "activity_type": info.activity_type,
                "attempt": info.attempt,
                "task_queue": info.task_queue,
                "workflow_id": info.workflow_id,
                "workflow_run_id": info.workflow_run_id,
                "workflow_type": info.workflow_type,
                "workflow_namespace": info.workflow_namespace,
                "scheduled_time": (
                    info.scheduled_time.isoformat() if info.scheduled_time else None
                ),
            }
        except RuntimeError:
            activity_context = {}

        # Serialize activity input for debugging (truncated)
        activity_input = _safe_serialize_input(input.args)

        # Add breadcrumb for activity start with input summary
        sentry_sdk.add_breadcrumb(
            message=f"Activity started: {input.fn.__name__}",
            category="temporal.activity",
            level="info",
            data={
                "activity_type": input.fn.__name__,
                "attempt": activity_context.get("attempt", 1),
                "workflow_id": activity_context.get("workflow_id"),
                **activity_input,
            },
        )

        # Create a span for the activity execution
        with sentry_sdk.start_span(
            op="temporal.activity",
            name=input.fn.__name__,
            origin="auto.temporal",
        ) as span:
            # Set span data from activity context
            for key, value in activity_context.items():
                if value is not None:
                    span.set_data(key, value)

            try:
                result = await super().execute_activity(input)

                # Add breadcrumb for activity success
                sentry_sdk.add_breadcrumb(
                    message=f"Activity completed: {input.fn.__name__}",
                    category="temporal.activity",
                    level="info",
                    data={
                        "activity_type": input.fn.__name__,
                        "status": "completed",
                    },
                )

                span.set_status("ok")
                return result

            except Exception as e:
                # Capture exception with full Temporal context using isolated scope
                # Note: Activities may also call activity.logger.exception() which
                # triggers error logging. Duplicate events are deduplicated
                # based on fingerprint and merged.
                with sentry_sdk.push_scope() as scope:
                    # Set Temporal-specific tags for filtering
                    scope.set_tag("temporal.activity_type", input.fn.__name__)
                    scope.set_tag(
                        "temporal.workflow_id",
                        activity_context.get("workflow_id", "unknown"),
                    )
                    scope.set_tag(
                        "temporal.workflow_type",
                        activity_context.get("workflow_type", "unknown"),
                    )
                    scope.set_tag(
                        "temporal.task_queue",
                        activity_context.get("task_queue", "unknown"),
                    )
                    scope.set_tag(
                        "temporal.attempt",
                        str(activity_context.get("attempt", 1)),
                    )

                    # Set context with full activity info
                    scope.set_context("temporal_activity", activity_context)

                    # Set context with activity input for debugging
                    scope.set_context("activity_input", activity_input)

                    # Set fingerprint for better grouping - ensures deduplication
                    scope.fingerprint = [
                        "temporal",
                        "activity",
                        input.fn.__name__,
                        type(e).__name__,
                    ]

                    # An expected refusal keeps its breadcrumb and span status
                    # below but is not reported as an issue.
                    if not _is_expected_activity_failure(e):
                        sentry_sdk.capture_exception(e)

                # Add breadcrumb for activity failure
                sentry_sdk.add_breadcrumb(
                    message=f"Activity failed: {input.fn.__name__}",
                    category="temporal.activity",
                    level="error",
                    data={
                        "activity_type": input.fn.__name__,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )

                span.set_status("internal_error")
                span.set_data("error", str(e))
                span.set_data("error_type", type(e).__name__)

                raise


class SentryWorkflowInboundInterceptor(WorkflowInboundInterceptor):
    """
    Intercepts workflow execution to:
    - Create Sentry transactions for performance monitoring
    - Set workflow context for all nested activities
    """

    async def execute_workflow(self, input: ExecuteWorkflowInput) -> Any:
        sentry_sdk = _get_sentry_sdk()
        if sentry_sdk is None:
            return await super().execute_workflow(input)

        # Get workflow info for context
        try:
            info = workflow.info()
            workflow_context = {
                "workflow_id": info.workflow_id,
                "workflow_type": info.workflow_type,
                "run_id": info.run_id,
                "task_queue": info.task_queue,
                "namespace": info.namespace,
                "attempt": info.attempt,
                "start_time": (
                    info.start_time.isoformat() if info.start_time else None
                ),
            }
        except RuntimeError:
            workflow_context = {}

        # Start a transaction for the workflow
        # Note: Workflows can run for a long time, so we use a high timeout
        with sentry_sdk.start_transaction(
            op="temporal.workflow",
            name=input.type.__name__ if hasattr(input, "type") else "workflow",
            origin="auto.temporal",
        ) as transaction:
            # Set transaction data
            for key, value in workflow_context.items():
                if value is not None:
                    transaction.set_data(key, value)

            # Set tags for filtering
            transaction.set_tag(
                "temporal.workflow_type",
                workflow_context.get("workflow_type", "unknown"),
            )
            transaction.set_tag(
                "temporal.task_queue",
                workflow_context.get("task_queue", "unknown"),
            )

            try:
                result = await super().execute_workflow(input)
                transaction.set_status("ok")
                return result

            except Exception as e:
                # Capture workflow-level exceptions
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag(
                        "temporal.workflow_type",
                        workflow_context.get("workflow_type", "unknown"),
                    )
                    scope.set_tag(
                        "temporal.workflow_id",
                        workflow_context.get("workflow_id", "unknown"),
                    )
                    scope.set_context("temporal_workflow", workflow_context)

                    # Only capture if it's not already captured by activity
                    # (to avoid duplicate events)
                    scope.fingerprint = [
                        "temporal",
                        "workflow",
                        workflow_context.get("workflow_type", "unknown"),
                        type(e).__name__,
                    ]

                    sentry_sdk.capture_exception(e)

                transaction.set_status("internal_error")
                raise


class SentryInterceptor(Interceptor):
    """
    Main Sentry interceptor for Temporal workers.

    Usage:
        from tfc.temporal.common.sentry_interceptor import SentryInterceptor

        worker = Worker(
            client=client,
            task_queue=task_queue,
            workflows=workflows,
            activities=activities,
            interceptors=[SentryInterceptor()],
        )

    This interceptor automatically:
    - Creates Sentry transactions for workflow executions
    - Creates Sentry spans for activity executions
    - Captures exceptions with full Temporal context
    - Adds breadcrumbs for activity lifecycle events
    """

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        """Wrap activity execution with Sentry instrumentation."""
        return SentryActivityInboundInterceptor(next)

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> Optional[Type[WorkflowInboundInterceptor]]:
        """
        Return the workflow interceptor class.

        Note: Workflow interceptors have limited capabilities due to
        Temporal's determinism requirements. We mainly use this for
        transaction creation, not exception capture (which is better
        handled at the activity level).
        """
        # Workflow interceptor is optional - activity interceptor handles most cases
        return None


def configure_sentry_for_temporal(queue_name: str = "default") -> None:
    """
    Configure Sentry SDK with Temporal-specific settings.

    This should be called during worker initialization, after init_sentry().

    Args:
        queue_name: The Temporal task queue name for tagging
    """
    sentry_sdk = _get_sentry_sdk()
    if sentry_sdk is None:
        return

    # Set global tags for this worker
    sentry_sdk.set_tag("temporal.task_queue", queue_name)
    sentry_sdk.set_tag("component", "temporal-worker")

    # Add context about the worker
    sentry_sdk.set_context(
        "temporal_worker",
        {
            "task_queue": queue_name,
            "started_at": datetime.utcnow().isoformat(),
        },
    )


__all__ = [
    "SentryInterceptor",
    "SentryActivityInboundInterceptor",
    "SentryWorkflowInboundInterceptor",
    "configure_sentry_for_temporal",
]
