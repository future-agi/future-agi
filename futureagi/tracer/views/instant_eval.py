"""
Instant eval's History, and what the run endpoints share.

GET /tracer/instant-eval/runs/   past Instant eval runs of a project, newest
                                 first, with their answer split

The runs themselves (estimate, start, summary, rows, export, control) are in
``tracer.views.instant_eval_scale``.
"""

from datetime import UTC

from django.core.cache import cache
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.models.eval_task import EvalTask, RunType
from tracer.models.instant_eval import InstantEvalRun
from tracer.models.project import Project
from tracer.services.instant_eval_run import _iso, _task_ids, asked_order
from tracer.views._license import InstantEvalLicenseRequired

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    404: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
    503: ApiErrorResponseSerializer,
}

# The most rows a run that names none is taken to have answered, when its
# rows are read back from the eval logger.
MAX_ANSWER_ROWS = 2000


def _org(request):
    return getattr(request, "organization", None) or request.user.organization


def _project(request, project_id):
    return Project.objects.filter(
        id=project_id, organization=_org(request), deleted=False
    ).first()


# ─── History ────────────────────────────────────────────────────────────────
HISTORY_SCAN = 500
INSTANT_TASK_PREFIX = "Instant eval"
# A finished run's history line is a few hundred bytes and its key moves with
# the run's answers version and update time, so this only bounds the cache.
# Long, because a page whose lines all expired counts up to 50 runs at once.
HISTORY_TALLY_TTL = 30 * 24 * 60 * 60


class InstantEvalRunsQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    unit = serializers.ChoiceField(
        choices=["traces", "spans", "sessions", "users"], required=False
    )
    search = serializers.CharField(max_length=200, required=False, allow_blank=True)
    # One run, for a page that opens it by id.
    task_id = serializers.UUIDField(required=False)
    page_number = serializers.IntegerField(min_value=0, required=False, default=0)
    page_size = serializers.IntegerField(
        min_value=1, max_value=50, required=False, default=20
    )


class InstantEvalRunSerializer(serializers.Serializer):
    task_id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    run_type = serializers.CharField()
    unit = serializers.CharField()
    reads = serializers.CharField(allow_blank=True, allow_null=True)
    rows = serializers.IntegerField()
    config_ids = serializers.ListField(child=serializers.CharField())
    questions = serializers.ListField(child=serializers.CharField())
    counts = serializers.DictField(
        child=serializers.DictField(child=serializers.IntegerField())
    )
    answered = serializers.IntegerField()
    pending = serializers.IntegerField()
    # When the last answer landed, once every row has one.
    finished_at = serializers.CharField(allow_null=True, required=False)
    # When the run last moved: its last answer, or its task's last change
    # (a start, a pause). History tells a live run that has stopped moving
    # by it, without asking Temporal about each run.
    last_activity_at = serializers.CharField(allow_null=True, required=False)
    # Runs started from the Ask page: who started it, what it was expected to
    # cost, and how big the view it sampled was.
    created_by = serializers.CharField(allow_null=True, required=False)
    credits_estimate = serializers.FloatField(allow_null=True, required=False)
    population = serializers.IntegerField(allow_null=True, required=False)
    is_sample = serializers.BooleanField(required=False)
    # What the run was asked about: the view's date option, how its scope
    # read, and the window it read ({"from", "to"}).
    date_preset = serializers.CharField(allow_null=True, required=False)
    label = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    window = serializers.JSONField(allow_null=True, required=False)
    # A user question's sessions read; its rows and counts are its users.
    sessions = serializers.IntegerField(allow_null=True, required=False)


class InstantEvalRunsResultSerializer(serializers.Serializer):
    runs = InstantEvalRunSerializer(many=True)
    has_more = serializers.BooleanField()


class InstantEvalRunsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = InstantEvalRunsResultSerializer()


def _task_rows(task) -> int:
    filters = task.filters or {}
    for key in ("session_id", "trace_id", "span_id"):
        if isinstance(filters.get(key), list):
            return len(filters[key])
    return int(task.spans_limit or 0)


def _legacy_tally(task_ids: list[str], out: dict[str, list]) -> None:
    """Runs whose answers are only in the eval logger: count them there."""
    from django.db import connection

    from tracer.services.instant_eval_summary import LABEL, PENDING

    rid = "COALESCE(l.trace_session_id::text, l.trace_id::text, l.observation_span_id)"
    with connection.cursor() as cur:
        # A row is settled once none of its questions' entries is pending: a
        # row with 1 of k answers is still pending on a multi-question run.
        cur.execute(
            "SELECT t, count(rid), count(rid) FILTER (WHERE settled), max(last) FROM ("
            f"SELECT l.eval_task_id AS t, {rid} AS rid, "
            f"bool_and(({LABEL}) <> '{PENDING}') AS settled, max(l.updated_at) AS last "
            "FROM tracer_eval_logger l WHERE l.eval_task_id = ANY(%(tasks)s) AND l.deleted = false "
            "GROUP BY 1, 2) x GROUP BY t",
            {"tasks": task_ids},
        )
        for tid, seen, settled, last in cur.fetchall():
            out[tid] = [int(seen), int(settled), last, {}, 0]
        cur.execute(
            f"SELECT l.eval_task_id, l.custom_eval_config_id::text, {LABEL} AS label, count(*) "
            "FROM tracer_eval_logger l WHERE l.eval_task_id = ANY(%(tasks)s) AND l.deleted = false "
            "GROUP BY 1, 2, 3",
            {"tasks": task_ids},
        )
        for tid, cfg, label, n in cur.fetchall():
            if label != PENDING and tid in out:
                out[tid][3].setdefault(cfg, {})[label] = int(n)


