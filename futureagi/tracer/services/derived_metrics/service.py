"""Version/manifest lifecycle. ClickHouse attempts publish only after completion."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager, nullcontext
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
from tracer.services.derived_metrics.queries import (
    PREVIEW_QUERY_SETTINGS,
    QUERY_SETTINGS,
    SCOPE_SQL,
    UNCERTAINTY_COLUMNS,
    Scope,
    rollup_insert,
    snapshot_select,
)
from tracer.services.derived_metrics.spec import (
    SEMANTIC_KEYS,
    normalize_spec,
    require_computable,
)
from tracer.services.derived_metrics.uncertainty import CONFIDENCE_LEVEL, NOT_COMPUTED
from tracer.services.derived_metrics.unit_reads import field_recipe

logger = structlog.get_logger(__name__)
SEMANTICS = "One contribution per selection unit; complete cases only; inverse-probability weights apply once. Missing inputs are not zero. Zero denominator and invalid probabilities produce no value. Replay uses frozen inputs, not mutable raw history."
DEFINITION_KEYS = SEMANTIC_KEYS


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
    from types import SimpleNamespace

    data = normalize_spec(data)
    compiled = field_recipe(SimpleNamespace(**data))
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
        "kind": data["kind"],
        "required_inputs": [item["alias"] for item in compiled.required_inputs],
        "semantics": SEMANTICS,
    }


@transaction.atomic
def save_definition(
    data: dict, project, workspace, user, metric: DerivedMetric | None = None
) -> DerivedMetric:
    validate_definition(data, project)
    recipe = _json_value(normalize_spec(data))
    from tracer.services.derived_metrics.deletion_protocol import project_lock

    project_lock(project.pk)
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
    from tracer.models.derived_metric import MetricProjectRefresh

    MetricProjectRefresh.objects.update_or_create(
        project=project, defaults={"pending_backfill": True}
    )
    return metric


@transaction.atomic
def create_run(
    metric: DerivedMetric, data: dict, user
) -> tuple[DerivedMetricRun, bool]:
    from tracer.services.derived_metrics.deletion_protocol import project_lock
    from tracer.services.derived_metrics.snapshots import SUBMISSION_PROTOCOL

    project_lock(metric.project_id)
    version = data.get(
        "version",
        metric.active_version.version
        if metric.active_version
        else metric.current_version,
    )
    definition = DerivedMetricVersion.objects.filter(
        metric=metric, version=version
    ).first()
    if definition is None:
        raise MetricInputError({"version": "Metric version does not exist."})
    require_computable(definition)
    if definition.kind != "custom" or definition.field_spec:
        raise MetricUnavailableError(
            "refresh_unavailable",
            "Use the committed refresh writer for v2 metric definitions, not raw frozen runs.",
        )
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
            "source_manifest": {"write_protocol": SUBMISSION_PROTOCOL},
            "lease_expires_at": timezone.now() + timedelta(minutes=20),
        },
    )
    _require_enrolled_run(run)
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


def _require_enrolled_run(run):
    from tracer.services.derived_metrics.snapshots import SUBMISSION_PROTOCOL

    # This feature has no deployed pre-protocol writer. create_run is the only
    # production raw-run constructor and enrolls before enqueue; fixture rows
    # may model impossible historical states, but no native path may adopt one.
    manifest = run.source_manifest
    if (
        not isinstance(manifest, dict)
        or manifest.get("write_protocol") != SUBMISSION_PROTOCOL
    ):
        raise MetricInputError(
            "Legacy run writer precondition failed: missing submission protocol. "
            "This run cannot execute or be retroactively enrolled."
        )


def execute_run(run_id: UUID | str) -> None:
    from tracer.services.derived_metrics.deletion_protocol import serialized_project

    project_id = DerivedMetricRun.objects.values_list(
        "definition__metric__project_id", flat=True
    ).get(pk=run_id)
    with serialized_project(project_id):
        run = (
            DerivedMetricRun.objects.select_for_update()
            .select_related(
                "definition__metric__project", "definition__metric__workspace"
            )
            .get(pk=run_id)
        )
        if run.deleted_at or run.status != DerivedMetricRun.Status.QUEUED:
            return
        if not _authorized_metric(run.definition.metric) or not _requester_can_run(run):
            run.status, run.error = (
                DerivedMetricRun.Status.FAILED,
                "Project or requester is no longer authorized in this workspace.",
            )
            run.save(update_fields=["status", "error"])
            return
        _require_enrolled_run(run)
        run.status, run.error = DerivedMetricRun.Status.RUNNING, ""
        run.attempt_id = uuid4()
        run.lease_expires_at = timezone.now() + timedelta(minutes=11)
        run.save(
            update_fields=[
                "status",
                "error",
                "attempt_id",
                "lease_expires_at",
                "source_manifest",
            ]
        )
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
    from tracer.services.derived_metrics.snapshots import snapshot_store

    _require_enrolled_run(run)
    with snapshot_store() as store:
        _compute_tracked(run, store)


def _input_capture_not_submitted(run):
    """Only a positive pre-dispatch receipt permits replacing allocated inputs.

    A namespace without a complete manifest is not automatically empty. A
    dispatched write with lost acknowledgement must remain fenced for drainage.
    """
    from tracer.models.derived_metric import MetricErasureRequest
    from tracer.services.derived_metrics.deletion_protocol import serialized_project
    from tracer.services.derived_metrics.snapshots import SUBMISSION_PROTOCOL, _current

    if run.source_manifest != {"write_protocol": SUBMISSION_PROTOCOL}:
        return False
    with serialized_project(run.definition.metric.project_id):
        _current(run)
        submissions = MetricErasureRequest.objects.filter(
            organization_key=run.definition.metric.organization_id,
            project_key=run.definition.metric.project_id,
            run_key=run.pk,
            snapshot_key=run.snapshot_id,
            store_payload__protocol=SUBMISSION_PROTOCOL,
        )
        count = submissions.count()
        return (
            bool(count)
            and count
            == submissions.filter(
                status="done", receipts__not_submitted__drained=1
            ).count()
        )


def _compute_tracked(run, store):
    from tracer.services.derived_metrics.deletion_protocol import serialized_project
    from tracer.services.derived_metrics.snapshots import SUBMISSION_PROTOCOL, _write

    _require_enrolled_run(run)
    definition = run.definition
    require_computable(definition)
    if definition.kind != "custom" or definition.field_spec:
        raise MetricInputError(
            "Use the committed refresh writer for v2 metric definitions; raw frozen runs are not supported."
        )
    metric = definition.metric
    scope = Scope(metric.organization_id, metric.workspace_id, metric.project_id)
    client = store.client
    if run.snapshot_id is None or _input_capture_not_submitted(run):
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
        # Persist the immutable input namespace BEFORE dispatch. Expiry can then
        # address even a capture whose native acknowledgement is lost.
        with serialized_project(metric.project_id):
            from tracer.services.derived_metrics.snapshots import _current

            _current(run)
            DerivedMetricRun.objects.filter(pk=run.pk).update(snapshot_id=snapshot_id)
            run.snapshot_id = snapshot_id
        params["snapshot_id"] = str(snapshot_id)
        _write(
            store,
            run,
            "INSERT INTO derived_metric_inputs "
            "(org_id, workspace_id, project_id, snapshot_id, unit_id, session_id, user_id, "
            "cohort, day, input_values, probability, invalid_probability, source_rows, "
            "sampling_scope, sampling_stratum, sampling_design, population_size) "
            f"SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(snapshot_id)s, frozen.* FROM ({query}) frozen",
            params,
            private=True,
        )
        captured_scope = client.execute(
            f"SELECT if(uniqExactIf(sampling_scope, notEmpty(sampling_scope)) = 1, anyIf(sampling_scope, notEmpty(sampling_scope)), '') FROM derived_metric_inputs WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s",
            params,
            settings=QUERY_SETTINGS,
        )[0][0]
        run.snapshot_id = snapshot_id
        run.source_manifest = {
            "write_protocol": SUBMISSION_PROTOCOL,
            "schema_version": 1,
            "compiler_version": 1,
            "sampling_scope": captured_scope,
            "evaluator_table": eval_logger_source()[0],
            "definition_hash": definition.definition_hash,
            "captured_at": run.as_of.isoformat(),
            "semantics": "Frozen persisted inputs observed at capture; not historical time travel. Evaluator hashes are pinned in the definition.",
        }
        if not DerivedMetricRun.objects.filter(
            pk=run.id, attempt_id=run.attempt_id, deleted_at__isnull=True
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
    result_id = run.attempt_id
    for rollup in ("overall", "session", "user", "cohort", "day"):
        query, params = rollup_insert(definition, run, result_id, rollup)
        _write(store, run, query, params)
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
    from tracer.services.derived_metrics.source_retention import require_run

    require_run(run)
    previous = client.execute(
        f"SELECT max(version) FROM derived_metric_commits WHERE {SCOPE_SQL} AND metric_id = %(metric_id)s AND run_id = %(run_id)s",
        params,
        settings=QUERY_SETTINGS,
    )
    params["version"] = int(previous[0][0] or 0) + 1
    _write(
        store,
        run,
        "INSERT INTO derived_metric_commits VALUES (%(org_id)s, %(workspace_id)s, %(project_id)s, %(metric_id)s, %(run_id)s, %(result_id)s, %(committed_at)s, %(version)s)",
        params,
    )
    require_run(run)
    with serialized_project(metric.project_id):
        current = DerivedMetricRun.objects.select_for_update().get(pk=run.id)
        if (
            current.deleted_at is not None
            or current.attempt_id != run.attempt_id
            or current.lease_expires_at <= timezone.now()
        ):
            raise RuntimeError("Metric execution lease expired before publication.")
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
    if run.kind == "snapshot":
        raise MetricInputError(
            "Saved snapshots cannot be rerun. Save a new snapshot instead."
        )
    from tracer.services.derived_metrics.source_retention import window_retained

    if run.deleted_at or not window_retained(
        run.definition.metric.organization_id, run.window_start
    ):
        raise MetricInputError(
            "This result is no longer available under source retention."
        )
    _require_enrolled_run(run)
    require_computable(run.definition)
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


def read_results(run, rollup="overall", after="", limit=50, include_uncertainty=False):
    from tracer.services.derived_metrics.snapshots import snapshot_read

    with snapshot_read(run) as current:
        return _read_results(current, rollup, after, limit, include_uncertainty)


def _read_results(
    run: DerivedMetricRun,
    rollup: str = "overall",
    after: str = "",
    limit: int = 50,
    include_uncertainty: bool = False,
) -> dict:
    if getattr(run, "deleted_at", None) or (
        getattr(run, "kind", "run") == "snapshot" and run.status != "complete"
    ):
        raise MetricInputError(
            "This snapshot does not have an available committed result."
        )
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
PREVIEW_ACTOR_SLOTS = 1
PREVIEW_ORG_SLOTS = 4
PREVIEW_LEASE_SECONDS = PREVIEW_QUERY_SETTINGS["max_execution_time"] + 15
PREVIEW_MAX_ROWS = 1
BUDGET_ERROR_CODES = frozenset({158, 159, 160, 241, 307, 396})


class MetricUnavailableError(Exception):
    def __init__(
        self,
        code="metric_unavailable",
        message="Metric reads are temporarily unavailable. Retry after a committed refresh.",
        *,
        value_mode=None,
        calculated_through=None,
        reason=None,
    ):
        self.code, self.message = code, message
        self.value_mode = value_mode
        self.calculated_through = calculated_through
        self.reason = reason
        super().__init__(message)


class PreviewSupersededError(Exception):
    pass


class SnapshotNotSupportedError(MetricInputError):
    pass


@contextmanager
def _admission_lock(organization_id):
    """Serialize cache lease changes, including compare-and-delete on release.

    The cache must be shared in production (as it was for the old guard). The
    lock never encloses a telemetry query; its TTL is a crash-recovery bound.
    """
    import time

    key = f"derived-metric-preview:lock:{organization_id}"
    token = uuid4().hex
    deadline = time.monotonic() + 2
    while not cache.add(key, token, timeout=10):
        if time.monotonic() >= deadline:
            raise MetricUnavailableError(
                message="Preview admission is temporarily unavailable. Retry."
            )
        time.sleep(0.01)
    try:
        yield
    finally:
        if cache.get(key) == token:
            cache.delete(key)


def _actor_key(actor_id, organization_id):
    return f"derived-metric-preview:actor:{organization_id}:{actor_id}"


def _cancel_preview_query(query_id):
    if not query_id:
        return
    # query_id is generated here, never supplied by a caller. KILL uses a
    # separate pooled connection; cancelling must not wait on the busy driver.
    get_clickhouse_client().execute(
        "KILL QUERY WHERE query_id = %(preview_query_id)s SYNC",
        {"preview_query_id": query_id},
        settings={"max_execution_time": 2},
    )


def _execution_key(state):
    return f"derived-metric-preview:executing:{state['token']}"


def _release_preview(actor_id, organization_id, state):
    with _admission_lock(organization_id):
        key = _actor_key(actor_id, organization_id)
        current = cache.get(key)
        if current and current["token"] == state["token"]:
            cache.delete(key)
        if cache.get(state["slot"]) == state["token"]:
            cache.delete(state["slot"])


def _stop_preview(state):
    """Acknowledge cancellation only after the executing worker has stopped.

    KILL alone is insufficient: it may arrive just before native registration.
    The phase marker is acquired under the same lock as revocation. Retain the
    slot and retry KILL until the worker acknowledges in its finally block. A
    failed/slow cancellation never frees capacity for another running query.
    """
    import time

    deadline = time.monotonic() + 2
    while True:
        _cancel_preview_query(state.get("query_id"))
        if not cache.get(_execution_key(state)):
            return
        if time.monotonic() >= deadline:
            raise MetricUnavailableError(
                "preview_cancellation_pending",
                "Preview cancellation is pending. Retry.",
            )
        time.sleep(0.01)


@contextmanager
def preview_admission(actor_id, organization_id, *, request_id=None, metric_id=None):
    """Replace this actor's last request after its execution acknowledges stop."""
    key = _actor_key(actor_id, organization_id)
    state = {
        "token": uuid4().hex,
        "request_id": str(request_id or uuid4()),
        "metric_id": str(metric_id or ""),
        "query_id": None,
        "cancelled": False,
    }
    while True:
        with _admission_lock(organization_id):
            previous = cache.get(key)
            if previous:
                previous["cancelled"] = True
                cache.set(key, previous, timeout=PREVIEW_LEASE_SECONDS)
            else:
                slot = next(
                    (
                        f"derived-metric-preview:org:{organization_id}:{index}"
                        for index in range(PREVIEW_ORG_SLOTS)
                        if cache.get(
                            f"derived-metric-preview:org:{organization_id}:{index}"
                        )
                        is None
                    ),
                    None,
                )
                if slot is None:
                    raise MetricBusyError(
                        "Metric previews are busy in this organization. Retry shortly.",
                        retry_after=PREVIEW_LEASE_SECONDS,
                    )
                state["slot"] = slot
                cache.set(slot, state["token"], timeout=PREVIEW_LEASE_SECONDS)
                cache.set(key, state, timeout=PREVIEW_LEASE_SECONDS)
                break
        _stop_preview(previous)
        _release_preview(actor_id, organization_id, previous)
    try:
        yield state
    finally:
        _release_preview(actor_id, organization_id, state)


