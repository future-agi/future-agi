"""One bounded job, no automatic retries of an analytical scan."""

import logging

from tfc.temporal import temporal_activity
from django.utils import timezone

from tracer.models.sql_query import SQLExecution

from .access import Actor, authorize
from .compiler import compile_query
from .catalog import CatalogUnavailable, additional_table_filters
from .runner import QueryCancelled, QueryUnavailable, run
from .service import query_scope

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
    if not SQLExecution.objects.filter(
        id=job_id, state="queued", deadline__gt=timezone.now()
    ).update(state="running"):
        return
    job = SQLExecution.objects.get(id=job_id)
    actor = Actor(job.created_by_id, job.organization_id, job.workspace_id)
    try:
        project = authorize(actor, job.project_id)
        scope = query_scope(actor, project, job.start, job.end, job.sql)
        sql = compile_query(job.sql, scope)

        def cancelled():
            return not SQLExecution.objects.filter(
                id=job_id, state="running", deadline__gt=timezone.now()
            ).exists()

        result = run(
            sql, job.id, cancelled, table_filters=additional_table_filters(scope)
        )
        # Revocation during execution must not publish a previously admitted result.
        authorize(actor, job.project_id)
        SQLExecution.objects.filter(id=job_id, state="running").update(
            state="succeeded",
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
            if isinstance(exc, (QueryUnavailable, CatalogUnavailable))
            else "Query failed or reached a resource limit. Narrow the window or simplify the query."
        )
        logger.warning(
            "SQL job failed",
            extra={"query_id": str(job.id), "error_type": type(exc).__name__},
        )
        SQLExecution.objects.filter(id=job_id, state="running").update(
            state="failed", error=message[:300], result={}, finished_at=timezone.now()
        )


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
