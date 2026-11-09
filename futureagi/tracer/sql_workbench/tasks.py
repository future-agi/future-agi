"""One bounded job, no automatic retries of an analytical scan."""

import logging
from datetime import timedelta

from django.utils import timezone

from tfc.temporal import temporal_activity
from tfc.temporal.sql_workbench import SQL_ACTIVITY_QUEUE, SQLExecutionWorkflow
from tracer.models.sql_query import SQLExecution

from .access import Actor, authorize
from .catalog import (
    CatalogUnavailable,
    EvalConfigLimitExceeded,
    additional_table_filters,
)
from .compiler import compile_query
from .runner import QueryCancelled, QueryDeadlineExpired, QueryUnavailable, run
from .service import (
    EXECUTION_SECONDS,
    confirm_stopped,
    fail_execution,
    query_scope,
    result_cache_key,
    result_cache_seconds,
)

logger = logging.getLogger(__name__)


@temporal_activity(
    name="tracer.sql_workbench.execute",
    queue=SQL_ACTIVITY_QUEUE,
    workflow_class=SQLExecutionWorkflow,
    time_limit=45,
    schedule_to_start_timeout=45,
    workflow_run_timeout=150,
    workflow_execution_timeout=180,
    max_retries=0,
)
def execute_sql(job_id):
    now = timezone.now()
    # The queue deadline admits the start; the execution deadline is re-anchored
    # to this actual start, so time spent waiting in the queue never shortens it.
    if not SQLExecution.objects.filter(
        id=job_id, state="queued", deadline__gt=now
    ).update(state="running", deadline=now + timedelta(seconds=EXECUTION_SECONDS)):
        fail_execution(job_id, now=now, expired_only=True)
        return
    job = SQLExecution.objects.get(id=job_id)
    actor = Actor(job.created_by_id, job.organization_id, job.workspace_id)
    try:
        project = authorize(actor, job.project_id)
        scope = query_scope(actor, project, job.start, job.end, job.sql)
        sql = compile_query(job.sql, scope)
        # Admission may have preceded a membership/catalog/store change. Such
        # a job can still run under current access, but cannot populate the
        # original admission key with a result produced under different scope.
        cache_key = job.cache_key
        if cache_key and (
            not result_cache_seconds()
            or result_cache_key(actor, project, scope, job.sql) != cache_key
        ):
            cache_key = ""

        def cancelled():
            control = (
                SQLExecution.objects.filter(id=job_id)
                .values("state", "deadline")
                .first()
            )
            if control is None or control["deadline"] <= timezone.now():
                raise QueryDeadlineExpired()
            if control["state"] == "cancelled":
                return True
            if control["state"] != "running":
                raise QueryDeadlineExpired()
            return False

        result = run(
            sql, job.id, cancelled, table_filters=additional_table_filters(scope)
        )
        # Revocation during execution must not publish a previously admitted result.
        project = authorize(actor, job.project_id)
        if cache_key:
            completed_scope = query_scope(actor, project, job.start, job.end, job.sql)
            if (
                not result_cache_seconds()
                or result_cache_key(actor, project, completed_scope, job.sql)
                != cache_key
            ):
                cache_key = ""
        completed_at = timezone.now()
        published = SQLExecution.objects.filter(
            id=job_id, state="running", deadline__gt=completed_at
        ).update(
            state="succeeded",
            cache_key=cache_key,
            result=result,
            output_bytes=result["result_bytes"],
            finished_at=completed_at,
        )
        if not published:
            fail_execution(job_id, now=completed_at, expired_only=True)
    except QueryCancelled:
        SQLExecution.objects.filter(id=job_id, state="running").update(
            state="cancelled", finished_at=timezone.now()
        )
    except Exception as exc:
        # Driver exceptions may contain physical schema, SQL and literal values.
        # Do not return or log that text. Job ids provide safe audit correlation.
        message = (
            str(exc)
            if isinstance(
                exc, QueryUnavailable | CatalogUnavailable | EvalConfigLimitExceeded
            )
            else "Query failed or reached a resource limit. Narrow the window or simplify the query."
        )
        logger.warning(
            "SQL job failed",
            extra={"query_id": str(job.id), "error_type": type(exc).__name__},
        )
        SQLExecution.objects.filter(id=job_id, state="running").update(
            state="failed",
            error=message[:300],
            result={},
            output_bytes=0,
            finished_at=timezone.now(),
        )
    finally:
        # run() has returned and closed its native connection: a user cancel
        # that arrived during the scan can release its admission slot now.
        confirm_stopped(job_id)


@temporal_activity(
    name="tracer.sql_workbench.purge_expired_results",
    queue="tasks_s",
    time_limit=30,
    schedule_to_start_timeout=900,
    workflow_run_timeout=960,
    workflow_execution_timeout=990,
    max_retries=0,
)
def purge_expired_sql_results():
    from .service import purge_expired_results

    return purge_expired_results(batch_size=1000)


@temporal_activity(
    name="tracer.sql_workbench.fail_execution",
    queue="tasks_s",
    time_limit=5,
    max_retries=2,
)
def fail_sql_execution(job_id: str) -> int:
    return fail_execution(job_id, now=timezone.now())


@temporal_activity(
    name="tracer.sql_workbench.reconcile_expired",
    queue="tasks_s",
    time_limit=10,
    max_retries=0,
)
def reconcile_expired_sql_executions() -> int:
    from .service import reconcile_expired_executions

    return reconcile_expired_executions(now=timezone.now(), batch_size=1000)