def _check_preview_current(actor_id, organization_id, state):
    current = cache.get(_actor_key(actor_id, organization_id))
    if (
        not current
        or current["token"] != state["token"]
        or current.get("cancelled")
        or cache.get(state["slot"]) != state["token"]
    ):
        raise PreviewSupersededError(
            "This preview was cancelled or superseded by a newer request."
        )


@contextmanager
def _preview_phase(actor_id, organization_id, state):
    # Must run AFTER pool acquisition and separately for every native statement.
    # Revocation cannot acknowledge stop between this check and the phase marker.
    with _admission_lock(organization_id):
        _check_preview_current(actor_id, organization_id, state)
        # Pool waiting must not consume the lease protecting a running query.
        cache.set(state["slot"], state["token"], timeout=PREVIEW_LEASE_SECONDS)
        cache.set(
            _actor_key(actor_id, organization_id), state, timeout=PREVIEW_LEASE_SECONDS
        )
        cache.set(_execution_key(state), True, timeout=PREVIEW_LEASE_SECONDS)
    try:
        yield
    finally:
        cache.delete(_execution_key(state))


def cancel_preview(actor_id, organization_id, metric_id, request_id):
    key = _actor_key(actor_id, organization_id)
    with _admission_lock(organization_id):
        state = cache.get(key)
        if (
            not state
            or state["request_id"] != str(request_id)
            or state["metric_id"] != str(metric_id)
        ):
            return {"cancelled": False}
        state["cancelled"] = True
        cache.set(key, state, timeout=PREVIEW_LEASE_SECONDS)
    _stop_preview(state)
    _release_preview(actor_id, organization_id, state)
    return {"cancelled": True}