def _answer_tally(task_ids: list[str], out: dict[str, list]) -> None:
    """Runs whose answers are recorded as they land: count the narrow table."""
    from django.db import connection

    with connection.cursor() as cur:
        # A row is settled once every question of its run has an answer: a
        # row with 1 of k answers is still pending on a multi-question run.
        cur.execute(
            "SELECT a.task_id::text, "
            "count(*) FILTER (WHERE a.n >= GREATEST(COALESCE(r.questions, 1), 1)), "
            "max(a.last) FROM ("
            "SELECT task_id, row_id, count(*) AS n, max(answered_at) AS last "
            "FROM tracer_instant_eval_answer WHERE task_id = ANY(%(tasks)s::uuid[]) "
            "GROUP BY task_id, row_id) a "
            "LEFT JOIN tracer_instant_eval_run r ON r.eval_task_id = a.task_id "
            "GROUP BY a.task_id",
            {"tasks": task_ids},
        )
        for tid, settled, last in cur.fetchall():
            out[tid] = [0, int(settled), last, {}, 0]
        cur.execute(
            "SELECT task_id::text, config_id::text, label, count(*) "
            "FROM tracer_instant_eval_answer WHERE task_id = ANY(%(tasks)s::uuid[]) "
            "GROUP BY 1, 2, 3",
            {"tasks": task_ids},
        )
        for tid, cfg, label, n in cur.fetchall():
            out.setdefault(tid, [0, 0, None, {}, 0])[3].setdefault(cfg, {})[label] = (
                int(n)
            )


FINISHED = frozenset({"completed", "failed", "paused"})


def _latest(*times) -> str | None:
    """The latest of these times (naive ones are UTC), as ISO 8601."""
    known = [t if t.tzinfo else t.replace(tzinfo=UTC) for t in times if t]
    return _iso(max(known)) if known else None


def _history_status(task, run, pending: int) -> str:
    """A run's state for History; a run that ended with rows unanswered
    stopped before finishing."""
    if run is not None and run.cancelled_at:
        return "cancelled"
    status = str(task.status)
    if run is not None and pending and status in ("completed", "failed"):
        return "stalled"
    return status


def _user_tally(task, run, config_ids: list[str], value: list) -> None:
    """A user question counts its users, each once, under their rolled-up
    answer; the rows its run stored are their sessions."""
    from tracer.services.instant_eval_summary import RunView, user_answer_counts, zone

    users, settled, counts = user_answer_counts(
        RunView(
            task_id=str(task.id),
            row_type="sessions",
            unit="users",
            config_ids=config_ids,
            tz=zone("UTC"),
            cancelled=bool(run.cancelled_at),
        )
    )
    value[:] = [users, settled, value[2], counts, users, value[4]]


def _history_tally(page: list[tuple]) -> dict[str, tuple]:
    """Per task: rows logged, rows with an answer, the last update, the
    answer counts per question, the rows its run stored, and for a user
    question the sessions behind its users (its rows are then its users).
    Counted in SQL (from the narrow answer table where the run records one),
    and kept for a month once a run has finished."""
    from django.db import connection

    out: dict[str, list] = {}
    keys: dict[str, str] = {}
    for task, run, _ in page:
        if str(task.status) in FINISHED or (run is not None and run.cancelled_at):
            stamp = task.updated_at.timestamp() if task.updated_at else 0
            version = run.answers_version if run is not None else -1
            keys[str(task.id)] = f"ie-history:v2:{task.id}:{version}:{stamp}"
    cached = cache.get_many(list(keys.values())) if keys else {}
    for tid, key in keys.items():
        if key in cached:
            out[tid] = list(cached[key])
    todo = [entry for entry in page if str(entry[0].id) not in out]
    synced = [str(t.id) for t, run, _ in todo if run is not None and run.answers_synced]
    legacy = [str(t.id) for t, run, _ in todo if run is None or not run.answers_synced]
    fresh: dict[str, list] = {}
    if synced:
        _answer_tally(synced, fresh)
    if legacy:
        _legacy_tally(legacy, fresh)
    if todo:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT task_id::text, count(*) FROM tracer_instant_eval_row "
                "WHERE task_id = ANY(%(tasks)s::uuid[]) GROUP BY task_id",
                {"tasks": [str(t.id) for t, _, _ in todo]},
            )
            for tid, n in cur.fetchall():
                fresh.setdefault(tid, [0, 0, None, {}, 0])[4] = int(n)
    for value in fresh.values():
        value.append(None)
    for task, run, config_ids in todo:
        tid = str(task.id)
        if tid in synced and run.unit == "users" and tid in fresh and config_ids:
            _user_tally(task, run, config_ids, fresh[tid])
    for tid, value in fresh.items():
        if tid in synced:
            # Every stored row is logged once the run has picked them.
            value[0] = value[4]
        out[tid] = value
        if tid in keys:
            cache.set(keys[tid], value, HISTORY_TALLY_TTL)
    return {tid: tuple(v) for tid, v in out.items()}


