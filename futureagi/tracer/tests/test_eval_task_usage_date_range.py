"""
Custom date-range filtering and the all-time fallback on
``EvalTaskView.get_usage`` — see ``tracer/views/eval_task.py`` and
``tracer/services/eval_tasks/usage.py``.

A window that excludes every run widens to all-time so the user is not left
staring at an empty chart. That fallback resets *both* bounds: leaving
``end_date`` pinned to an empty custom window that sits before the runs would
make ``start_date > end_date``, and the zero-fill loop would emit nothing.
"""

import uuid
from datetime import timedelta

import pytest  # noqa: E402
from django.utils import timezone

# Break the import cycle (see test_eval_logger_schema.py for the
# canonical comment).
import model_hub.tasks  # noqa: F401
from model_hub.models.evals_metric import EvalTemplate  # noqa: E402
from tracer.models.custom_eval_config import CustomEvalConfig  # noqa: E402
from tracer.models.eval_task import (  # noqa: E402
    EvalTask,
    EvalTaskStatus,
    RunType,
)
from tracer.models.observation_span import (  # noqa: E402
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)

USAGE_URL = "/tracer/eval-task/get_usage/"


# ── Test scaffolding ───────────────────────────────────────────────────


def _template(*, organization, workspace, name=None):
    return EvalTemplate.objects.create(
        name=name or "Template (pass_fail)",
        description="",
        organization=organization,
        workspace=workspace,
        output_type_normalized="pass_fail",
        config={"output": "Pass/Fail"},
    )


def _config(*, project, template, name):
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=template,
        config={},
        mapping={},
        filters={},
    )


def _task(*, project, name="Usage task"):
    return EvalTask.objects.create(
        project=project,
        name=name,
        filters={},
        sampling_rate=100,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        spans_limit=100,
    )


def _fresh_span(base):
    """A new span sharing base's trace/project — one eval row per span, so
    live rows don't collide on the eval_logger_live_span_uniq
    (task, span, cfg) partial unique constraint."""
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=base.project,
        trace=base.trace,
        name="usage span",
        observation_type="llm",
        start_time=base.start_time,
        end_time=base.end_time,
    )


def _row(*, span, cfg, task, created_at, **kwargs):
    """One eval run, backdated to ``created_at`` (created_at is auto_now_add,
    so it can only be set after the fact via .update())."""
    row = EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=span,
        trace=span.trace,
        custom_eval_config=cfg,
        eval_task_id=str(task.id),
        **kwargs,
    )
    EvalLogger.objects.filter(id=row.id).update(created_at=created_at)
    row.refresh_from_db()
    return row


def _iso(delta_days):
    return (timezone.now() + timedelta(days=delta_days)).isoformat()


def _get(auth_client, task, **extra):
    return auth_client.get(USAGE_URL, {"eval_task_id": str(task.id), **extra})


def _result(response):
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _chart_calls(result):
    return sum(bucket["calls"] for bucket in result["chart"])


@pytest.fixture
def task_with_two_runs(project, organization, workspace, observation_span):
    """A task with one run 10 days ago and one 1 day ago."""
    template = _template(organization=organization, workspace=workspace)
    cfg = _config(project=project, template=template, name="Toxicity")
    task = _task(project=project)
    now = timezone.now()
    _row(
        span=observation_span,
        cfg=cfg,
        task=task,
        created_at=now - timedelta(days=10),
        output_bool=True,
    )
    _row(
        span=_fresh_span(observation_span),
        cfg=cfg,
        task=task,
        created_at=now - timedelta(days=1),
        output_bool=False,
    )
    return task, cfg