def _preview_aggregate(query, recipe, params):
    if recipe.kind not in {"unique_count", "percentile"}:
        params.update(recipe.formula.parameters)
        from tracer.services.derived_metrics.parts import (
            combine_parts_select,
            stratum_parts_select,
        )

        return combine_parts_select(
            stratum_parts_select(query, recipe.formula, scope_column="sampling_scope"),
            recipe.formula,
        )
    # The same typed reduced value feeds the refresh sketch. Preview computes
    # one bounded sketch directly; no numeric coercion of text/yes-no values.
    mapping = {
        "number": "input_values",
        "boolean": "inputs_bool",
        "text": "inputs_text",
    }[recipe.value_type]
    value = f"{mapping}['{recipe.value_alias}']"
    present = f"isNotNull({value})" + (
        f" AND isFinite({value})" if recipe.value_type == "number" else ""
    )
    params["preview_percentile"] = recipe.source.field_spec.get("percentile", 0.95)
    aggregate = (
        "uniqExactIf(source_value, present)"
        if recipe.kind == "unique_count"
        else "quantileTDigestIf(%(preview_percentile)s)(source_value, present)"
    )
    # A weighted distinct/quantile estimate isn't implemented. Never substitute
    # an unweighted estimate for a sampled population.
    return f"""SELECT '' AS group_key, if(status = 'ok', toFloat64(raw_value), NULL) AS value,
        value AS numerator, CAST(NULL, 'Nullable(Float64)') AS denominator, status,
        unit_count, valid_count, missing_count, invalid_probability_count,
        'not_estimable' AS ci_status, 'not_computed' AS ci_reason, '' AS ci_method, '' AS ci_interval,
        CAST(NULL, 'Nullable(Float64)') AS std_error, CAST(NULL, 'Nullable(Float64)') AS ci_low,
        CAST(NULL, 'Nullable(Float64)') AS ci_high, CAST(NULL, 'Nullable(Float64)') AS effective_n,
        CAST(NULL, 'Nullable(UInt64)') AS ci_df
        FROM (SELECT count() AS unit_count, countIf(present) AS valid_count,
            countIf(NOT present) AS missing_count,
            if(uniqExactIf(sampling_scope, notEmpty(sampling_scope)) > 1, countIf(notEmpty(sampling_scope)), countIf(invalid_probability != 0 OR ifNull(probability != 1, 1))) AS invalid_probability_count,
            {aggregate} AS raw_value,
            multiIf(unit_count = 0, 'no_data', invalid_probability_count > 0, 'invalid_probability', valid_count = 0, 'missing_inputs', NOT isFinite(toFloat64(raw_value)), 'non_finite', 'ok') AS status
            FROM (SELECT *, {value} AS source_value, ifNull({present}, 0) AS present FROM ({query})))"""


