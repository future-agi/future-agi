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

from django.db import IntegrityError, transaction  # noqa: E402

from tracer.models.observation_span import EvalLogger, EvalTargetType  # noqa: E402


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
        """target_type='span' AND trace_session set → IntegrityError."""
        with pytest.raises(IntegrityError), transaction.atomic():
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
        """target_type='trace' MUST anchor to a root span. NULL observation_span → IntegrityError."""
        with pytest.raises(IntegrityError), transaction.atomic():
            EvalLogger.objects.create(
                target_type=EvalTargetType.TRACE,
                observation_span=None,
                trace=trace,
                custom_eval_config=custom_eval_config,
            )

    def test_rejects_session_target_with_span_set(
        self, observation_span, trace_session, custom_eval_config
    ):
        """target_type='session' MUST have NULL span — IntegrityError otherwise."""
        with pytest.raises(IntegrityError), transaction.atomic():
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
        """target_type='session' MUST have NULL trace — IntegrityError otherwise."""
        with pytest.raises(IntegrityError), transaction.atomic():
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

    def test_get_evaluation_details_clickhouse_filters_target_type_span(
        self, observation_span, custom_eval_config
    ):
        """``_get_evaluation_details_clickhouse`` adds ``target_type='span'`` to its WHERE.

        We don't need a real ClickHouse here — just assert the helper builds
        a query string with the new filter. Pre-PR3 the WHERE clause was
        keyed only on observation_span_id + custom_eval_config_id.
        """
        from tracer.views.observation_span import ObservationSpanView

        view = ObservationSpanView()
        analytics = MagicMock()
        # Make the executed query observable; return an empty .data so the
        # helper short-circuits to a 'no row' response (we only care about the
        # SQL it built, not the result handling).
        analytics.execute_ch_query.return_value.data = []

        view._get_evaluation_details_clickhouse(
            observation_span_id=observation_span.id,
            custom_eval_config_id=custom_eval_config.id,
            analytics=analytics,
        )

        # The query string is the first positional arg.
        sent_query = analytics.execute_ch_query.call_args.args[0]
        assert "target_type = 'span'" in sent_query, (
            f"expected target_type='span' filter in CH query, got:\n{sent_query}"
        )


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
            {"eval_task_id": str(task.id), "page": 0, "page_size": 25, "period": "30d"},
        )
        assert response.status_code == 200
        body = response.json().get("result", {})
        items = body.get("logs", {}).get("items", [])
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
