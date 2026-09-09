"""Exact custom date-range filtering for ``EvalTaskView.get_usage``.

Empty windows stay empty and keep the requested bounds. The endpoint must not
turn an interactive period read into a hidden all-time history scan.

Also covers the cross-reference ids each returned log row carries back to
observe.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from django.utils import timezone

# Break the import cycle (see test_eval_logger_schema.py for the
# canonical comment).
import model_hub.tasks  # noqa: F401
from tracer.models.observation_span import (
    EvalLogger,
    EvalTargetType,
)
from tracer.tests.eval_task_factories import (
    make_config as _config,
)
from tracer.tests.eval_task_factories import (
    make_fresh_span as _fresh_span,
)
from tracer.tests.eval_task_factories import (
    make_row as _row,
)
from tracer.tests.eval_task_factories import (
    make_task as _task,
)
from tracer.tests.eval_task_factories import (
    make_template as _template,
)

USAGE_URL = "/tracer/eval-task/get_usage/"


# ── Test scaffolding ───────────────────────────────────────────────────


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

    def test_range_before_first_run_stays_exact_and_empty(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        result = _result(
            _get(auth_client, task, start_date=_iso(-100), end_date=_iso(-50))
        )
        assert result["period_requested"] == "custom"
        assert result["period_used"] == "custom"
        assert result["stats"]["total_runs"] == 2
        assert result["stats"]["runs_period"] == 0
        assert result["logs"]["count"] == 0
        assert _chart_calls(result) == 0

    def test_range_after_last_run_stays_exact_and_empty(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        result = _result(_get(auth_client, task, start_date=_iso(1), end_date=_iso(5)))
        assert result["period_requested"] == "custom"
        assert result["period_used"] == "custom"
        assert result["stats"]["runs_period"] == 0
        assert result["logs"]["count"] == 0
        assert _chart_calls(result) == 0

    def test_empty_range_does_not_publish_a_widened_window(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        result = _result(
            _get(auth_client, task, start_date=_iso(-100), end_date=_iso(-50))
        )
        assert result["period_used"] == "custom"
        assert "start_date_used" not in result
        assert "end_date_used" not in result

    def test_eval_filter_applies_alongside_custom_range(
        self,
        auth_client,
        task_with_two_runs,
        project,
        organization,
        workspace,
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


# ── Predefined period bounds ───────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestPeriodBounds:
    def test_period_containing_runs_is_reported_unchanged(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        result = _result(_get(auth_client, task, period="30d"))
        assert result["period_requested"] == "30d"
        assert result["period_used"] == "30d"
        assert result["stats"]["runs_period"] == 2
        assert _chart_calls(result) == 2

    def test_period_excluding_all_runs_stays_exact_and_empty(
        self, auth_client, task_with_two_runs
    ):
        task, _ = task_with_two_runs
        # Both runs are older than 30 minutes.
        result = _result(_get(auth_client, task, period="30m"))
        assert result["period_requested"] == "30m"
        assert result["period_used"] == "30m"
        assert result["stats"]["total_runs"] == 2
        assert result["stats"]["runs_period"] == 0
        assert result["logs"]["count"] == 0
        assert _chart_calls(result) == 0

    def test_short_empty_period_keeps_a_bounded_bucket_count(
        self, auth_client, task_with_two_runs
    ):
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

    def test_period_outside_the_enum_is_rejected(self, auth_client, task_with_two_runs):
        task, _ = task_with_two_runs
        # "custom" and "all" are response-only labels, never accepted as input.
        assert _get(auth_client, task, period="custom").status_code == 400
        assert _get(auth_client, task, period="all").status_code == 400
        assert _get(auth_client, task, period="7 days").status_code == 400

    def test_reversed_range_is_rejected(self, auth_client, task_with_two_runs):
        task, _ = task_with_two_runs
        response = _get(auth_client, task, start_date=_iso(0), end_date=_iso(-10))
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

    def test_lone_bound_is_rejected_outside_aggregation_mode(
        self, auth_client, task_with_two_runs
    ):
        """Outside the aggregation modes, the chart/logs path only reads the
        pair — a lone bound would otherwise be silently ignored."""
        task, _ = task_with_two_runs
        response = _get(auth_client, task, start_date=_iso(-15))
        assert response.status_code == 400

        response = _get(auth_client, task, end_date=_iso(0))
        assert response.status_code == 400


# ── Bucket alignment ───────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestBucketAlignment:
    """The chart's zero-fill keys must land on the same instants as the data.

    TH-4805 itself: 7d buckets the data at hours {0, 6, 12, 18}, but the
    zero-fill loop used to step 6h from ``now - 7d`` with only the minute
    floored, so the two sets only intersected when ``now.hour % 6 == 0``. The
    clock is frozen at an hour where it is not, because unfrozen the old bug
    reproduced 20 hours a day and the test would be flaky-green.
    """

    @pytest.mark.parametrize("frozen_hour", [1, 13])
    def test_seven_day_chart_counts_every_run(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
        frozen_hour,
    ):
        frozen = datetime(2026, 1, 15, frozen_hour, 37, 11, tzinfo=UTC)
        assert frozen.hour % 6 != 0, "an aligned hour would hide the bug"

        template = _template(organization=organization, workspace=workspace)
        cfg = _config(project=project, template=template, name="Toxicity")
        task = _task(project=project)

        with mock.patch("django.utils.timezone.now", return_value=frozen):
            _row(
                span=observation_span,
                cfg=cfg,
                task=task,
                created_at=frozen - timedelta(days=1),
                output_bool=True,
            )
            _row(
                span=_fresh_span(observation_span),
                cfg=cfg,
                task=task,
                created_at=frozen - timedelta(days=3),
                output_bool=False,
            )
            result = _result(_get(auth_client, task, period="7d"))

        assert result["period_used"] == "7d"
        assert _chart_calls(result) == 2


# ── Log row cross-references ───────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestLogItemCrossReferences:
    """Each log row carries the ids the side panel jumps back to observe with.

    The eval engine resolves a run's target from ClickHouse and the target FKs
    are unconstrained, so a run can legitimately point at a span, trace or
    session that has no Postgres row. The row must still report what it
    evaluated.
    """

    def _one_row(self, auth_client, task):
        results = _result(_get(auth_client, task, period="30d"))["logs"]["results"]
        assert len(results) == 1
        return results[0]

    def _cfg(self, project, organization, workspace):
        return _config(
            project=project,
            template=_template(organization=organization, workspace=workspace),
            name="Toxicity",
        )

    def test_session_row_reports_its_session_id_without_a_postgres_row(
        self, auth_client, project, organization, workspace
    ):
        task = _task(project=project)
        session_id = uuid.uuid4()
        EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            trace_session_id=session_id,
            custom_eval_config=self._cfg(project, organization, workspace),
            eval_task_id=str(task.id),
            output_bool=True,
        )

        row = self._one_row(auth_client, task)
        assert row["session_id"] == str(session_id)
        assert row["detail"]["session_id"] == str(session_id)