# ── Custom range ───────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestCustomDateRange:
    """``start_date`` + ``end_date`` together scope stats, chart and logs,
    and are reported back as the ``custom`` period."""

    def test_range_containing_all_runs(self, auth_client, task_with_two_runs):
        task, _ = task_with_two_runs
        result = _result(
            _get(auth_client, task, start_date=_iso(-15), end_date=_iso(0))
        )
        assert result["stats"]["runs_period"] == 2
        assert result["stats"]["total_runs"] == 2
        assert result["logs"]["count"] == 2
        assert _chart_calls(result) == 2
        assert result["period_requested"] == "custom"
        assert result["period_used"] == "custom"

    def test_range_containing_one_run_excludes_the_other(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        # 15..5 days ago keeps the 10-day-old run, drops the 1-day-old one.
        result = _result(
            _get(auth_client, task, start_date=_iso(-15), end_date=_iso(-5))
        )
        assert result["stats"]["runs_period"] == 1
        # total_runs stays task-wide — only the period figures are scoped.
        assert result["stats"]["total_runs"] == 2
        assert result["logs"]["count"] == 1
        assert _chart_calls(result) == 1
        assert result["period_used"] == "custom"

    def test_range_before_first_run_falls_back_with_non_empty_chart(
        self, auth_client, task_with_two_runs
    ):
        """The regression this fixes: a window entirely *before* the runs
        used to reset start_date but leave end_date in the empty window, so
        start_date > end_date and the chart came back empty."""
        task, _ = task_with_two_runs
        result = _result(
            _get(auth_client, task, start_date=_iso(-100), end_date=_iso(-50))
        )
        assert result["period_requested"] == "custom"
        assert result["period_used"] == "all"
        assert result["stats"]["runs_period"] == 2
        assert result["chart"], "fallback must still produce chart buckets"
        assert _chart_calls(result) == 2

    def test_range_after_last_run_falls_back_with_non_empty_chart(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        result = _result(
            _get(auth_client, task, start_date=_iso(1), end_date=_iso(5))
        )
        assert result["period_used"] == "all"
        assert result["chart"]
        assert _chart_calls(result) == 2

    def test_fallback_window_covers_the_actual_run_span(
        self, auth_client, task_with_two_runs
    ):
        """``start_date_used`` / ``end_date_used`` report the widened window
        so the frontend can label the chart with the range really charted."""
        task, _ = task_with_two_runs
        result = _result(
            _get(auth_client, task, start_date=_iso(-100), end_date=_iso(-50))
        )
        now = timezone.now()
        start_used = timezone.datetime.fromisoformat(result["start_date_used"])
        end_used = timezone.datetime.fromisoformat(result["end_date_used"])
        # Earliest run is 10 days old, latest is 1 day old.
        assert timedelta(days=9) < now - start_used < timedelta(days=11)
        assert timedelta(hours=12) < now - end_used < timedelta(days=2)
        assert start_used < end_used

    def test_eval_filter_applies_alongside_custom_range(
        self, auth_client, task_with_two_runs, project, organization, workspace,
        observation_span,
    ):
        task, cfg = task_with_two_runs
        other_cfg = _config(
            project=project,
            template=_template(
                organization=organization, workspace=workspace, name="Other tpl"
            ),
            name="Relevance",
        )
        _row(
            span=_fresh_span(observation_span),
            cfg=other_cfg,
            task=task,
            created_at=timezone.now() - timedelta(days=2),
            output_bool=True,
        )

        result = _result(
            _get(
                auth_client,
                task,
                start_date=_iso(-15),
                end_date=_iso(0),
                eval_id=str(other_cfg.id),
            )
        )
        # Both filters applied: only the Relevance run is in scope.
        assert result["stats"]["runs_period"] == 1
        assert result["stats"]["total_runs"] == 1
        assert result["logs"]["count"] == 1
        assert result["logs"]["results"][0]["eval_id"] == str(other_cfg.id)


# ── Predefined periods / fallback ──────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestPeriodFallback:
    def test_period_containing_runs_is_reported_unchanged(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        result = _result(_get(auth_client, task, period="30d"))
        assert result["period_requested"] == "30d"
        assert result["period_used"] == "30d"
        assert result["stats"]["runs_period"] == 2
        assert _chart_calls(result) == 2

    def test_period_excluding_all_runs_falls_back_to_all(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        # Both runs are older than 30 minutes.
        result = _result(_get(auth_client, task, period="30m"))
        assert result["period_requested"] == "30m"
        assert result["period_used"] == "all"
        assert result["stats"]["runs_period"] == 2
        assert result["chart"]
        assert _chart_calls(result) == 2

    def test_short_period_fallback_does_not_explode_bucket_count(
        self, auth_client, task_with_two_runs
    ):
        """Bucket width comes from the resolved window, not the requested
        period — a "30m" request that widens to a 10-day window must not
        zero-fill 10 days at 5-minute resolution."""
        task, _ = task_with_two_runs
        result = _result(_get(auth_client, task, period="30m"))
        assert len(result["chart"]) < 50

    def test_task_with_no_runs_returns_empty_chart(self, auth_client, project):
        task = _task(project=project, name="Empty task")
        result = _result(_get(auth_client, task, period="30d"))
        assert result["chart"] == []
        assert result["stats"]["total_runs"] == 0
        assert result["stats"]["runs_period"] == 0
        assert result["stats"]["pass_rate"] == 0
        assert result["logs"]["count"] == 0
        # Nothing to widen to, so no fallback.
        assert result["period_used"] == "30d"


# ── Query contract ─────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestUsageQueryContract:
    """``get_usage`` validates its query string through
    ``EvalTaskUsageQuerySerializer`` rather than reading query_params ad hoc."""

    def test_missing_eval_task_id_is_rejected(self, auth_client):
        assert auth_client.get(USAGE_URL, {"period": "30d"}).status_code == 400

    def test_unknown_query_param_is_rejected(self, auth_client, task_with_two_runs):
        task, _ = task_with_two_runs
        assert _get(auth_client, task, bogus_param="1").status_code == 400

    def test_period_outside_the_enum_is_rejected(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        # "custom" and "all" are response-only labels, never accepted as input.
        assert _get(auth_client, task, period="custom").status_code == 400
        assert _get(auth_client, task, period="all").status_code == 400
        assert _get(auth_client, task, period="7 days").status_code == 400

    def test_reversed_range_is_rejected(self, auth_client, task_with_two_runs):
        task, _ = task_with_two_runs
        response = _get(
            auth_client, task, start_date=_iso(0), end_date=_iso(-10)
        )
        assert response.status_code == 400

    def test_single_bound_still_allowed_for_aggregation_mode(
        self, auth_client, task_with_two_runs
    ):
        """The aggregation modes filter open-ended, so one bound on its own
        must stay valid."""
        task, _ = task_with_two_runs
        response = _get(
            auth_client, task, eval_aggregation="true", start_date=_iso(-15)
        )
        assert response.status_code == 200
        assert "eval_aggregation" in response.json()["result"]