def _preview_samples(query, recipe, params):
    """Bounded typed evidence from the SAME settled source as the aggregate.

    One array result keeps the original ten-row server result cap unchanged.
    Missing source values are represented as No value, not false/zero.
    """
    from tracer.services.derived_metrics.typed_fields import reduced_type

    maps = {"number": "input_values", "boolean": "inputs_bool", "text": "inputs_text"}
    if recipe.formula:
        formula = recipe.formula
        params.update(formula.parameters)
        unpack = ", ".join(
            f"input_values['{name}'] AS v_{name}" for name in formula.required
        )
        ratio = formula.kind in {"mean", "ratio"}
        raw = "part_n / nullIf(part_d, 0)" if ratio else "part_n"
        if recipe.value_type == "boolean":
            raw = "toUInt8(part_n != 0)"
        zero = "part_d = 0" if ratio else "0"
        missing = " OR ".join(f"isNull(v_{name})" for name in formula.required)
        complete = f"({formula.complete_sql}) AND isNotNull(part_n) AND isFinite(part_n) AND isNotNull(part_d) AND isFinite(part_d)"
        evidence = f"SELECT *, {unpack}, {formula.numerator} AS part_n, {formula.denominator} AS part_d FROM ({query})"
    else:
        raw = f"{maps[recipe.value_type]}['{recipe.value_alias}']"
        zero, missing, complete, evidence = (
            "0",
            f"isNull({raw})",
            f"isNotNull({raw})",
            query,
        )
    missing_evals = (
        " OR ".join(
            f"isNull({maps[reduced_type(item)]}['{item['alias']}'])"
            for item in recipe.required_inputs
            if item["source"] == "eval"
        )
        or "0"
    )
    finite = "isFinite(computed_value)" if recipe.value_type == "number" else "1"
    available = "if(reason = '', computed_value, NULL)"
    values = [
        f"CAST({available if kind == recipe.value_type else 'NULL'}, 'Nullable({cast})')"
        for kind, cast in (
            ("number", "Float64"),
            ("boolean", "UInt8"),
            ("text", "String"),
        )
    ]
    return f"""SELECT groupArray(20)(tuple(unit_id, reason, {", ".join(values)}, input_values, inputs_bool, inputs_text)) AS sample_units
        FROM (SELECT *, {raw} AS computed_value,
            multiIf(invalid_probability != 0, 'invalid_probability', {missing},
                if({missing_evals}, 'not_evaluated', 'attribute_absent'),
                {zero}, 'zero_denominator', NOT ({complete}) OR NOT ({finite}), 'non_finite', '') AS reason
            FROM ({evidence}) ORDER BY unit_start DESC, unit_id LIMIT 20)"""