class InstantEvalRunsView(InstantEvalLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=InstantEvalRunsQuerySerializer,
        responses={200: InstantEvalRunsResponseSerializer, **ERROR_RESPONSES},
    )
    def get(self, request):
        """Past Instant eval runs of a project, newest first, with their answer split."""
        params = request.validated_query_data
        project = _project(request, params["project_id"])
        if project is None:
            return self._gm.not_found("Project not found")
        # A "Keep answering" task is continuous: it never finishes and isn't
        # a run, so History leaves it out (a page polls while a run is live).
        tasks = EvalTask.objects.filter(
            project=project, deleted=False, name__startswith=INSTANT_TASK_PREFIX
        ).exclude(run_type=RunType.CONTINUOUS)
        if params.get("task_id"):
            tasks = tasks.filter(id=params["task_id"])
        if params.get("search"):
            tasks = tasks.filter(name__icontains=params["search"])
        tasks = list(
            tasks.order_by("-created_at").prefetch_related("evals")[:HISTORY_SCAN]
        )
        # A run knows its own unit; a question may have been asked of another.
        run_units = dict(
            InstantEvalRun.objects.filter(
                eval_task_id__in=[t.id for t in tasks]
            ).values_list("eval_task_id", "unit")
        )
        runs = []
        for task in tasks:
            # In the order the run asked them (its page opens on the first).
            metas = [
                (str(c.id), (c.config or {}).get("instant_eval") or {})
                for c in asked_order(task, list(task.evals.all()))
            ]
            metas = [(cid, m) for cid, m in metas if m.get("question")]
            if not metas:
                continue
            unit = (
                run_units.get(task.id)
                or metas[0][1].get("unit")
                or str(task.row_type or "traces")
            )
            if params.get("unit") and unit != params["unit"]:
                continue
            runs.append((task, unit, metas))
        start = params["page_number"] * params["page_size"]
        page = runs[start : start + params["page_size"]]
        tids = [str(t.id) for t, _, _ in page]
        instant_runs = {
            str(r.eval_task_id): r
            for r in InstantEvalRun.objects.filter(
                eval_task_id__in=tids
            ).select_related("created_by")
        }
        tally = _history_tally(
            [
                (t, instant_runs.get(str(t.id)), [cid for cid, _ in m])
                for t, _, m in page
            ]
        )
        from tracer.views.instant_eval_scale import shown_credits_per_answer

        # Credits are shown only where the run is billed.
        billed = shown_credits_per_answer() is not None
        out = []
        for task, unit, metas in page:
            tid = str(task.id)
            seen, settled, last_update, counts, stored, sessions = tally.get(
                tid, (0, 0, None, {}, 0, None)
            )
            run = instant_runs.get(tid)
            # Rows the task named (or its run picked) but hasn't logged yet are
            # pending too; a task that ran on filters only knows what it logged.
            named = len(_task_ids(task, unit)) or stored or seen
            if run is not None and not stored and task.status in ("pending", "running"):
                named = run.rows_requested
            pending = max(0, max(named, seen) - settled)
            if run is not None and run.cancelled_at:
                pending = 0
            out.append(
                {
                    "task_id": tid,
                    "name": task.name or "",
                    "created_at": _iso(task.created_at),
                    "status": _history_status(task, run, pending),
                    "run_type": str(task.run_type),
                    "unit": unit,
                    "reads": metas[0][1].get("reads"),
                    "rows": stored or _task_rows(task),
                    "config_ids": [cid for cid, _ in metas],
                    "questions": [m["question"] for _, m in metas],
                    "counts": counts,
                    "answered": settled,
                    "pending": pending,
                    "finished_at": _iso(last_update) if not pending else None,
                    "last_activity_at": _latest(last_update, task.updated_at),
                    "created_by": (
                        (run.created_by.name or run.created_by.email)
                        if run is not None and run.created_by
                        else None
                    ),
                    "credits_estimate": (
                        run.credits_estimate if run is not None and billed else None
                    ),
                    "population": run.population if run is not None else None,
                    "is_sample": bool(
                        run is not None and run.rows_requested < run.population
                    ),
                    "date_preset": run.date_preset if run is not None else None,
                    "label": run.label if run is not None else None,
                    "window": (
                        {"from": _iso(run.date_from), "to": _iso(run.date_to)}
                        if run is not None and run.date_from
                        else None
                    ),
                    "sessions": sessions,
                }
            )
        return self._gm.success_response(
            {"runs": out, "has_more": start + params["page_size"] < len(runs)}
        )
