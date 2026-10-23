"""One bounded job, no automatic retries of an analytical scan."""

import logging
from datetime import timedelta

from tfc.temporal import temporal_activity
from django.utils import timezone

from tracer.models.sql_query import SQLExecution

from .access import Actor, authorize
from .compiler import compile_query
from .catalog import (
    CatalogUnavailable,
    EvalConfigLimitExceeded,
    additional_table_filters,
)
from .runner import QueryCancelled, QueryUnavailable, run
from .service import (
    EXECUTION_SECONDS,
    confirm_stopped,
    query_scope,
    result_cache_key,
    result_cache_seconds,
)

logger = logging.getLogger(__name__)


@temporal_activity(
    name="tracer.sql_workbench.execute",
    queue="exact_aggregation",
    time_limit=45,
    schedule_to_start_timeout=45,
    workflow_run_timeout=100,
    workflow_execution_timeout=120,
    max_retries=0,
)
def execute_sql(job_id):
    now = timezone.now()
    # The queue deadline admits the start; the execution deadline is re-anchored
    # to this actual start, so time spent waiting in the queue never shortens it.
    if not SQLExecution.objects.filter(
        id=job_id, state="queued", deadline__gt=now
    ).update(state="running", deadline=now + timedelta(seconds=EXECUTION_SECONDS)):
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
            return not SQLExecution.objects.filter(
                id=job_id, state="running", deadline__gt=timezone.now()
            ).exists()

        result = run(
            sql, job.id, cancelled, table_filters=additional_table_filters(scope)
        )
        # Revocation during execution must not publish a previously admitted result.
        project = authorize(actor, job.project_id)
        if cache_key:
            completed_scope = query_scope(actor, project, job.start, job.end, job.sql)
            if (
                not result_cache_seconds()
                or result_cache_key(actor, project, completed_scope, job.sql) != cache_key
            ):
                cache_key = ""
        SQLExecution.objects.filter(id=job_id, state="running").update(
            state="succeeded",
            cache_key=cache_key,
            result=result,
            output_bytes=result["result_bytes"],
            finished_at=timezone.now(),
        )
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
                exc, (QueryUnavailable, CatalogUnavailable, EvalConfigLimitExceeded)
            )
            else "Query failed or reached a resource limit. Narrow the window or simplify the query."
        )
        logger.warning(
            "SQL job failed",
            extra={"query_id": str(job.id), "error_type": type(exc).__name__},
        )
        SQLExecution.objects.filter(id=job_id, state="running").update(
            state="failed", error=message[:300], result={}, finished_at=timezone.now()
        )
    finally:
        # run() has returned and closed its native connection: a user cancel
        # that arrived during the scan can release its admission slot now.
        confirm_stopped(job_id)


@temporal_activity(
    name="tracer.sql_workbench.purge_expired_results",
    queue="exact_aggregation",
    time_limit=30,
    schedule_to_start_timeout=900,
    workflow_run_timeout=960,
    workflow_execution_timeout=990,
    max_retries=0,
)
def purge_expired_sql_results():
    from .service import purge_expired_results

    return purge_expired_results(batch_size=1000)