def _shape_preview_samples(rows, definition):
    samples = []
    for unit_id, reason, number, boolean, text, inputs, bools, texts in rows:
        samples.append(
            {
                "unit_id": unit_id,
                "status": "no_value" if reason else "value",
                "no_value_reason": reason,
                "value_number": number,
                "value_bool": None if boolean is None else bool(boolean),
                "value_text": text,
                "value_label": (
                    definition.value_labels["match" if boolean else "other"]
                    if not reason and boolean is not None and definition.value_labels
                    else ""
                ),
                "inputs": inputs,
                "inputs_bool": {
                    key: None if value is None else bool(value)
                    for key, value in bools.items()
                },
                "inputs_text": texts,
            }
        )
    return samples


def _preview_evaluated(
    connection,
    query,
    recipe,
    params,
    query_id,
    check_current,
    *,
    phase=nullcontext,
    include_samples=False,
):
    """Materialize reduced evidence on the leased connection, never in Python.

    CH 25.3's analyzer expands the evaluation-lineage expressions repeatedly
    beneath the variance estimator (the two-input fixture exceeded 15 seconds).
    A connection-local Memory table is an optimizer boundary, not a saved run.
    Materialization, aggregation and optional sample evidence share the original
    15-second deadline and abort budgets.
    """
    import time

    table = "metric_preview_" + uuid4().hex
    deadline = time.monotonic() + PREVIEW_QUERY_SETTINGS["max_execution_time"]

    def remaining_settings():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MetricInputError(
                {
                    "window": "This preview exceeded its interactive read budget. Choose a shorter window or narrower filters, then retry."
                }
            )
        return {**PREVIEW_QUERY_SETTINGS, "max_execution_time": remaining}

    try:
        with phase():
            check_current()
            connection.execute(
                f"CREATE TEMPORARY TABLE {table} ENGINE=Memory AS {query}",
                params,
                query_id=query_id,
                settings=PREVIEW_QUERY_SETTINGS,
            )
        check_current()
        with phase():
            result = connection.execute(
                _preview_aggregate(f"SELECT * FROM {table}", recipe, params),
                params,
                query_id=query_id,
                with_column_types=True,
                settings=remaining_settings(),
            )
        samples = []
        if include_samples:
            check_current()
            with phase():
                samples = connection.execute(
                    _preview_samples(f"SELECT * FROM {table}", recipe, params),
                    params,
                    query_id=query_id,
                    settings=remaining_settings(),
                )[0][0]
        return (*result, samples)
    finally:
        try:
            connection.execute(
                f"DROP TEMPORARY TABLE IF EXISTS {table}",
                settings={"max_execution_time": 2},
            )
        except Exception:
            # Never return a connection carrying somebody's preview evidence to
            # the pool if cleanup failed. Disconnect destroys its temp tables.
            connection.disconnect()


