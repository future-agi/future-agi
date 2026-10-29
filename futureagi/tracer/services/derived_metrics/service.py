"""Version/manifest lifecycle. ClickHouse attempts publish only after completion."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import structlog
from django.core.cache import cache
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
from tracer.services.derived_metrics.errors import MetricBusyError, MetricInputError
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.queries import (
    PREVIEW_QUERY_SETTINGS,
    QUERY_SETTINGS,
    SCOPE_SQL,
    UNCERTAINTY_COLUMNS,
    Scope,
    aggregate_select,
    rollup_insert,
    snapshot_select,
)
from tracer.services.derived_metrics.uncertainty import CONFIDENCE_LEVEL, NOT_COMPUTED

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


def list_evaluators(project, search: str = "", limit: int = 50) -> dict:
    """Project evaluators with the config_hash their current definition persists under."""
    from tracer.services.eval_tasks.config_hash import resolved_config_hash

    queryset = CustomEvalConfig.objects.filter(
        project=project, deleted=False
    ).select_related("eval_template")
    if search:
        queryset = queryset.filter(name__icontains=search)
    rows = list(queryset.order_by("name", "id")[: limit + 1])
    return {
        "results": [
            {
                "id": config.id,
                "name": config.name or "",
                "config_hash": resolved_config_hash(config),
            }
            for config in rows[:limit]
        ],
        "truncated": len(rows) > limit,
    }


def conflicting_filter_field(filters: list[dict]) -> str | None:
    """A field pinned to two different exact values can only select nothing."""
    seen: dict[str, str] = {}
    for item in filters:
        if seen.setdefault(item["field"], item["value"]) != item["value"]:
            return item["field"]
    return None


def require_satisfiable_filters(definition, run_filters: list[dict]) -> None:
    """A run's filters are ANDed with the version's own; contradictions select nothing."""
    conflict = conflicting_filter_field([*definition.filters, *run_filters])
    if conflict:
        raise MetricInputError(
            {
                "filters": (
                    f"This run's filter on '{conflict}' contradicts the saved version's "
                    "exact filter, so no span can match. Remove or align it."
                )
            }
        )


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
    if data["weighting"] == "inverse_probability":
        if not config_ids:
            raise MetricInputError(
                {
                    "weighting": "Weighting requires persisted sampling metadata on an evaluation input."
                }
            )
        unversioned = [
            item["alias"]
            for item in data["inputs"]
            if item["source"] == "eval" and not item.get("config_hash")
        ]
        if unversioned:
            # An empty hash selects only legacy unversioned results. Sampled
            # results always carry the evaluator's resolved config_hash, so this
            # combination can never find a weight: every unit would be missing.
            raise MetricInputError(
                {
                    "inputs": (
                        "Weighted metrics need the exact evaluator config_hash that produced "
                        "the sampled results; an empty hash selects only legacy unversioned "
                        "results, which have no sampling metadata. Select the evaluator "
                        "or enter its hash for: " + ", ".join(unversioned) + "."
                    )
                }
            )
    conflict = conflicting_filter_field(data.get("filters") or [])
    if conflict:
        raise MetricInputError(
            {
                "filters": (
                    f"Filters are combined with AND, so different exact values for "
                    f"'{conflict}' can never match a span. Keep one value per field."
                )
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
    require_satisfiable_filters(definition, data["filters"])
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
    from tracer.models.project import Project

    project = metric.project
    # Jobs reauthorize after dequeue. Default-workspace legacy projects have
    # null workspace; no job may follow a project across an organization boundary.
    return (
        not metric.deleted
        and not metric.workspace.deleted
        and metric.workspace.is_active
        and metric.workspace.organization_id == metric.organization_id
        and not project.deleted
        # A stored FK is not admission: use the same simulation-excluding
        # manager as the HTTP boundary, including for jobs and monitor reads.
        and Project.no_workspace_objects.filter(pk=project.pk).exists()
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


RESULT_COLUMNS = (
    "group_key",
    "value",
    "numerator",
    "denominator",
    "status",
    "unit_count",
    "valid_count",
    "missing_count",
    "invalid_probability_count",
)


def shape_result(row: dict, *, include_uncertainty: bool) -> dict:
    """The stable result row, plus the nested 95% interval when requested.

    Rows persisted before uncertainty existed have an empty ci_status; they are
    reported as not estimable rather than as an interval of unknown provenance.
    """
    shaped = {name: row[name] for name in RESULT_COLUMNS}
    if include_uncertainty:
        computed = bool(row.get("ci_status"))
        shaped["uncertainty"] = {
            "confidence_level": CONFIDENCE_LEVEL,
            "status": row["ci_status"] if computed else "not_estimable",
            "reason": row["ci_reason"] if computed else NOT_COMPUTED,
            "method": row.get("ci_method") or "",
            "interval": row.get("ci_interval") or "",
            "std_error": row.get("std_error"),
            "ci_low": row.get("ci_low"),
            "ci_high": row.get("ci_high"),
            "effective_n": row.get("effective_n"),
            "degrees_of_freedom": row.get("ci_df"),
        }
    return shaped


def read_results(
    run: DerivedMetricRun,
    rollup: str = "overall",
    after: str = "",
    limit: int = 50,
    include_uncertainty: bool = False,
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
    selected = RESULT_COLUMNS + (UNCERTAINTY_COLUMNS if include_uncertainty else ())
    query = f"""SELECT {", ".join(selected)}
        FROM derived_metric_results WHERE {SCOPE_SQL} AND metric_id = %(metric_id)s AND run_id = %(run_id)s AND result_id = %(result_id)s AND rollup = %(rollup)s
        AND (NOT %(has_cursor)s OR group_key > %(after_key)s) ORDER BY group_key LIMIT %(limit)s"""
    rows, columns = get_clickhouse_client().execute(
        query, params, with_column_types=True, settings=QUERY_SETTINGS
    )
    names = [column[0] for column in columns]
    return {
        "results": [
            shape_result(
                dict(zip(names, row, strict=True)),
                include_uncertainty=include_uncertainty,
            )
            for row in rows[:limit]
        ],
        # Use the same identity as the predicate even if the caller's run changes.
        "result_id": params["result_id"],
        "next_cursor": encode_result_cursor(
            params["result_id"], rollup, rows[limit - 1][0]
        )
        if len(rows) > limit
        else None,
    }


# --- Interactive preview ---------------------------------------------------------
# Preview is a synchronous ClickHouse read on a web worker, outside the single-slot
# background lane. It is bounded three ways: (1) a cross-process (cache-backed)
# concurrency guard per actor and per organization, so one user or tenant cannot
# occupy the worker pool or the database; (2) a much smaller server-side abort
# budget than a persisted run (PREVIEW_QUERY_SETTINGS: rows/bytes read, memory,
# group-by, execution time, result rows; every limit throws, nothing truncates);
# (3) the single overall row it returns. A guard lease outlives the longest allowed
# query, so a crashed worker releases its slot by expiry instead of blocking forever.
PREVIEW_ACTOR_SLOTS = 1
PREVIEW_ORG_SLOTS = 4
PREVIEW_LEASE_SECONDS = PREVIEW_QUERY_SETTINGS["max_execution_time"] + 15
PREVIEW_MAX_ROWS = 1
# ClickHouse server codes for an aborted read budget: TOO_MANY_ROWS, TIMEOUT_EXCEEDED,
# TOO_SLOW, MEMORY_LIMIT_EXCEEDED, TOO_MANY_BYTES, TOO_MANY_ROWS_OR_BYTES.
BUDGET_ERROR_CODES = frozenset({158, 159, 160, 241, 307, 396})


def _acquire_slot(prefix: str, limit: int, token: str) -> str | None:
    for index in range(limit):
        key = f"{prefix}:{index}"
        if cache.add(key, token, timeout=PREVIEW_LEASE_SECONDS):
            return key
    return None


@contextmanager
def preview_admission(actor_id, organization_id):
    """Hold one actor slot and one organization slot for the duration of a preview."""
    token = uuid4().hex
    held: list[str] = []
    try:
        for scope, identity, limit in (
            ("actor", actor_id, PREVIEW_ACTOR_SLOTS),
            ("org", organization_id, PREVIEW_ORG_SLOTS),
        ):
            key = _acquire_slot(
                f"derived-metric-preview:{scope}:{identity}", limit, token
            )
            if key is None:
                raise MetricBusyError(
                    "A metric preview is already running"
                    + (" for you" if scope == "actor" else " in this organization")
                    + ". Wait for it to finish, then retry.",
                    retry_after=PREVIEW_LEASE_SECONDS,
                )
            held.append(key)
        yield
    finally:
        for key in held:
            # Release only our own lease; an expired one may belong to someone else.
            if cache.get(key) == token:
                cache.delete(key)


def preview(
    definition,
    scope: Scope,
    start: datetime,
    end: datetime,
    filters: list[dict],
    source_run_id: str,
    *,
    actor_id,
) -> dict:
    require_satisfiable_filters(definition, filters)
    formula = compile_formula(
        definition.formula, [item["alias"] for item in definition.inputs]
    )
    with preview_admission(actor_id, scope.organization):
        query, params = snapshot_select(
            definition,
            scope,
            start,
            end,
            timezone.now(),
            definition.filters + filters,
            source_run_id,
        )
        client = get_clickhouse_client()
        if client.server_enforced_readonly:
            raise RuntimeError(
                "Metric preview requires the bounded computation connection."
            )
        try:
            rows, columns = client.execute(
                aggregate_select(query, formula, rollup="overall"),
                {**params, **formula.parameters},
                with_column_types=True,
                settings=PREVIEW_QUERY_SETTINGS,
            )
        except Exception as exc:
            if getattr(exc, "code", None) in BUDGET_ERROR_CODES:
                logger.warning("derived_metric_preview_budget", code=exc.code)
                raise MetricInputError(
                    {
                        "window": "This preview exceeded its interactive read budget. "
                        "Choose a shorter window or narrower filters, or run and persist "
                        "the window instead."
                    }
                ) from exc
            raise
    names = [column[0] for column in columns]
    return {
        "results": [
            shape_result(dict(zip(names, row, strict=True)), include_uncertainty=True)
            for row in rows[:PREVIEW_MAX_ROWS]
        ],
        "next_cursor": None,
    }
