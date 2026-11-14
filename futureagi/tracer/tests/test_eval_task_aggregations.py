"""
Tests for the new ``eval_aggregation`` / ``span_aggregation`` modes on
``EvalTaskView.get_usage`` — see ``tracer/views/eval_task.py``.

Both modes short-circuit ``get_usage`` and return *only* the aggregated
payload (no ``stats`` / ``chart`` / ``logs``). Soft-deleted and error rows
are excluded from rollups. Span aggregation ignores session/trace-target
rows (``observation_span_id IS NULL``) and picks the latest run when the
same ``(span, eval_config)`` repeats.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

# Break the import cycle (see test_eval_logger_schema.py for the
# canonical comment).
import model_hub.tasks  # noqa: F401
from tracer.models.observation_span import (
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
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


# ── eval_aggregation ───────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalAggregation:
    def _get(self, auth_client, task, **extra):
        return auth_client.get(
            USAGE_URL,
            {"eval_task_id": str(task.id), "eval_aggregation": "true", **extra},
        )

    def test_percentage_eval_returns_avg_output_float(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="percentage",
        )
        cfg = _config(project=project, template=tpl, name="Faithfulness")
        task = _task(project=project)
        for v in (0.4, 0.6, 0.8):
            _row(span=_fresh_span(observation_span), cfg=cfg, task=task, output_float=v)

        body = self._get(auth_client, task).json()["result"]
        agg = body["eval_aggregation"]["Faithfulness"]
        assert agg["output_type"] == "percentage"
        assert agg["aggregated_score"] == pytest.approx(0.6)
        assert "stats" not in body and "chart" not in body and "logs" not in body

    def test_pass_fail_eval_returns_pass_rate_0_to_100(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        cfg = _config(project=project, template=tpl, name="Toxicity Check")
        task = _task(project=project)
        for v in (True, True, True, False):
            _row(span=_fresh_span(observation_span), cfg=cfg, task=task, output_bool=v)

        agg = self._get(auth_client, task).json()["result"]["eval_aggregation"][
            "Toxicity Check"
        ]
        assert agg["output_type"] == "pass_fail"
        assert agg["aggregated_score"] == 75.0

    def test_deterministic_eval_returns_per_choice_percentages(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="deterministic",
        )
        cfg = _config(project=project, template=tpl, name="Sentiment")
        task = _task(project=project)
        # 4 rows: A, B, AC, A → A in 3/4, B in 1/4, C in 1/4
        for lst in (["A"], ["B"], ["A", "C"], ["A"]):
            _row(
                span=_fresh_span(observation_span),
                cfg=cfg,
                task=task,
                output_str_list=lst,
            )

        agg = self._get(auth_client, task).json()["result"]["eval_aggregation"][
            "Sentiment"
        ]
        assert agg["output_type"] == "deterministic"
        assert agg["aggregated_score"] == {
            "A": 75.0,
            "B": 25.0,
            "C": 25.0,
        }

    def test_multiple_eval_types_in_one_task(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl_p = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="percentage",
            name="t-pct",
        )
        tpl_b = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
            name="t-pf",
        )
        tpl_d = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="deterministic",
            name="t-det",
        )
        cfg_p = _config(project=project, template=tpl_p, name="Faithfulness")
        cfg_b = _config(project=project, template=tpl_b, name="Toxicity")
        cfg_d = _config(project=project, template=tpl_d, name="Sentiment")
        task = _task(project=project)
        _row(span=observation_span, cfg=cfg_p, task=task, output_float=0.5)
        _row(span=observation_span, cfg=cfg_b, task=task, output_bool=True)
        _row(span=observation_span, cfg=cfg_d, task=task, output_str_list=["pos"])

        agg = self._get(auth_client, task).json()["result"]["eval_aggregation"]
        assert set(agg.keys()) == {"Faithfulness", "Toxicity", "Sentiment"}
        assert agg["Faithfulness"]["aggregated_score"] == pytest.approx(0.5)
        assert agg["Toxicity"]["aggregated_score"] == 100.0
        assert agg["Sentiment"]["aggregated_score"] == {"pos": 100.0}

    def test_empty_task_returns_empty_dict(self, auth_client, project):
        task = _task(project=project)
        agg = self._get(auth_client, task).json()["result"]["eval_aggregation"]
        assert agg == {}

    def test_error_rows_are_excluded(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="percentage",
        )
        cfg = _config(project=project, template=tpl, name="Faithfulness")
        task = _task(project=project)
        _row(span=_fresh_span(observation_span), cfg=cfg, task=task, output_float=0.5)
        _row(span=_fresh_span(observation_span), cfg=cfg, task=task, output_float=0.5)
        # Adding an error row with a spurious output_float must not shift
        # the mean — the row is excluded entirely.
        _row(
            span=_fresh_span(observation_span),
            cfg=cfg,
            task=task,
            error=True,
            error_message="boom",
            output_float=1.0,
        )

        agg = self._get(auth_client, task).json()["result"]["eval_aggregation"][
            "Faithfulness"
        ]
        assert agg["aggregated_score"] == pytest.approx(0.5)

    def test_soft_deleted_rows_are_excluded(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        cfg = _config(project=project, template=tpl, name="Toxicity")
        task = _task(project=project)
        _row(span=_fresh_span(observation_span), cfg=cfg, task=task, output_bool=True)
        _row(span=_fresh_span(observation_span), cfg=cfg, task=task, output_bool=True)
        # A soft-deleted False row would drop pass-rate to 66% if counted;
        # excluding it keeps it at 100%.
        _row(
            span=_fresh_span(observation_span),
            cfg=cfg,
            task=task,
            output_bool=False,
            deleted=True,
        )

        agg = self._get(auth_client, task).json()["result"]["eval_aggregation"][
            "Toxicity"
        ]
        assert agg["aggregated_score"] == 100.0

    def test_eval_id_filter_narrows_to_one_config(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="percentage",
        )
        cfg_a = _config(project=project, template=tpl, name="A")
        cfg_b = _config(project=project, template=tpl, name="B")
        task = _task(project=project)
        _row(span=observation_span, cfg=cfg_a, task=task, output_float=0.1)
        _row(span=observation_span, cfg=cfg_b, task=task, output_float=0.9)

        agg = self._get(auth_client, task, eval_id=str(cfg_a.id)).json()["result"][
            "eval_aggregation"
        ]
        assert list(agg.keys()) == ["A"]

    def test_session_target_rows_are_excluded(
        self,
        auth_client,
        project,
        observe_project,
        trace_session,
        organization,
        workspace,
        observation_span,
    ):
        # Session-target rows (no observation_span) must be dropped from
        # eval_aggregation. A False session row would tank pass-rate from
        # 100% to 50% if counted — assertion below pins it at 100%.
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        cfg_session = _config(project=observe_project, template=tpl, name="SessionEval")
        cfg_span = _config(project=project, template=tpl, name="SpanEval")
        task = _task(project=project)
        EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            observation_span=None,
            trace=None,
            trace_session=trace_session,
            custom_eval_config=cfg_session,
            eval_task_id=str(task.id),
            output_bool=False,
        )
        _row(span=observation_span, cfg=cfg_span, task=task, output_bool=True)

        agg = self._get(auth_client, task).json()["result"]["eval_aggregation"]
        assert set(agg.keys()) == {"SpanEval"}
        assert agg["SpanEval"]["aggregated_score"] == 100.0


# ── span_aggregation ───────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSpanAggregation:
    def _get(self, auth_client, task, **extra):
        return auth_client.get(
            USAGE_URL,
            {"eval_task_id": str(task.id), "span_aggregation": "true", **extra},
        )

    def test_returns_raw_value_per_eval_per_span(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
        child_span,
    ):
        tpl_p = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="percentage",
            name="t-pct",
        )
        tpl_d = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="deterministic",
            name="t-det",
        )
        cfg_p = _config(project=project, template=tpl_p, name="Faithfulness")
        cfg_d = _config(project=project, template=tpl_d, name="Sentiment")
        task = _task(project=project)
        _row(span=observation_span, cfg=cfg_p, task=task, output_float=0.82)
        _row(
            span=observation_span,
            cfg=cfg_d,
            task=task,
            output_str_list=["positive"],
        )
        _row(span=child_span, cfg=cfg_p, task=task, output_float=0.31)

        body = self._get(auth_client, task).json()["result"]
        sa = body["span_aggregation"]
        assert set(sa.keys()) == {
            str(observation_span.id),
            str(child_span.id),
        }
        assert sa[str(observation_span.id)]["Faithfulness"]["value"] == 0.82
        assert sa[str(observation_span.id)]["Sentiment"]["value"] == ["positive"]
        assert sa[str(child_span.id)]["Faithfulness"]["value"] == 0.31
        assert "stats" not in body and "logs" not in body

    def test_session_target_rows_are_skipped(
        self,
        auth_client,
        observe_project,
        trace_session,
        organization,
        workspace,
        observation_span,
        project,
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        cfg_obs = _config(project=observe_project, template=tpl, name="ObsEval")
        cfg_span = _config(project=project, template=tpl, name="SpanEval")
        task = _task(project=project)
        # One session-target row (no observation_span) — must be skipped.
        EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            observation_span=None,
            trace=None,
            trace_session=trace_session,
            custom_eval_config=cfg_obs,
            eval_task_id=str(task.id),
            output_bool=True,
        )
        # One span-target row — must appear.
        _row(span=observation_span, cfg=cfg_span, task=task, output_bool=True)

        sa = self._get(auth_client, task).json()["result"]["span_aggregation"]
        assert list(sa.keys()) == [str(observation_span.id)]
        assert sa[str(observation_span.id)]["SpanEval"]["value"] is True

    def test_soft_deleted_predecessor_is_superseded_by_live_row(
        self, auth_client, project, organization, workspace, observation_span
    ):
        """Re-evaluating a (task, span, cfg) triple soft-deletes the old row and
        upserts a new live one; only the live value surfaces in the rollup.

        The ``eval_logger_live_span_uniq`` constraint (scoped
        ``eval_task_id__isnull=False``) makes two *live* rows for one triple
        impossible, so for eval-*task* rollups "latest wins" reduces to
        "soft-deleted predecessor is excluded" — which is all this can assert.

        NOTE: the endpoint's created_at "latest wins" tie-break is only
        reachable on the inline (task-less) path, where the unique constraint
        does not apply and multiple live rows are representable. That branch is
        unexercised here. TODO(TH-XXXX): add ordering coverage on the inline
        path, or delete the now-unreachable ordering branch.
        """
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="percentage",
        )
        cfg = _config(project=project, template=tpl, name="Faithfulness")
        task = _task(project=project)
        # Superseded row is soft-deleted (re-eval upserts it); the live row is
        # the only survivor under the (task, span, cfg) unique constraint.
        _row(span=observation_span, cfg=cfg, task=task, output_float=0.1, deleted=True)
        _row(span=observation_span, cfg=cfg, task=task, output_float=0.9)

        sa = self._get(auth_client, task).json()["result"]["span_aggregation"]
        assert sa[str(observation_span.id)]["Faithfulness"]["value"] == pytest.approx(
            0.9
        )

    def test_soft_deleted_rows_are_excluded(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        cfg = _config(project=project, template=tpl, name="Toxicity")
        task = _task(project=project)
        _row(
            span=observation_span,
            cfg=cfg,
            task=task,
            output_bool=False,
            deleted=True,
        )

        sa = self._get(auth_client, task).json()["result"]["span_aggregation"]
        assert sa == {}


# ── Both flags / legacy preservation ───────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestAggregationFlagsCombined:
    def test_both_flags_return_both_top_level_keys(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        cfg = _config(project=project, template=tpl, name="Toxicity")
        task = _task(project=project)
        _row(span=observation_span, cfg=cfg, task=task, output_bool=True)

        body = auth_client.get(
            USAGE_URL,
            {
                "eval_task_id": str(task.id),
                "eval_aggregation": "true",
                "span_aggregation": "true",
            },
        ).json()["result"]

        assert "eval_aggregation" in body and "span_aggregation" in body
        assert "stats" not in body and "chart" not in body and "logs" not in body

    def test_flags_absent_returns_legacy_shape(
        self, auth_client, project, organization, workspace, observation_span
    ):
        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        cfg = _config(project=project, template=tpl, name="Toxicity")
        task = _task(project=project)
        _row(span=observation_span, cfg=cfg, task=task, output_bool=True)

        body = auth_client.get(
            USAGE_URL,
            {"eval_task_id": str(task.id), "page": 1, "page_size": 25, "period": "30d"},
        ).json()["result"]

        # Legacy shape pinned — must keep top-level keys the FE consumes.
        assert "stats" in body and "chart" in body and "logs" in body
        assert "eval_aggregation" not in body
        assert "span_aggregation" not in body


# ── start_date / end_date date range filter ────────────────────────────


def _set_span_created_at(span, when):
    """Override ``created_at`` (auto_now_add) for a span via ``.update()``."""
    ObservationSpan.objects.filter(id=span.id).update(created_at=when)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestAggregationDateRange:
    """Date bounds filter both aggregations by the span's ``created_at``.

    A caller may supply either bound independently. Supplied bounds remain
    inclusive for compatibility; without bounds all task spans are included.
    """

    def _get(self, auth_client, task, **extra):
        return auth_client.get(
            USAGE_URL,
            {"eval_task_id": str(task.id), "eval_aggregation": "true", **extra},
        )

    def _setup_two_spans(
        self, project, organization, workspace, observation_span, child_span
    ):
        # observation_span = 10 days ago, child_span = 1 day ago.
        now = timezone.now()
        _set_span_created_at(observation_span, now - timedelta(days=10))
        _set_span_created_at(child_span, now - timedelta(days=1))

        tpl = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        cfg = _config(project=project, template=tpl, name="Toxicity")
        task = _task(project=project)
        # old span passes, recent span fails — pass rate diverges per range.
        _row(span=observation_span, cfg=cfg, task=task, output_bool=True)
        _row(span=child_span, cfg=cfg, task=task, output_bool=False)
        return task, cfg

    def test_no_date_range_includes_all_spans(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
        child_span,
    ):
        task, _ = self._setup_two_spans(
            project, organization, workspace, observation_span, child_span
        )
        agg = self._get(auth_client, task).json()["result"]["eval_aggregation"][
            "Toxicity"
        ]
        # 1 pass + 1 fail = 50%.
        assert agg["aggregated_score"] == 50.0

    def test_start_date_excludes_older_spans(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
        child_span,
    ):
        task, _ = self._setup_two_spans(
            project, organization, workspace, observation_span, child_span
        )
        # 5 days ago: keeps child_span (1d), drops observation_span (10d).
        start = (timezone.now() - timedelta(days=5)).isoformat()
        agg = self._get(auth_client, task, start_date=start).json()["result"][
            "eval_aggregation"
        ]["Toxicity"]
        # Only the failing recent span survives → 0%.
        assert agg["aggregated_score"] == 0.0

    def test_end_date_excludes_newer_spans(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
        child_span,
    ):
        task, _ = self._setup_two_spans(
            project, organization, workspace, observation_span, child_span
        )
        # 5 days ago: keeps observation_span (10d), drops child_span (1d).
        end = (timezone.now() - timedelta(days=5)).isoformat()
        agg = self._get(auth_client, task, end_date=end).json()["result"][
            "eval_aggregation"
        ]["Toxicity"]
        # Only the passing older span survives → 100%.
        assert agg["aggregated_score"] == 100.0

    def test_both_bounds_keep_only_in_range_spans(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
        child_span,
    ):
        task, _ = self._setup_two_spans(
            project, organization, workspace, observation_span, child_span
        )
        # Window 15..7 days ago → only observation_span (10d) qualifies.
        start = (timezone.now() - timedelta(days=15)).isoformat()
        end = (timezone.now() - timedelta(days=7)).isoformat()
        agg = self._get(auth_client, task, start_date=start, end_date=end).json()[
            "result"
        ]["eval_aggregation"]["Toxicity"]
        assert agg["aggregated_score"] == 100.0

    def test_range_excluding_all_returns_empty_rollup(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
        child_span,
    ):
        task, _ = self._setup_two_spans(
            project, organization, workspace, observation_span, child_span
        )
        # A bounded historical window with no spans omits the config.
        start = (timezone.now() - timedelta(days=300)).isoformat()
        end = (timezone.now() - timedelta(days=299)).isoformat()
        agg = self._get(auth_client, task, start_date=start, end_date=end).json()[
            "result"
        ]["eval_aggregation"]
        assert agg == {}

    def test_range_filters_span_aggregation_too(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
        child_span,
    ):
        # Same range mechanic must apply when only span_aggregation is set.
        task, _ = self._setup_two_spans(
            project, organization, workspace, observation_span, child_span
        )
        start = (timezone.now() - timedelta(days=5)).isoformat()
        sa = auth_client.get(
            USAGE_URL,
            {
                "eval_task_id": str(task.id),
                "span_aggregation": "true",
                "start_date": start,
            },
        ).json()["result"]["span_aggregation"]
        # Only the recent (child) span survives the window.
        assert list(sa.keys()) == [str(child_span.id)]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestUsageLogBoundedProjection:
    def _get(self, auth_client, task, **extra):
        return auth_client.get(
            USAGE_URL,
            {
                "eval_task_id": str(task.id),
                "period": "30d",
                "page": 1,
                "page_size": 100,
                "include_summary": "false",
                **extra,
            },
        )

    def test_preserves_json_types_details_deleted_and_dangling_spans(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
    ):
        template = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        config = _config(project=project, template=template, name="Projection")
        config.mapping = {
            "string_true": "payload.string_true",
            "string_number": "payload.string_number",
            "string_null": "payload.string_null",
            "empty_string": "payload.empty_string",
            "real_bool": "payload.real_bool",
            "real_list": "payload.real_list",
            "nested_list": "messages.0.content",
            "double_underscore": "foo__bar",
            "numeric_root": "0",
        }
        config.save(update_fields=["mapping"])
        task = _task(project=project, name="Projection task")
        ObservationSpan.objects.filter(id=observation_span.id).update(
            span_attributes={
                "input": False,
                "input.value": "fallback input",
                "payload": {
                    "string_true": "true",
                    "string_number": "123",
                    "string_null": "null",
                    "empty_string": "",
                    "real_bool": True,
                    "real_list": ["one", 2],
                },
                "messages": [{"content": "nested"}],
                "foo__bar": "literal double underscore",
                "0": "literal numeric root",
            },
            deleted=True,
        )
        live_log = _row(
            span=observation_span,
            cfg=config,
            task=task,
            output_bool=True,
            eval_explanation="",
            error_message="fallback reason",
            output_metadata={
                "warnings": [{"type": "partial_input", "message": "bounded"}]
            },
            results_explanation={"why": "preserved"},
        )
        dangling_log = EvalLogger.objects.create(
            target_type=EvalTargetType.SPAN,
            observation_span_id="missing-span",
            trace=observation_span.trace,
            custom_eval_config=config,
            eval_task_id=str(task.id),
            output_bool=True,
        )

        response = self._get(auth_client, task)

        assert response.status_code == 200, response.json()
        rows = {
            item["id"]: item for item in response.json()["result"]["logs"]["results"]
        }
        live = rows[str(live_log.id)]
        assert live["input"] == "fallback input"
        assert live["reason"] == "fallback reason"
        assert live["warnings"] == [{"type": "partial_input", "message": "bounded"}]
        assert live["detail"]["results_explanation"] == {"why": "preserved"}
        assert live["detail"]["input_variables"] == {
            "string_true": "true",
            "string_number": "123",
            "string_null": "null",
            "empty_string": "",
            "real_bool": True,
            "real_list": ["one", 2],
            "nested_list": "nested",
            "double_underscore": "literal double underscore",
            "numeric_root": "literal numeric root",
        }
        assert live["detail"]["detail_complete"] is True
        dangling = rows[str(dangling_log.id)]
        assert dangling["detail"]["detail_complete"] is False
        assert "span_context" in dangling["detail"]["omitted_fields"]

    def test_page_of_100_uses_constant_bounded_projection_queries(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
    ):
        template = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        config = _config(project=project, template=template, name="Scale")
        config.mapping = {"prompt": "input"}
        config.save(update_fields=["mapping"])
        task = _task(project=project, name="Scale task")
        spans = [
            ObservationSpan(
                id=f"usage_span_{ordinal:03d}",
                project=observation_span.project,
                trace=observation_span.trace,
                name=f"usage span {ordinal}",
                observation_type="llm",
                start_time=observation_span.start_time,
                end_time=observation_span.end_time,
                span_attributes={"input": f"prompt {ordinal}"},
            )
            for ordinal in range(100)
        ]
        ObservationSpan.objects.bulk_create(spans)
        EvalLogger.objects.bulk_create(
            [
                EvalLogger(
                    target_type=EvalTargetType.SPAN,
                    observation_span_id=span.id,
                    trace_id=observation_span.trace_id,
                    custom_eval_config_id=config.id,
                    eval_task_id=str(task.id),
                    output_bool=True,
                )
                for span in spans
            ]
        )

        with CaptureQueriesContext(connection) as captured:
            response = self._get(auth_client, task)

        assert response.status_code == 200, response.json()
        assert len(response.json()["result"]["logs"]["results"]) == 100
        sql_statements = [query["sql"] for query in captured.captured_queries]
        assert sum('FROM "tracer_eval_logger"' in sql for sql in sql_statements) == 1
        assert (
            sum('FROM "tracer_observation_span"' in sql for sql in sql_statements) == 1
        )
        assert (
            sum('FROM "tracer_custom_eval_config"' in sql for sql in sql_statements)
            == 1
        )

    def test_oversized_and_malformed_mapping_is_bounded_and_truthful(
        self,
        auth_client,
        project,
        organization,
        workspace,
        observation_span,
    ):
        template = _template(
            organization=organization,
            workspace=workspace,
            output_type_normalized="pass_fail",
        )
        config = _config(project=project, template=template, name="Oversized")
        config.mapping = {"huge": "x" * 9_000, "bad": ["not", "a", "path"]}
        config.save(update_fields=["mapping"])
        task = _task(project=project, name="Oversized mapping task")
        log = _row(
            span=observation_span,
            cfg=config,
            task=task,
            output_bool=True,
        )

        response = self._get(auth_client, task)

        assert response.status_code == 200, response.json()
        row = next(
            item
            for item in response.json()["result"]["logs"]["results"]
            if item["id"] == str(log.id)
        )
        assert row["detail"]["input_variables"] == {}
        assert row["detail"]["detail_complete"] is False
        assert "input_variables.mapping_oversized" in row["detail"]["omitted_fields"]