def preview(
    definition,
    scope: Scope,
    start: datetime,
    end: datetime,
    filters: list[dict],
    source_run_id: str,
    *,
    actor_id,
    request_id=None,
    include_samples=False,
) -> dict:
    require_computable(definition)
    require_satisfiable_filters(definition, filters)
    if not start < end or end - start > timedelta(days=7) or end > timezone.now():
        raise MetricInputError(
            {"window": "Preview requires a closed window of at most 7 days."}
        )
    if definition.level == "user":
        raise MetricUnavailableError(
            "user_preview_unavailable",
            "User preview is not available until the shared user-level reader is installed.",
            value_mode="on_demand",
        )
    if definition.level == "trace":
        raise MetricUnavailableError(
            "trace_preview_unavailable",
            "Trace preview is not available until the measured trace reader is installed.",
        )
    from tracer.services.derived_metrics.source_retention import require_window

    require_window(scope.organization, start)
    recipe = field_recipe(definition)
    recipe.source.selection_unit = "span" if definition.level == "span" else "session"
    from tracer.models.derived_metric import MetricProjectRefresh

    settle = (
        MetricProjectRefresh.objects.filter(project_id=scope.project)
        .values_list("settle_minutes", flat=True)
        .first()
    )
    settle = 30 if settle is None else settle
    with preview_admission(
        actor_id,
        scope.organization,
        request_id=request_id,
        metric_id=definition.metric_id,
    ) as admitted:
        # Full membership before start/settle predicates, just like refresh. An
        # old first span must not disappear because only its later root is in range.
        query, params = snapshot_select(
            recipe.source,
            scope,
            start,
            end,
            timezone.now(),
            definition.filters + filters,
            source_run_id,
            unit_predicate="1",
            include_evidence=True,
        )
        params["preview_settle_minutes"] = settle
        settled = "fromUnixTimestamp64Nano(toInt64(last_arrival_ns)) + toIntervalMinute(%(preview_settle_minutes)s) <= toDateTime64(%(as_of)s, 6, 'UTC')"
        if definition.level == "span":
            # Span lifecycle is end_time, not the conversation inactivity timer.
            # Collapse before this mutable end/deletion predicate as in B03.
            settled = """unit_id IN (SELECT toJSONString(tuple(toString(trace_id), toString(id))) FROM (
                SELECT id, trace_id, end_time, is_deleted FROM spans
                PREWHERE project_id = %(project_id)s
                WHERE (org_id = %(org_id)s OR isNull(org_id)) AND created_at <= %(as_of)s AND updated_at <= %(as_of)s
                ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id
            ) WHERE is_deleted = 0 AND isNotNull(end_time) AND end_time <= %(as_of)s)"""
        query = f"""SELECT * FROM ({query}) WHERE unit_start >= toDateTime64(%(start)s, 6, 'UTC')
            AND unit_start < toDateTime64(%(end)s, 6, 'UTC') AND ({settled})"""
        # B03's scoped parts reject mixed scopes at this settled read window,
        # without a per-unit window expansion of every input expression.
        client = get_clickhouse_client()
        if client.server_enforced_readonly:
            raise MetricUnavailableError(
                message="Metric preview requires the bounded computation connection."
            )
        query_id = "metric-preview-" + admitted["token"]
        with _admission_lock(scope.organization):
            _check_preview_current(actor_id, scope.organization, admitted)
            admitted["query_id"] = query_id
            cache.set(
                _actor_key(actor_id, scope.organization),
                admitted,
                timeout=PREVIEW_LEASE_SECONDS,
            )
        try:
            _check_preview_current(actor_id, scope.organization, admitted)
            # The pooled wrapper's execute has no query_id parameter. A leased
            # native connection supplies one, so replace/cancel can abort it.
            with client.connection() as connection:
                _check_preview_current(actor_id, scope.organization, admitted)
                samples = []
                if include_samples or any(
                    item["source"] == "eval" for item in recipe.source.inputs
                ):
                    rows, columns, samples = _preview_evaluated(
                        connection,
                        query,
                        recipe,
                        params,
                        query_id,
                        lambda: _check_preview_current(
                            actor_id, scope.organization, admitted
                        ),
                        phase=lambda: _preview_phase(
                            actor_id, scope.organization, admitted
                        ),
                        include_samples=include_samples,
                    )
                else:
                    aggregate = _preview_aggregate(query, recipe, params)
                    with _preview_phase(actor_id, scope.organization, admitted):
                        rows, columns = connection.execute(
                            aggregate,
                            params,
                            query_id=query_id,
                            with_column_types=True,
                            settings=PREVIEW_QUERY_SETTINGS,
                        )
            _check_preview_current(actor_id, scope.organization, admitted)
        except Exception as exc:
            _check_preview_current(actor_id, scope.organization, admitted)
            if getattr(exc, "code", None) in BUDGET_ERROR_CODES:
                raise MetricInputError(
                    {
                        "window": "This preview exceeded its interactive read budget. Choose a shorter window or narrower filters, then retry."
                    }
                ) from exc
            raise
    require_window(scope.organization, start)
    names = [column[0] for column in columns]
    results = [
        shape_result(dict(zip(names, row, strict=True)), include_uncertainty=True)
        for row in rows[:PREVIEW_MAX_ROWS]
    ]
    return {
        "results": results,
        "next_cursor": None,
        "unit_noun": "spans" if definition.level == "span" else "conversations",
        "value_mode": "materialized",
        "rate_eligible": bool(recipe.formula and recipe.formula.rate_eligible),
        "weighted": definition.weighting == "inverse_probability",
        "unit": definition.unit,
        "value_format": definition.value_format,
        "point_status": results[0]["status"] if results else "no_data",
        "summary": "Preview of settled units; missing inputs are not zero.",
        **(
            {"sample_units": _shape_preview_samples(samples, definition)}
            if include_samples
            else {}
        ),
    }


