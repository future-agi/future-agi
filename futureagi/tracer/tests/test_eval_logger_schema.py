"""
EvalLogger schema tests (PR3).

Pin the per-target_type FK shape, the conflation behaviour on root spans,
and the read-side filters that keep session rows off span/trace surfaces.
Schema-only tests; PR4 introduces the writers.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

# Break the same import cycle PR1's runtime tests broke: the chain
# tracer.utils.eval_tasks -> tracer.utils.eval -> model_hub.tasks.__init__
# -> tracer.utils.eval_tasks loops because the package __init__ imports
# from a submodule that's still loading. Importing model_hub.tasks first
# unwinds the cycle via the user_evaluation submodule (no tracer.utils.eval
# dependency).
import model_hub.tasks  # noqa: F401, E402

from django.core.exceptions import ValidationError  # noqa: E402
from django.db import IntegrityError, transaction  # noqa: E402

from tracer.models.observation_span import EvalLogger, EvalTargetType  # noqa: E402

# The per-target_type FK shape is enforced in two layers:
#   - Python: ``EvalLogger.clean()`` (via ``save() -> full_clean()``) raises
#     ``ValidationError`` for single-row writes.
#   - DB: ``eval_logger_target_type_fks`` CHECK constraint raises
#     ``IntegrityError`` for paths that bypass ``save()`` (``bulk_create``,
#     raw SQL, the ClickHouse CDC mirror).
# Either rejection satisfies the contract; tests accept both so refactors
# of one layer don't churn the other.
_REJECTION_ERRORS = (ValidationError, IntegrityError)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalLoggerTargetTypeShape:
    """Per-target_type FK contract enforced by ``eval_logger_target_type_fks``."""

    def test_accepts_span_row(self, observation_span, custom_eval_config):
        """target_type='span': span+trace populated, trace_session NULL — succeeds."""
        row = EvalLogger.objects.create(
            target_type=EvalTargetType.SPAN,
            observation_span=observation_span,
            trace=observation_span.trace,
            custom_eval_config=custom_eval_config,
            output_bool=True,
        )
        assert row.target_type == "span"
        assert row.observation_span_id == observation_span.id
        assert row.trace_id == observation_span.trace_id
        assert row.trace_session_id is None

    def test_accepts_trace_row_anchored_to_root_span(
        self, observation_span, custom_eval_config
    ):
        """target_type='trace': observation_span = trace's root span; same FK shape as span."""
        row = EvalLogger.objects.create(
            target_type=EvalTargetType.TRACE,
            observation_span=observation_span,
            trace=observation_span.trace,
            custom_eval_config=custom_eval_config,
            output_float=0.85,
        )
        assert row.target_type == "trace"
        assert row.observation_span_id == observation_span.id
        # Schema-indistinguishable from a span row at the column level —
        # disambiguator is target_type. PR4 readers that need strict span
        # semantics filter `target_type='span'` (or trace, etc.).

    def test_accepts_session_row(self, trace_session, custom_eval_config):
        """target_type='session': observation_span + trace NULL, trace_session set."""
        row = EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            observation_span=None,
            trace=None,
            trace_session=trace_session,
            custom_eval_config=custom_eval_config,
            output_bool=True,
        )
        assert row.target_type == "session"
        assert row.observation_span_id is None
        assert row.trace_id is None
        assert row.trace_session_id == trace_session.id

    def test_rejects_span_target_with_session_set(
        self, observation_span, trace_session, custom_eval_config
    ):
        """target_type='span' AND trace_session set → rejected (ValidationError or IntegrityError)."""
        with pytest.raises(_REJECTION_ERRORS), transaction.atomic():
            EvalLogger.objects.create(
                target_type=EvalTargetType.SPAN,
                observation_span=observation_span,
                trace=observation_span.trace,
                trace_session=trace_session,
                custom_eval_config=custom_eval_config,
            )

    def test_rejects_trace_target_with_null_span(
        self, trace, custom_eval_config
    ):
        """target_type='trace' MUST anchor to a root span. NULL observation_span → rejected."""
        with pytest.raises(_REJECTION_ERRORS), transaction.atomic():
            EvalLogger.objects.create(
                target_type=EvalTargetType.TRACE,
                observation_span=None,
                trace=trace,
                custom_eval_config=custom_eval_config,
            )

    def test_rejects_session_target_with_span_set(
        self, observation_span, trace_session, custom_eval_config
    ):
        """target_type='session' MUST have NULL span — rejected otherwise."""
        with pytest.raises(_REJECTION_ERRORS), transaction.atomic():
            EvalLogger.objects.create(
                target_type=EvalTargetType.SESSION,
                observation_span=observation_span,
                trace=None,
                trace_session=trace_session,
                custom_eval_config=custom_eval_config,
            )

    def test_rejects_session_target_with_trace_set(
        self, trace_session, trace, custom_eval_config
    ):
        """target_type='session' MUST have NULL trace — rejected otherwise."""
        with pytest.raises(_REJECTION_ERRORS), transaction.atomic():
            EvalLogger.objects.create(
                target_type=EvalTargetType.SESSION,
                observation_span=None,
                trace=trace,
                trace_session=trace_session,
                custom_eval_config=custom_eval_config,
            )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalLoggerConflationOnRootSpan:
    """Pins the user-chosen UX: trace-level eval rows surface on the root span.

    A query like ``EvalLogger.objects.filter(observation_span_id=root.id)``
    intentionally returns BOTH the root span's own span-level evals AND the
    trace-level eval anchored to that root span. PR4 evaluators write trace
    rows with ``observation_span = root span``; this test prevents a future
    "fix" from accidentally filtering them out by adding a ``target_type``
    constraint that breaks the conflation.
    """

    def test_observation_span_keyed_query_returns_span_and_trace_rows(
        self, observation_span, custom_eval_config
    ):
        """A span-keyed query returns both span- and trace-target rows on the root span."""
        EvalLogger.objects.create(
            target_type=EvalTargetType.SPAN,
            observation_span=observation_span,
            trace=observation_span.trace,
            custom_eval_config=custom_eval_config,
            output_bool=True,
        )
        EvalLogger.objects.create(
            target_type=EvalTargetType.TRACE,
            observation_span=observation_span,  # = trace's root span
            trace=observation_span.trace,
            custom_eval_config=custom_eval_config,
            output_float=0.9,
        )

        rows = EvalLogger.objects.filter(observation_span_id=observation_span.id)
        assert rows.count() == 2  # conflation intentional
        target_types = sorted(r.target_type for r in rows)
        assert target_types == ["span", "trace"]

        # Explicit-filter escape hatch: callers that mean "strictly span-level"
        # add target_type='span' (per the reader audit in PR3).
        span_only = EvalLogger.objects.filter(
            observation_span_id=observation_span.id, target_type="span"
        )
        assert span_only.count() == 1
        assert span_only.first().target_type == "span"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalLoggerReaderAudit:
    """Pin the reader-audit fixes that keep session rows off span/trace surfaces."""

    def test_get_evaluation_details_clickhouse_filters_target_type_span_and_trace(
        self, observation_span, custom_eval_config
    ):
        """CH query allows span+trace targets and excludes session rows."""
        from tracer.views.observation_span import ObservationSpanView

        view = ObservationSpanView()
        analytics = MagicMock()
        analytics.execute_ch_query.return_value.data = []

        view._get_evaluation_details_clickhouse(
            observation_span_id=observation_span.id,
            custom_eval_config_id=custom_eval_config.id,
            analytics=analytics,
        )

        sent_query = analytics.execute_ch_query.call_args.args[0]
        assert "target_type IN ('span', 'trace')" in sent_query, (
            f"expected target_type IN ('span', 'trace') filter in CH query, got:\n{sent_query}"
        )
        assert "'session'" not in sent_query, (
            "session-target rows must not be reachable via this endpoint; "
            f"got:\n{sent_query}"
        )

    def test_get_evaluation_details_pg_excludes_session_rows(
        self,
        auth_client,
        observe_project,
        trace_session,
        custom_eval_config,
    ):
        """Endpoint must never surface session-target rows (HTTP-level pin)."""
        from tracer.models.custom_eval_config import CustomEvalConfig

        observe_config = CustomEvalConfig.objects.create(
            name="Observe Eval Session-Excluded",
            project=observe_project,
            eval_template=custom_eval_config.eval_template,
            config={"output": "Pass/Fail"},
            mapping={"input": "input"},
        )
        EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            observation_span=None,
            trace=None,
            trace_session=trace_session,
            custom_eval_config=observe_config,
            output_bool=True,
            eval_explanation="session-only row",
        )

        response = auth_client.get(
            "/tracer/observation-span/get_evaluation_details/",
            {
                "observation_span_id": "0000000000000000",
                "custom_eval_config_id": str(observe_config.id),
            },
        )
        assert response.status_code == 400
        body = response.json()
        assert body.get("status") is False
        assert "No eval logger found" in str(body.get("result", ""))

    def test_get_evaluation_details_pg_excludes_session_rows_when_span_row_absent(
        self,
        observe_project,
        observation_span,
        trace_session,
        custom_eval_config,
    ):
        """Query-level pin: target_type__in filter is what excludes session rows."""
        from tracer.models.custom_eval_config import CustomEvalConfig

        observe_config = CustomEvalConfig.objects.create(
            name="Observe Eval Query-Level",
            project=observe_project,
            eval_template=custom_eval_config.eval_template,
            config={"output": "Pass/Fail"},
            mapping={"input": "input"},
        )
        span_row = EvalLogger.objects.create(
            target_type=EvalTargetType.SPAN,
            observation_span=observation_span,
            trace=observation_span.trace,
            custom_eval_config=observe_config,
            output_bool=True,
        )
        EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            observation_span=None,
            trace=None,
            trace_session=trace_session,
            custom_eval_config=observe_config,
            output_bool=False,
        )

        endpoint_filter = EvalLogger.objects.filter(
            observation_span_id=observation_span.id,
            custom_eval_config_id=observe_config.id,
            target_type__in=["span", "trace"],
        )
        assert endpoint_filter.count() == 1
        assert endpoint_filter.first().id == span_row.id

        session_row = EvalLogger.objects.filter(
            custom_eval_config_id=observe_config.id,
            target_type=EvalTargetType.SESSION,
        ).first()
        assert session_row is not None
        assert session_row.observation_span_id is None


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalTaskViewsExposeRowTypeAndTargetType:
    """``get_eval_task_logs`` returns row_type; ``get_usage`` returns per-row target_type."""

    def test_get_eval_task_logs_includes_row_type(
        self, auth_client, project, custom_eval_config
    ):
        """The logs endpoint surfaces parent-EvalTask.row_type so the FE can swap labels."""
        from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType

        task = EvalTask.objects.create(
            project=project,
            name="Trace task",
            filters={},
            sampling_rate=100,
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.PENDING,
            spans_limit=100,
            row_type="traces",
        )
        task.evals.add(custom_eval_config)

        response = auth_client.get(
            "/tracer/eval-task/get_eval_task_logs/",
            {"eval_task_id": str(task.id)},
        )
        assert response.status_code == 200
        body = response.json().get("result", {})
        assert body.get("row_type") == "traces"

    def test_get_usage_session_row_falls_back_to_session_fields(
        self,
        auth_client,
        observe_project,
        trace_session,
        custom_eval_config,
    ):
        """Session-target rows surface session_id + session_name in detail; span/trace IDs NULL."""
        from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType

        # Custom eval config tied to the observe project (test data must
        # match the project the trace_session belongs to)
        from tracer.models.custom_eval_config import CustomEvalConfig

        observe_config = CustomEvalConfig.objects.create(
            name="Observe Eval",
            project=observe_project,
            eval_template=custom_eval_config.eval_template,
            config={"output": "Pass/Fail"},
            mapping={"input": "input"},
        )
        task = EvalTask.objects.create(
            project=observe_project,
            name="Session task",
            filters={},
            sampling_rate=100,
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.PENDING,
            spans_limit=100,
            row_type="sessions",
        )
        task.evals.add(observe_config)

        EvalLogger.objects.create(
            target_type=EvalTargetType.SESSION,
            observation_span=None,
            trace=None,
            trace_session=trace_session,
            custom_eval_config=observe_config,
            eval_task_id=str(task.id),
            output_bool=True,
            eval_explanation="stubbed",
        )

        response = auth_client.get(
            "/tracer/eval-task/get_usage/",
            {"eval_task_id": str(task.id), "page": 1, "page_size": 25, "period": "30d"},
        )
        assert response.status_code == 200
        body = response.json().get("result", {})
        # ExtendedPageNumberPagination native shape: results/count/...
        items = body.get("logs", {}).get("results", [])
        assert len(items) == 1, f"expected one log item, got {items}"
        item = items[0]
        # Top-level cross-references: span/trace NULL, session_id populated.
        assert item["span_id"] is None
        assert item["trace_id"] is None
        assert item["session_id"] == str(trace_session.id)
        # Detail panel: target_type discriminator + session fields.
        assert item["detail"]["target_type"] == "session"
        assert item["detail"]["session_id"] == str(trace_session.id)
        assert item["detail"]["session_name"] == trace_session.name
        assert item["detail"]["span_id"] is None
        assert item["detail"]["trace_id"] is None


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestGetUsageAggregationBlocks:
    """``get_usage`` opt-in aggregation outputs.

    ``?eval_aggregation=true`` -> ``{eval_id: {name, output_type, agg_score}}``
    ``?span_aggregation=true`` -> ``{span_id: {eval_id: {output_type, score}}}``

    The contract: span_aggregation MUST cover every span that has an
    eval row for this task (none missed) AND MUST NOT include rows whose
    target is the trace or the session (no bleed). eval_aggregation MUST
    include one entry per configured eval with its output_type.
    """

    def test_span_aggregation_covers_every_span_and_excludes_other_targets(
        self,
        auth_client,
        project,
        trace,
        multiple_spans,
        eval_template,
    ):
        from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
        from tracer.models.custom_eval_config import CustomEvalConfig

        # Two configs so each span carries two eval cells.
        cc1 = CustomEvalConfig.objects.create(
            name="cfg-A", project=project, eval_template=eval_template,
            config={}, mapping={}, filters={},
        )
        cc2 = CustomEvalConfig.objects.create(
            name="cfg-B", project=project, eval_template=eval_template,
            config={}, mapping={}, filters={},
        )
        task = EvalTask.objects.create(
            project=project, name="agg test",
            status=EvalTaskStatus.COMPLETED, run_type=RunType.HISTORICAL,
            row_type="spans", sampling_rate=100.0,
        )
        task.evals.add(cc1, cc2)

        # 10 spans x 2 configs = 20 span-target eval rows.
        for sp in multiple_spans:
            for cc in (cc1, cc2):
                EvalLogger.objects.create(
                    target_type=EvalTargetType.SPAN,
                    observation_span=sp, trace=sp.trace,
                    custom_eval_config=cc, eval_task_id=str(task.id),
                    output_bool=True, error=False,
                )

        # Decoy: trace-target row. observation_span is set (per the FK
        # constraint) but target_type='trace'. span_aggregation MUST
        # exclude it; otherwise trace-level evals bleed into the per-span
        # matrix.
        EvalLogger.objects.create(
            target_type=EvalTargetType.TRACE,
            observation_span=multiple_spans[0],
            trace=multiple_spans[0].trace,
            custom_eval_config=cc1, eval_task_id=str(task.id),
            output_bool=True, error=False,
        )

        resp = auth_client.get(
            "/tracer/eval-task/get_usage/",
            {
                "eval_task_id": str(task.id),
                "page": 1, "page_size": 5, "period": "30d",
                "span_aggregation": "true",
                "eval_aggregation": "true",
            },
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()["result"]

        # span_aggregation: every span present, both eval cells under each.
        sa = body["span_aggregation"]
        expected_span_ids = {str(sp.id) for sp in multiple_spans}
        assert set(sa.keys()) == expected_span_ids, (
            f"span_aggregation key set mismatch.\n"
            f"  missing: {expected_span_ids - set(sa.keys())}\n"
            f"  extra:   {set(sa.keys()) - expected_span_ids}"
        )
        for span_id, evals in sa.items():
            assert set(evals.keys()) == {str(cc1.id), str(cc2.id)}, (
                f"span {span_id} missing eval cells: {evals}"
            )
            for cc_id, cell in evals.items():
                assert cell["output_type"] == "bool"
                # pivot_eval_results returns pass_rate on a 0-100 scale for
                # pass_fail evals; all 20 seeded rows pass so pass_rate = 100.
                assert cell["score"] == 100.0

        # eval_aggregation: one entry per config, output_type present.
        ea = body["eval_aggregation"]
        assert set(ea.keys()) == {str(cc1.id), str(cc2.id)}
        for cc_id, agg in ea.items():
            assert set(agg.keys()) == {"name", "output_type", "agg_score"}, (
                f"eval_aggregation[{cc_id}] missing fields: {agg}"
            )

        # Per-eval breakdown on evals[] (default response, always on).
        for meta in body["evals"]:
            for k in ("runs_period", "success_count", "error_count", "pass_rate"):
                assert k in meta, f"evals[] entry missing {k}: {meta}"

    def test_span_attributes_filter_malformed_json_returns_400(
        self, auth_client, project, eval_template
    ):
        """``?span_attributes_filters=`` rejects non-JSON input with a clean 400.

        Pins the contract that malformed filter input fails loudly with an
        informative error rather than silently no-op'ing (which would let a
        broken caller think they were filtering when they were not).
        """
        from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
        from tracer.models.custom_eval_config import CustomEvalConfig

        cc = CustomEvalConfig.objects.create(
            name="cfg", project=project, eval_template=eval_template,
            config={}, mapping={}, filters={},
        )
        task = EvalTask.objects.create(
            project=project, name="malformed-filter test",
            status=EvalTaskStatus.COMPLETED, run_type=RunType.HISTORICAL,
            row_type="spans", sampling_rate=100.0,
        )
        task.evals.add(cc)

        resp = auth_client.get(
            "/tracer/eval-task/get_usage/",
            {
                "eval_task_id": str(task.id),
                "page": 1, "page_size": 5, "period": "30d",
                "span_attributes_filters": "not-valid-json",
            },
        )
        assert resp.status_code == 400, (
            f"expected 400 for malformed span_attributes_filters, got {resp.status_code}"
        )
        body = resp.json()
        assert body.get("status") is False
        assert "span_attributes_filters" in str(body.get("result", "")), (
            f"error message should name the bad field, got {body!r}"
        )
