"""Version/manifest lifecycle. ClickHouse attempts publish only after completion."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import structlog
from django.db import transaction
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
)
from tracer.models.eval_task import EvalTask
from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.derived_metrics.cursor import (
    decode_result_cursor,
    encode_result_cursor,
)
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    SCOPE_SQL,
    Scope,
    aggregate_select,
    rollup_insert,
    snapshot_select,
)

logger = structlog.get_logger(__name__)
SEMANTICS = "One contribution per selection unit; complete cases only; inverse-probability weights apply once. Missing inputs are not zero. Zero denominator and invalid probabilities produce no value. Replay uses frozen inputs, not mutable raw history."
DEFINITION_KEYS = (
    "formula",
    "inputs",
    "selection_unit",
    "weighting",
    "cohort_key",
    "filters",
)


def _json_value(value):
    return json.loads(json.dumps(value, default=str))


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def validate_definition(data: dict, project) -> dict:
    formula = compile_formula(
        data["formula"], [item["alias"] for item in data["inputs"]]
    )
    if set(formula.required) != {item["alias"] for item in data["inputs"]}:
        raise MetricInputError(
            {"inputs": "Remove inputs that the formula does not use."}
        )
    config_ids = {
        str(item["config_id"]) for item in data["inputs"] if item["source"] == "eval"
    }
    visible = {
        str(value)
        for value in CustomEvalConfig.objects.filter(
            project=project, id__in=config_ids, deleted=False
        ).values_list("id", flat=True)
    }
    if config_ids != visible:
        raise MetricInputError(
            {"inputs": "One or more evaluators are not available in this project."}
        )
    if data["weighting"] == "inverse_probability" and not config_ids:
        raise MetricInputError(
            {
                "weighting": "Weighting requires persisted sampling metadata on an evaluation input."
            }
        )
    return {
        "valid": True,
        "kind": formula.kind,
        "required_inputs": list(formula.required),
        "semantics": SEMANTICS,
    }


@transaction.atomic
def save_definition(
    data: dict, project, workspace, user, metric: DerivedMetric | None = None
) -> DerivedMetric:
    validate_definition(data, project)
    recipe = _json_value({key: data[key] for key in DEFINITION_KEYS})
    if metric:
        metric = DerivedMetric.objects.select_for_update().get(pk=metric.pk)
        if metric.project_id != project.pk:
            raise MetricInputError(
                {"project_id": "A metric cannot move between projects."}
            )
        if data.get("expected_version") != metric.current_version:
            raise MetricInputError(
                {
                    "expected_version": "Metric changed. Reload before saving a new version."
                }
            )
        metric.current_version += 1
        metric.name, metric.description = data["name"], data["description"]
        metric.save(
            update_fields=["current_version", "name", "description", "updated_at"]
        )
    else:
        metric = DerivedMetric.objects.create(
            project=project,
            workspace=workspace,
            organization=workspace.organization,
            created_by=user,
            name=data["name"],
            description=data["description"],
        )
    DerivedMetricVersion.objects.create(
        metric=metric,
        version=metric.current_version,
        definition_hash=_hash(recipe),
        **recipe,
    )
    return metric


@transaction.atomic
def create_run(
    metric: DerivedMetric, data: dict, user
) -> tuple[DerivedMetricRun, bool]:
    version = data.get("version", metric.current_version)
    definition = DerivedMetricVersion.objects.filter(
        metric=metric, version=version
    ).first()
    if definition is None:
        raise MetricInputError({"version": "Metric version does not exist."})
    source_run_id = str(data.get("source_run_id") or "")
    if (
        source_run_id
        and not EvalTask.objects.filter(
            pk=source_run_id, project_id=metric.project_id, deleted=False
        ).exists()
    ):
        raise MetricInputError(
            {"source_run_id": "Evaluation run is not available in this project."}
        )
    payload = {
        "definition": str(definition.id),
        "window_start": data["window_start"],
        "window_end": data["window_end"],
        "source_run_id": source_run_id,
        "filters": data["filters"],
    }
    fingerprint = _hash({"metric": str(metric.id), "key": data["idempotency_key"]})
    # Serialize admission across metrics in the same project, not source rows.
    from tracer.models.project import Project

    Project.no_workspace_objects.select_for_update().get(pk=metric.project_id)
    if not DerivedMetricRun.objects.filter(fingerprint=fingerprint).exists():
        active = DerivedMetricRun.objects.filter(
            definition__metric__project_id=metric.project_id,
            status__in=[
                DerivedMetricRun.Status.QUEUED,
                DerivedMetricRun.Status.RUNNING,
            ],
            lease_expires_at__gt=timezone.now(),
        ).count()
        if active >= 2:
            raise MetricInputError(
                "Two metric runs are already active in this project. Wait or retry an expired run."
            )
    run, created = DerivedMetricRun.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={
            "definition": definition,
            "requested_by": user,
            "window_start": data["window_start"],
            "window_end": data["window_end"],
            "source_run_id": source_run_id,
            "filters": _json_value(data["filters"]),
            "as_of": timezone.now(),
            "lease_expires_at": timezone.now() + timedelta(minutes=20),
        },
    )
    existing = {
        "definition": str(run.definition_id),
        "window_start": run.window_start,
        "window_end": run.window_end,
        "source_run_id": run.source_run_id,
        "filters": run.filters,
    }
    if _hash(existing) != _hash(payload):
        raise MetricInputError(
            {"idempotency_key": "This key already identifies a different run request."}
        )
    return run, created


def _authorized_metric(metric: DerivedMetric) -> bool:
    project = metric.project
    # Jobs reauthorize after dequeue. Default-workspace legacy projects have
    # null workspace; no job may follow a project across an organization boundary.
    return (
        not metric.deleted
        and not metric.workspace.deleted
        and metric.workspace.is_active
        and metric.workspace.organization_id == metric.organization_id
        and not project.deleted
        and project.organization_id == metric.organization_id
        and (
            project.workspace_id == metric.workspace_id
            or (project.workspace_id is None and metric.workspace.is_default)
        )
    )


def _requester_can_run(run: DerivedMetricRun) -> bool:
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    metric = run.definition.metric
    if not run.requested_by_id or not run.requested_by.is_active:
        return False
    org = OrganizationMembership.no_workspace_objects.filter(
        user_id=run.requested_by_id,
        organization_id=metric.organization_id,
        is_active=True,
        deleted=False,
    ).first()
    if org is None:
        return False
    if org.level_or_legacy >= Level.ADMIN:
        return True
    membership = WorkspaceMembership.no_workspace_objects.filter(
        user_id=run.requested_by_id,
        workspace_id=metric.workspace_id,
        is_active=True,
        deleted=False,
    ).first()
    return (
        membership is not None
        and max(org.level_or_legacy, membership.level_or_legacy)
        >= Level.WORKSPACE_MEMBER
    )


def execute_run(run_id: UUID | str) -> None:
    with transaction.atomic():
        run = (
            DerivedMetricRun.objects.select_for_update()
            .select_related(
                "definition__metric__project", "definition__metric__workspace"
            )
            .get(pk=run_id)
        )
        if run.status != DerivedMetricRun.Status.QUEUED:
            return
        if not _authorized_metric(run.definition.metric) or not _requester_can_run(run):
            run.status, run.error = (
                DerivedMetricRun.Status.FAILED,
                "Project or requester is no longer authorized in this workspace.",
            )
            run.save(update_fields=["status", "error"])
            return
        run.status, run.error = DerivedMetricRun.Status.RUNNING, ""
        run.attempt_id = uuid4()
        run.lease_expires_at = timezone.now() + timedelta(minutes=11)
        run.save(update_fields=["status", "error", "attempt_id", "lease_expires_at"])
    try:
        _compute(run)
    except Exception as exc:
        logger.exception("derived_metric_run_failed", run_id=str(run.id))
        DerivedMetricRun.objects.filter(pk=run.id, attempt_id=run.attempt_id).update(
            status=DerivedMetricRun.Status.FAILED,
            error=(
                str(exc)[:300]
                if isinstance(exc, MetricInputError)
                else "Metric computation failed. Check source/schema availability or reduce the window, then retry."
            ),
        )
        raise
    try:
        from tracer.services.derived_metrics.integrations import notify_run_monitors

        run.refresh_from_db()
        notify_run_monitors(run)
    except Exception:
        # A notification failure does not erase a successfully committed metric.
        # The monitor scheduler retries delivery against the same run id.
        logger.exception("derived_metric_notification_failed", run_id=str(run.id))


def _compute(run: DerivedMetricRun) -> None:
    definition = run.definition
    metric = definition.metric
    scope = Scope(metric.organization_id, metric.workspace_id, metric.project_id)
    client = get_clickhouse_client()
    if client.server_enforced_readonly:
        raise RuntimeError("Derived metrics require a dedicated writer connection.")
    if run.snapshot_id is None:
        snapshot_id = uuid4()
        # This is an observation watermark, not a claim to reconstruct overwritten
        # historical source rows. Subsequent replay uses this immutable snapshot.
        run.as_of = timezone.now()
        query, params = snapshot_select(
            definition,
            scope,
            run.window_start,
            run.window_end,
            run.as_of,
            definition.filters + run.filters,
            run.source_run_id,
        )
        params["snapshot_id"] = str(snapshot_id)
        client.execute(
            f"INSERT INTO derived_metric_inputs SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(snapshot_id)s, frozen.* FROM ({query}) frozen",
            params,
            settings=QUERY_SETTINGS,
        )
        captured_scope = client.execute(
            f"SELECT if(uniqExactIf(sampling_scope, notEmpty(sampling_scope)) = 1, anyIf(sampling_scope, notEmpty(sampling_scope)), '') FROM derived_metric_inputs WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s",
            params,
            settings=QUERY_SETTINGS,
        )[0][0]
        run.snapshot_id = snapshot_id
        run.source_manifest = {
            "schema_version": 1,
            "compiler_version": 1,
            "sampling_scope": captured_scope,
            "evaluator_table": eval_logger_source()[0],
            "definition_hash": definition.definition_hash,
            "captured_at": run.as_of.isoformat(),
            "semantics": "Frozen persisted inputs observed at capture; not historical time travel. Evaluator hashes are pinned in the definition.",
        }
        if not DerivedMetricRun.objects.filter(
            pk=run.id, attempt_id=run.attempt_id
        ).update(
            snapshot_id=snapshot_id,
            as_of=run.as_of,
            source_manifest=run.source_manifest,
        ):
            raise RuntimeError("Metric execution lease was superseded.")
    manifest = run.source_manifest or {}
    if (
        manifest.get("schema_version") != 1
        or manifest.get("compiler_version") != 1
        or manifest.get("definition_hash") != definition.definition_hash
    ):
        raise MetricInputError(
            "Frozen snapshot format or definition differs from this compiler. Create a new run; existing committed results are retained."
        )
    result_id = uuid4()
    for rollup in ("overall", "session", "user", "cohort", "day"):
        query, params = rollup_insert(definition, run, result_id, rollup)
        client.execute(query, params, settings={**QUERY_SETTINGS, "max_threads": 1})
    # Recheck project ownership before publication, not only before computation.
    metric.refresh_from_db()
    metric.project.refresh_from_db()
    metric.workspace.refresh_from_db()
    run.requested_by.refresh_from_db()
    if not _authorized_metric(metric) or not _requester_can_run(run):
        raise RuntimeError(
            "Metric scope or requester access changed during computation."
        )
    now = timezone.now()
    params = {
        **scope.parameters(),
        "metric_id": str(metric.id),
        "run_id": str(run.id),
        "result_id": str(result_id),
        "committed_at": now,
        "version": 0,
    }
    rows = client.execute(
        f"SELECT count() FROM derived_metric_results WHERE {SCOPE_SQL} AND metric_id = %(metric_id)s AND run_id = %(run_id)s AND result_id = %(result_id)s",
        params,
        settings=QUERY_SETTINGS,
    )
    with transaction.atomic():
        current = DerivedMetricRun.objects.select_for_update().get(pk=run.id)
        if (
            current.attempt_id != run.attempt_id
            or current.lease_expires_at <= timezone.now()
        ):
            raise RuntimeError("Metric execution lease expired before publication.")
        # Durable per-run ordering survives clock rollback and a previous CH
        # publish whose PostgreSQL acknowledgement failed. No timestamp tie.
        previous = client.execute(
            f"SELECT max(version) FROM derived_metric_commits WHERE {SCOPE_SQL} AND metric_id = %(metric_id)s AND run_id = %(run_id)s",
            params,
            settings=QUERY_SETTINGS,
        )
        params["version"] = int(previous[0][0] or 0) + 1
        client.execute(
            "INSERT INTO derived_metric_commits VALUES (%(org_id)s, %(workspace_id)s, %(project_id)s, %(metric_id)s, %(run_id)s, %(result_id)s, %(committed_at)s, %(version)s)",
            params,
            settings=QUERY_SETTINGS,
        )
        DerivedMetricRun.objects.filter(pk=run.id, attempt_id=run.attempt_id).update(
            status=DerivedMetricRun.Status.COMPLETE,
            result_id=result_id,
            row_count=rows[0][0],
            completed_at=now,
            error="",
        )


@transaction.atomic
def queue_recompute(run: DerivedMetricRun, user) -> DerivedMetricRun:
    run = DerivedMetricRun.objects.select_for_update().get(pk=run.id)
    if (
        run.status in {DerivedMetricRun.Status.RUNNING, DerivedMetricRun.Status.QUEUED}
        and run.lease_expires_at
        and run.lease_expires_at > timezone.now()
    ):
        raise MetricInputError("This run is already queued or running.")
    run.status, run.error = DerivedMetricRun.Status.QUEUED, ""
    run.attempt_id = None
    run.requested_by = user
    run.lease_expires_at = timezone.now() + timedelta(minutes=20)
    run.save(
        update_fields=[
            "status",
            "error",
            "attempt_id",
            "lease_expires_at",
            "requested_by",
        ]
    )
    return run


def read_results(
    run: DerivedMetricRun, rollup: str = "overall", after: str = "", limit: int = 50
) -> dict:
    if run.result_id is None:
        raise MetricInputError("This run does not have a committed result yet.")
    metric = run.definition.metric
    if not _authorized_metric(metric):
        raise MetricInputError("Metric project scope is no longer available.")
    has_cursor, after_key = decode_result_cursor(after, str(run.result_id), rollup)
    params = {
        **Scope(
            metric.organization_id, metric.workspace_id, metric.project_id
        ).parameters(),
        "metric_id": str(metric.id),
        "run_id": str(run.id),
        "result_id": str(run.result_id),
        "rollup": rollup,
        "has_cursor": has_cursor,
        "after_key": after_key,
        "limit": limit + 1,
    }
    query = f"""SELECT group_key, value, numerator, denominator, status, unit_count, valid_count, missing_count, invalid_probability_count
        FROM derived_metric_results WHERE {SCOPE_SQL} AND metric_id = %(metric_id)s AND run_id = %(run_id)s AND result_id = %(result_id)s AND rollup = %(rollup)s
        AND (NOT %(has_cursor)s OR group_key > %(after_key)s) ORDER BY group_key LIMIT %(limit)s"""
    rows, columns = get_clickhouse_client().execute(
        query, params, with_column_types=True, settings=QUERY_SETTINGS
    )
    names = [column[0] for column in columns]
    return {
        "results": [dict(zip(names, row, strict=True)) for row in rows[:limit]],
        "next_cursor": encode_result_cursor(
            str(run.result_id), rollup, rows[limit - 1][0]
        )
        if len(rows) > limit
        else None,
    }


def preview(
    definition,
    scope: Scope,
    start: datetime,
    end: datetime,
    filters: list[dict],
    source_run_id: str,
) -> dict:
    query, params = snapshot_select(
        definition,
        scope,
        start,
        end,
        timezone.now(),
        definition.filters + filters,
        source_run_id,
    )
    formula = compile_formula(
        definition.formula, [item["alias"] for item in definition.inputs]
    )
    client = get_clickhouse_client()
    if client.server_enforced_readonly:
        raise RuntimeError(
            "Metric preview requires the bounded computation connection."
        )
    rows, columns = client.execute(
        aggregate_select(query, formula, rollup="overall"),
        {**params, **formula.parameters},
        with_column_types=True,
        settings=QUERY_SETTINGS,
    )
    names = [column[0] for column in columns]
    return {
        "results": [dict(zip(names, row, strict=True)) for row in rows],
        "next_cursor": None,
    }