def admit_refresh(metric):
    """Admit an authorized definition to B10's durable, throttled project queue."""
    from tracer.services.derived_metrics.admission import (
        RefreshAdmissionUnavailable,
        request_refresh,
    )
    from tracer.services.derived_metrics.feature_flag import metrics_enabled

    if not metrics_enabled(org=metric.organization_id, project=metric.project_id):
        raise MetricUnavailableError("metrics_disabled", "Metrics are not enabled.")
    definition = metric.active_version or metric.versions.get(
        version=metric.current_version
    )
    require_computable(definition)
    try:
        queued = request_refresh(metric.project_id)
    except RefreshAdmissionUnavailable as exc:
        raise MetricUnavailableError(
            "refresh_unavailable",
            "Refresh admission is temporarily unavailable. Retry this request.",
        ) from exc
    return {"status": "queued" if queued else "already_queued"}


def activate_definition(metric, data):
    definition = DerivedMetricVersion.objects.filter(
        metric=metric, version=data["version"]
    ).first()
    if definition is None:
        raise MetricInputError({"version": "Metric version does not exist."})
    require_computable(definition)
    from tracer.models.derived_metric import MetricStateConflict

    try:
        DerivedMetric.activate_version(
            metric.id, definition.id, expected_active_id=data["expected_active_id"]
        )
    except MetricStateConflict as exc:
        raise MetricInputError({"version": str(exc)}) from exc


def request_snapshot(metric, data, user):
    from tracer.services.derived_metrics.snapshots import request_snapshot as capture

    return capture(metric, data, user)


def delete_snapshot(metric, snapshot_id, user):
    from tracer.services.derived_metrics.snapshots import delete_snapshot as remove

    return remove(metric, snapshot_id, user)
