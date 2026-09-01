"""Adversarial contract tests for continuous eval-task reconciliation.

Continuous selection has two distinct sets:

* ``C`` -- identities whose source state changed in the frozen cursor window;
* ``M`` -- the subset of ``C`` that matches the task in latest full state.

The distinction matters for negative changes.  A changed identity absent from
``M`` is proof that its pending work must be removed, while an old identity
absent from ``C`` says nothing during an incremental pass.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

import tracer.selectors.eval_tasks.row_resolver as row_resolver
import tracer.services.eval_tasks.reconciler as reconciler_module
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.selectors.eval_tasks import continuous_candidates
from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)
from tracer.services.eval_tasks.ch_guardrails import EVAL_CH_GUARDRAILS
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.cursor_policy import (
    CONTINUOUS_CURSOR_OVERLAP,
    CONTINUOUS_MIN_PROOF_WINDOW,
)


@pytest.mark.unit
def test_background_eval_reads_follow_shared_clickhouse_policy() -> None:
    settings_sets = (
        EVAL_CH_GUARDRAILS,
        row_resolver._EVAL_TASK_STREAM_READ_SETTINGS,
        row_resolver._EVAL_TASK_FILTER_CLASSIFY_READ_SETTINGS,
        continuous_candidates._READ_SETTINGS,
    )
    for settings in settings_sets:
        assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
        assert "max_rows_to_read" not in settings
        assert settings["max_execution_time"] <= 30

    assert continuous_candidates._MAX_STATEMENT_TIMEOUT_MS == 30_000
    assert row_resolver._EVAL_TASK_MAX_READ_ATTEMPTS > 0
    assert row_resolver._EVAL_TASK_FILTER_CLASSIFY_READ_SETTINGS["max_threads"] > 0
    assert (
        row_resolver._EVAL_TASK_FILTER_CLASSIFY_READ_SETTINGS["max_bytes_to_read"] > 0
    )


def _attribute_filter(key: str = "final_status", value: str = "Rejected") -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": [value],
        },
    }


def _structured_attribute_filter() -> dict:
    return {
        "column_id": "langfuse.trace.tags",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "array",
            "filter_op": "contains",
            "filter_value": ["vip"],
        },
    }


def _has_annotation_filter(value: bool = True) -> dict:
    return {
        "column_id": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _has_eval_filter(value: bool = True) -> dict:
    return {
        "column_id": "has_eval",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _eval_metric_filter(
    eval_id: str,
    *,
    output_type: str = "SCORE",
) -> dict:
    filter_op, filter_value = {
        "SCORE": ("greater_than", 50),
        "PASS_FAIL": ("equals", "Passed"),
        "CHOICE": ("equals", "yes"),
        "CHOICES": ("equals", "yes"),
    }[output_type]
    return {
        "column_id": eval_id,
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "number" if output_type == "SCORE" else "text",
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


def _continuous_task(project, custom_eval_config, *, cursor=True) -> EvalTask:
    now = timezone.now()
    task = EvalTask.objects.create(
        project=project,
        name="continuous-contract",
        filters={"filters": [_attribute_filter()]},
        sampling_rate=100.0,
        spans_limit=1_000,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SPANS,
        start_time=now - timedelta(hours=1),
        continuous_cursor=now - timedelta(minutes=10) if cursor else None,
    )
    task.evals.add(custom_eval_config)
    return task


def _pending_entry(task, custom_eval_config, project, *, prefix: str) -> EvalLogger:
    trace = Trace.objects.create(project=project, name=f"trace-{prefix}")
    span = ObservationSpan.objects.create(
        id=f"span-{prefix}-{uuid.uuid4().hex[:8]}",
        project=project,
        trace=trace,
        name=f"span-{prefix}",
        observation_type="llm",
        parent_span_id="",
    )
    return EvalLogger.objects.create(
        trace=trace,
        observation_span=span,
        target_type=EvalTargetType.SPAN,
        eval_task_id=str(task.id),
        custom_eval_config=custom_eval_config,
        status=EvalEntryStatus.PENDING,
        config_hash=resolved_config_hash(custom_eval_config),
    )


def _resolution(
    *, candidates: tuple[str, ...], matches: tuple[str, ...], full_state: bool
):
    return row_resolver.ResolvedRowSet(
        candidate_ids=candidates,
        matched_ids=matches,
        full_state=full_state,
    )


@pytest.mark.unit
def test_continuous_iterator_is_only_a_batched_view_of_buffered_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility iterator must not start a second CH resolution."""

    task = object()
    calls = 0
    resolved = _resolution(
        candidates=("row-a", "row-b", "row-c"),
        matches=("row-a", "row-c"),
        full_state=False,
    )

    def fake_resolve(_task, *, ceiling=None):
        nonlocal calls
        calls += 1
        assert ceiling is None
        return resolved

    monkeypatch.setattr(row_resolver, "resolve_desired_rows", fake_resolve)

    assert list(row_resolver.iter_desired_rows(task, batch_size=1)) == [
        ["row-a"],
        ["row-c"],
    ]
    assert calls == 1


@pytest.mark.unit
def test_reconcile_resolves_once_before_the_first_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pass forwards one completely buffered result to materialization."""

    task = object()
    events: list[str] = []
    resolved = _resolution(
        candidates=("changed-a", "changed-b"),
        matches=("changed-b",),
        full_state=False,
    )

    def fake_resolve(_task, *, ceiling=None):
        assert ceiling is not None
        events.append("resolve")
        return resolved

    def fake_materialize(*args, **kwargs):
        events.append("materialize")
        forwarded = [*args[1:], *kwargs.values()]
        assert resolved.matched_ids in forwarded
        return 0

    monkeypatch.setattr(reconciler_module, "resolve_desired_rows", fake_resolve)
    monkeypatch.setattr(reconciler_module, "materialize_pending", fake_materialize)
    monkeypatch.setattr(reconciler_module, "_live_count", lambda _task: 0)
    monkeypatch.setattr(
        reconciler_module,
        "_advance_continuous_cursor",
        lambda _task, _now: events.append("cursor"),
    )

    reconciler_module.reconcile(task)

    assert events == ["resolve", "materialize", "cursor"]


@pytest.mark.parametrize("failure_kind", ["candidate-cap", "classifier-timeout"])
@pytest.mark.django_db
def test_candidate_cap_or_timeout_fails_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    project,
    custom_eval_config,
    failure_kind: str,
) -> None:
    task = _continuous_task(project, custom_eval_config, cursor=True)
    entry = _pending_entry(task, custom_eval_config, project, prefix=failure_kind)
    original_cursor = task.continuous_cursor
    writes: list[str] = []

    def fail_resolution(_task, *, ceiling=None):
        assert ceiling is not None
        raise row_resolver.EvalTaskReadBudgetExceeded(
            f"safe continuous failure: {failure_kind}"
        )

    monkeypatch.setattr(reconciler_module, "resolve_desired_rows", fail_resolution)
    monkeypatch.setattr(
        reconciler_module,
        "materialize_pending",
        lambda *args, **kwargs: writes.append("materialize"),
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="safe continuous failure",
    ):
        reconciler_module.reconcile(task)

    task.refresh_from_db()
    entry.refresh_from_db()
    assert writes == []
    assert task.continuous_cursor == original_cursor
    assert entry.status == EvalEntryStatus.PENDING
    assert entry.deleted is False


@pytest.mark.django_db
def test_delta_negative_change_drops_only_changed_nonmatch(
    monkeypatch: pytest.MonkeyPatch,
    project,
    custom_eval_config,
) -> None:
    """For DELTA, remove ``C − M`` and leave identities outside C alone."""

    task = _continuous_task(project, custom_eval_config, cursor=True)
    changed = _pending_entry(task, custom_eval_config, project, prefix="changed")
    untouched = _pending_entry(task, custom_eval_config, project, prefix="untouched")
    calls = 0

    def fake_resolve(_task, *, ceiling=None):
        nonlocal calls
        calls += 1
        return _resolution(
            candidates=(changed.observation_span_id,),
            matches=(),
            full_state=False,
        )

    monkeypatch.setattr(reconciler_module, "resolve_desired_rows", fake_resolve)
    monkeypatch.setattr(
        reconciler_module, "materialize_pending", lambda *args, **kwargs: 0
    )

    result = reconciler_module.reconcile(task)

    assert calls == 1
    assert result.dropped == 1
    assert EvalLogger.all_objects.get(id=changed.id).deleted is True
    assert EvalLogger.all_objects.get(id=untouched.id).deleted is False


@pytest.mark.django_db
def test_delta_requeues_only_stale_match_inside_candidate_set(
    monkeypatch: pytest.MonkeyPatch,
    project,
    custom_eval_config,
) -> None:
    task = _continuous_task(project, custom_eval_config, cursor=True)
    changed = _pending_entry(task, custom_eval_config, project, prefix="changed-match")
    untouched = _pending_entry(task, custom_eval_config, project, prefix="old-match")
    EvalLogger.objects.filter(id__in=(changed.id, untouched.id)).update(
        status=EvalEntryStatus.COMPLETED,
        config_hash="0" * 64,
    )

    monkeypatch.setattr(
        reconciler_module,
        "resolve_desired_rows",
        lambda *_args, **_kwargs: _resolution(
            candidates=(changed.observation_span_id,),
            matches=(changed.observation_span_id,),
            full_state=False,
        ),
    )
    monkeypatch.setattr(
        reconciler_module, "materialize_pending", lambda *args, **kwargs: 0
    )

    result = reconciler_module.reconcile(task)

    changed.refresh_from_db()
    untouched.refresh_from_db()
    assert result.requeued == 1
    assert changed.status == EvalEntryStatus.PENDING
    assert untouched.status == EvalEntryStatus.COMPLETED


@pytest.mark.django_db
def test_cursor_null_full_state_can_drop_every_absent_pending_entry(
    monkeypatch: pytest.MonkeyPatch,
    project,
    custom_eval_config,
) -> None:
    task = _continuous_task(project, custom_eval_config, cursor=False)
    first = _pending_entry(task, custom_eval_config, project, prefix="full-first")
    second = _pending_entry(task, custom_eval_config, project, prefix="full-second")

    monkeypatch.setattr(
        reconciler_module,
        "resolve_desired_rows",
        lambda *_args, **_kwargs: _resolution(
            candidates=(first.observation_span_id,),
            matches=(),
            full_state=True,
        ),
    )
    monkeypatch.setattr(
        reconciler_module, "materialize_pending", lambda *args, **kwargs: 0
    )

    result = reconciler_module.reconcile(task)

    assert result.dropped == 2
    assert EvalLogger.all_objects.get(id=first.id).deleted is True
    assert EvalLogger.all_objects.get(id=second.id).deleted is True


@pytest.mark.unit
def test_trace_full_state_classifier_allows_child_before_or_after_root() -> None:
    """Membership is candidate-scoped, not constrained to the arrival slice.

    The same SQL therefore classifies both adversarial layouts: a matching
    child five minutes before its root and a matching child after the root has
    aged out of the overlap window.
    """

    trace_id = str(uuid.uuid4())
    builder = TraceListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[_attribute_filter()],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    sql, params = builder.build_filter_match_query(
        [trace_id], candidate_full_state=True
    )

    assert params["candidate_trace_ids"] == (trace_id,)
    assert "candidate_start_date" not in params
    assert "candidate_end_date" not in params
    assert "latest_start_time >= %(candidate_start_date)s" not in sql
    assert "latest_start_time < %(candidate_end_date)s" not in sql
    assert "trace_id IN %(candidate_trace_ids)s" in sql


@pytest.mark.unit
def test_span_full_state_classifier_replays_old_start_latest_change() -> None:
    span_id = f"old-span-{uuid.uuid4().hex}"
    builder = SpanListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[_attribute_filter()],
        bounded_internal_scan=True,
        bounded_identity_only=True,
    )

    sql, params = builder.build_filter_match_query([span_id], candidate_full_state=True)

    assert params["candidate_span_ids"] == (span_id,)
    assert "candidate_start_date" not in params
    assert "candidate_end_date" not in params
    assert "latest_start_time >= %(candidate_start_date)s" not in sql
    assert "latest_start_time < %(candidate_end_date)s" not in sql
    assert "id IN %(candidate_span_ids)s" in sql


@pytest.mark.unit
def test_voice_full_state_classifier_does_not_require_root_child_coarrival() -> None:
    trace_id = str(uuid.uuid4())
    builder = VoiceCallListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[_attribute_filter()],
    )

    sql, params = builder.build_filter_match_query(
        [trace_id], candidate_full_state=True
    )

    assert params["candidate_trace_ids"] == (trace_id,)
    assert "candidate_start_date" not in params
    assert "candidate_end_date" not in params
    assert "start_time >= %(candidate_start_date)s" not in sql
    assert "start_time < %(candidate_end_date)s" not in sql
    assert "trace_id IN %(candidate_trace_ids)s" in sql


@pytest.mark.unit
def test_session_full_state_classifier_reads_old_user_membership_by_candidate() -> None:
    session_id = str(uuid.uuid4())
    end_user_id = str(uuid.uuid4())
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": [end_user_id],
                },
            }
        ],
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query(
        [session_id], candidate_full_state=True
    )

    assert params["candidate_filter_session_ids"] == (session_id,)
    assert "start_time >= %(start_date)s" not in sql
    assert "start_time < %(end_date)s" not in sql
    assert "toDate(start_time) BETWEEN" not in sql
    assert "candidate_filter_sessions AS" in sql
    assert "CAST(%(candidate_filter_session_id_array)s AS Array(UUID))" in sql
    assert "SELECT session_id FROM candidate_filter_sessions" in sql
    assert params["candidate_filter_session_id_array"] == [session_id]


@pytest.mark.unit
def test_full_state_classifier_still_honors_explicit_task_time_filter() -> None:
    now = timezone.now()
    builder = TraceListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        (now - timedelta(days=7)).isoformat(),
                        now.isoformat(),
                    ],
                },
            },
            _attribute_filter(),
        ],
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    sql, params = builder.build_filter_match_query(
        [str(uuid.uuid4())], candidate_full_state=True
    )

    assert "candidate_start_date" in params
    assert "candidate_end_date" in params
    assert "candidate_start_date_us" in params
    assert "candidate_end_date_us" in params
    assert (
        "latest_start_time >= fromUnixTimestamp64Micro(%(candidate_start_date_us)s)"
        in sql
    )
    assert (
        "latest_start_time < fromUnixTimestamp64Micro(%(candidate_end_date_us)s)" in sql
    )


@pytest.mark.unit
def test_full_state_session_classifier_rejects_unbounded_candidate_input() -> None:
    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[_attribute_filter()],
        bounded_internal_scan=True,
    )

    with pytest.raises(ValueError, match="candidate session batch exceeds"):
        builder.build_filter_match_query(
            [str(uuid.uuid4()) for _ in range(201)],
            candidate_full_state=True,
        )


@pytest.mark.unit
def test_continuous_exact_empty_returns_full_proof_without_ch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SimpleNamespace(
        project_id=uuid.uuid4(),
        id=uuid.uuid4(),
        run_type=RunType.CONTINUOUS,
        row_type=RowType.TRACES,
        continuous_cursor=datetime.now(UTC) - timedelta(minutes=5),
        start_time=datetime.now(UTC) - timedelta(hours=1),
        created_at=datetime.now(UTC) - timedelta(hours=1),
        filters={
            "filters": [
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "is_null",
                        "filter_value": None,
                    },
                }
            ]
        },
    )

    def forbidden_analytics():
        raise AssertionError("exact-empty proof must not construct CH analytics")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        forbidden_analytics,
    )

    resolved = row_resolver._resolve_continuous_rows(
        task,
        ceiling=datetime.now(UTC),
        sampling_rate=100.0,
    )

    assert resolved == row_resolver.ResolvedRowSet((), (), True)


class _FakeQueryResult:
    def __init__(self, data):
        self.data = data


def _continuous_trace_task_for_filter(filter_item: dict) -> SimpleNamespace:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    return SimpleNamespace(
        project_id=uuid.uuid4(),
        id=uuid.uuid4(),
        run_type=RunType.CONTINUOUS,
        row_type=RowType.TRACES,
        continuous_cursor=now - timedelta(minutes=5),
        start_time=now - timedelta(hours=1),
        created_at=now - timedelta(hours=1),
        filters={"filters": [filter_item]},
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "row_type",
    [RowType.SPANS, RowType.TRACES, RowType.SESSIONS, RowType.VOICE_CALLS],
)
@pytest.mark.parametrize(
    "config_ids",
    [
        ("00000000-0000-4000-8000-000000000088",),
        (),
    ],
    ids=["configured", "known-empty"],
)
def test_continuous_eval_filter_resolves_project_configs_once_for_all_batches(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
    config_ids: tuple[str, ...],
) -> None:
    has_eval_filter = {
        "column_id": "has_eval",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": True,
        },
    }
    task = _continuous_trace_task_for_filter(has_eval_filter)
    task.row_type = row_type
    classifier_ids = ("candidate-a", "candidate-b")
    public_ids = (
        ("root-a", "root-b") if row_type == RowType.VOICE_CALLS else classifier_ids
    )
    candidates = continuous_candidates.ContinuousCandidates(
        classifier_ids,
        public_ids,
    )
    builder_kwargs: dict = {}
    config_reads: list[tuple[str, tuple[dict, ...]]] = []
    classified_batches: list[tuple[str, ...]] = []

    class Builder:
        def __init__(self, **kwargs):
            builder_kwargs.update(kwargs)

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def recommended_filter_classify_batch_size() -> int:
            return 1

        @staticmethod
        def recommended_filter_classify_read_settings():
            return None

        @staticmethod
        def build_filter_match_query(batch, *, candidate_full_state):
            assert candidate_full_state is True
            classified_batches.append(tuple(batch))
            return "continuous_eval_classifier", {"candidate_ids": tuple(batch)}

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert query == "continuous_eval_classifier"
            candidate_id = params["candidate_ids"][0]
            result_key = {
                RowType.SPANS: "id",
                RowType.TRACES: "trace_id",
                RowType.SESSIONS: "session_id",
                RowType.VOICE_CALLS: "root_span_id",
            }[row_type]
            public_id = (
                candidate_id.replace("candidate-", "root-")
                if row_type == RowType.VOICE_CALLS
                else candidate_id
            )
            row = {result_key: public_id}
            return _FakeQueryResult([row])

    def resolve_configs(project_id, filters):
        config_reads.append((project_id, tuple(filters)))
        return config_ids

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        Analytics,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: Builder,
    )
    monkeypatch.setattr(
        row_resolver,
        "_eval_config_ids_for_filters",
        resolve_configs,
    )
    monkeypatch.setattr(
        continuous_candidates,
        "sample_public_ids",
        lambda _analytics, ids, **_kwargs: tuple(ids),
    )

    resolved = row_resolver._resolve_continuous_rows(
        task,
        ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        sampling_rate=100.0,
    )

    assert len(config_reads) == 1
    assert builder_kwargs["eval_config_ids"] == list(config_ids)
    assert classified_batches == [("candidate-a",), ("candidate-b",)]
    assert resolved.candidate_ids == public_ids
    assert resolved.matched_ids == public_ids


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_class", [SpanListQueryBuilderV2, SessionListQueryBuilderV2]
)
@pytest.mark.parametrize("config_ids", [(str(uuid.uuid4()),), ()])
def test_span_and_session_has_eval_use_authoritative_project_configs(
    monkeypatch: pytest.MonkeyPatch,
    builder_class,
    config_ids: tuple[str, ...],
) -> None:
    """Finite task classifiers cannot borrow a colliding foreign trace eval."""

    from tracer.models.custom_eval_config import CustomEvalConfig

    project_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())

    def unexpected_config_read(**_kwargs):
        raise AssertionError("the frozen project config set must avoid ORM fallback")

    monkeypatch.setattr(CustomEvalConfig.objects, "filter", unexpected_config_read)
    builder = builder_class(
        project_id=project_id,
        filters=[_has_eval_filter(True)],
        eval_config_ids=list(config_ids),
        bounded_internal_scan=True,
    )

    sql, params = builder.build_filter_match_query(
        [candidate_id], candidate_full_state=True
    )

    if config_ids:
        assert "eval_scan.custom_eval_config_id" in sql
        if builder_class is SpanListQueryBuilderV2:
            assert "sp.project_id" in sql
        assert "eval_scan.custom_eval_config_id IN" in sql
        assert config_ids in params.values()
    else:
        assert "00000000-0000-0000-0000-000000000000" in sql
        assert not any(key.startswith("project_eval_cfg") for key in params)


@pytest.mark.unit
@pytest.mark.parametrize(
    "row_type",
    [RowType.SPANS, RowType.TRACES, RowType.SESSIONS, RowType.VOICE_CALLS],
)
def test_continuous_eval_value_metadata_is_frozen_once_for_all_batches(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
) -> None:
    eval_id = "00000000-0000-4000-8000-000000000089"
    config_id = "00000000-0000-4000-8000-000000000090"
    task = _continuous_trace_task_for_filter(_eval_metric_filter(eval_id))
    task.row_type = row_type
    classifier_ids = ("candidate-a", "candidate-b")
    public_ids = (
        ("root-a", "root-b") if row_type == RowType.VOICE_CALLS else classifier_ids
    )
    candidates = continuous_candidates.ContinuousCandidates(
        classifier_ids,
        public_ids,
    )
    metadata = EvalFilterMetadata((config_id,), "SCORE")
    metadata_reads: list[tuple[str, tuple[str, ...]]] = []
    builder_kwargs: dict[str, object] = {}
    classified_batches: list[tuple[str, ...]] = []
    result_key = {
        RowType.SPANS: "id",
        RowType.TRACES: "trace_id",
        RowType.SESSIONS: "session_id",
        RowType.VOICE_CALLS: "root_span_id",
    }[row_type]

    class Builder:
        def __init__(self, **kwargs):
            builder_kwargs.update(kwargs)

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def recommended_filter_classify_batch_size() -> int:
            return 1

        @staticmethod
        def recommended_filter_classify_read_settings():
            return None

        @staticmethod
        def build_filter_match_query(batch, *, candidate_full_state):
            assert candidate_full_state is True
            classified_batches.append(tuple(batch))
            return "continuous_eval_value_classifier", {"candidate_ids": tuple(batch)}

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert query == "continuous_eval_value_classifier"
            candidate_id = params["candidate_ids"][0]
            public_id = (
                candidate_id.replace("candidate-", "root-")
                if row_type == RowType.VOICE_CALLS
                else candidate_id
            )
            return _FakeQueryResult([{result_key: public_id}])

    def resolve_metadata(filter_eval_id, project_ids):
        metadata_reads.append((filter_eval_id, tuple(project_ids or ())))
        return metadata

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        Analytics,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: Builder,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.filters.resolve_eval_filter_metadata",
        resolve_metadata,
    )
    monkeypatch.setattr(
        continuous_candidates,
        "sample_public_ids",
        lambda _analytics, ids, **_kwargs: tuple(ids),
    )

    resolved = row_resolver._resolve_continuous_rows(
        task,
        ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        sampling_rate=100.0,
    )

    assert metadata_reads == [(eval_id, (str(task.project_id),))]
    assert builder_kwargs["eval_filter_metadata"] == {eval_id: metadata}
    assert classified_batches == [("candidate-a",), ("candidate-b",)]
    assert resolved.candidate_ids == public_ids
    assert resolved.matched_ids == public_ids


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_class",
    [
        SpanListQueryBuilderV2,
        TraceListQueryBuilderV2,
        SessionListQueryBuilderV2,
        VoiceCallListQueryBuilderV2,
    ],
)
@pytest.mark.parametrize(
    ("output_type", "expected_sql"),
    [
        ("SCORE", "output_float >"),
        ("PASS_FAIL", "output_bool IN"),
        ("CHOICE", "JSONExtract(output_str_list, 'Array(String)')"),
    ],
)
def test_eval_value_metadata_reaches_every_bounded_builder_without_orm_reads(
    monkeypatch: pytest.MonkeyPatch,
    builder_class,
    output_type: str,
    expected_sql: str,
) -> None:
    project_id = "00000000-0000-4000-8000-000000000097"
    eval_id = "00000000-0000-4000-8000-000000000098"
    config_id = "00000000-0000-4000-8000-000000000099"
    monkeypatch.setattr(
        "tracer.services.clickhouse.query_builders.filters.resolve_eval_filter_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("bounded classifiers must use the frozen metadata")
        ),
    )
    kwargs = {
        "project_id": project_id,
        "filters": [_eval_metric_filter(eval_id, output_type=output_type)],
        "eval_filter_metadata": {
            eval_id: EvalFilterMetadata((config_id,), output_type)
        },
        "bounded_internal_scan": True,
    }
    if builder_class is TraceListQueryBuilderV2:
        kwargs.update(bounded_identity_only=True, bounded_bulk_scan=True)
    builder = builder_class(**kwargs)
    candidate_id = (
        "candidate-session"
        if builder_class is SessionListQueryBuilderV2
        else "candidate"
    )

    for _ in range(2):
        sql, params = builder.build_filter_match_query(
            [candidate_id],
            candidate_full_state=True,
        )
        assert expected_sql in sql
        assert (config_id,) in params.values()


@pytest.mark.unit
def test_continuous_eval_value_metadata_failure_stops_before_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from django.db import DatabaseError

    from tracer.models.custom_eval_config import CustomEvalConfig

    class UnavailableConfigManager:
        @staticmethod
        def filter(**_kwargs):
            raise DatabaseError("metadata backend unavailable")

    eval_id = "00000000-0000-4000-8000-000000000100"
    candidates = continuous_candidates.ContinuousCandidates(
        ("trace-a",),
        ("trace-a",),
    )
    monkeypatch.setattr(CustomEvalConfig, "objects", UnavailableConfigManager())
    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: (_ for _ in ()).throw(
            AssertionError("classifier must not be constructed")
        ),
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="evaluation metadata is temporarily unavailable",
    ):
        row_resolver._resolve_continuous_rows(
            _continuous_trace_task_for_filter(_eval_metric_filter(eval_id)),
            ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
            sampling_rate=100.0,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "row_type",
    [RowType.SPANS, RowType.TRACES, RowType.SESSIONS, RowType.VOICE_CALLS],
)
@pytest.mark.parametrize(
    "label_ids",
    [
        (
            "00000000-0000-4000-8000-000000000091",
            "00000000-0000-4000-8000-000000000092",
        ),
        (),
    ],
    ids=["configured", "known-empty"],
)
def test_continuous_annotation_filter_resolves_project_labels_once_for_all_batches(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
    label_ids: tuple[str, ...],
) -> None:
    task = _continuous_trace_task_for_filter(_has_annotation_filter())
    task.row_type = row_type
    classifier_ids = ("candidate-a", "candidate-b")
    public_ids = (
        ("root-a", "root-b") if row_type == RowType.VOICE_CALLS else classifier_ids
    )
    candidates = continuous_candidates.ContinuousCandidates(
        classifier_ids,
        public_ids,
    )
    builder_kwargs: dict[str, object] = {}
    label_reads: list[tuple[str, tuple[dict, ...]]] = []
    classified_batches: list[tuple[str, ...]] = []
    result_key = {
        RowType.SPANS: "id",
        RowType.TRACES: "trace_id",
        RowType.SESSIONS: "session_id",
        RowType.VOICE_CALLS: "root_span_id",
    }[row_type]

    class Builder:
        def __init__(self, **kwargs):
            builder_kwargs.update(kwargs)

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def recommended_filter_classify_batch_size() -> int:
            return 1

        @staticmethod
        def recommended_filter_classify_read_settings():
            return None

        @staticmethod
        def build_filter_match_query(batch, *, candidate_full_state):
            assert candidate_full_state is True
            classified_batches.append(tuple(batch))
            return "continuous_annotation_classifier", {"candidate_ids": tuple(batch)}

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert query == "continuous_annotation_classifier"
            candidate_id = params["candidate_ids"][0]
            if row_type == RowType.VOICE_CALLS:
                public_id = candidate_id.replace("candidate-", "root-")
            else:
                public_id = candidate_id
            return _FakeQueryResult([{result_key: public_id}])

    def resolve_labels(project_id, filters):
        label_reads.append((project_id, tuple(filters)))
        return label_ids

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        Analytics,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: Builder,
    )
    monkeypatch.setattr(
        row_resolver,
        "_annotation_label_ids_for_filters",
        resolve_labels,
    )
    monkeypatch.setattr(
        continuous_candidates,
        "sample_public_ids",
        lambda _analytics, ids, **_kwargs: tuple(ids),
    )

    resolved = row_resolver._resolve_continuous_rows(
        task,
        ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        sampling_rate=100.0,
    )

    assert len(label_reads) == 1
    assert builder_kwargs["annotation_label_ids"] == list(label_ids)
    assert classified_batches == [("candidate-a",), ("candidate-b",)]
    assert resolved.candidate_ids == public_ids
    assert resolved.matched_ids == public_ids


@pytest.mark.unit
def test_continuous_annotation_metadata_failure_stops_before_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = continuous_candidates.ContinuousCandidates(
        ("trace-a",),
        ("trace-a",),
    )
    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        row_resolver,
        "_annotation_label_ids_for_filters",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            row_resolver.EvalTaskReadBudgetExceeded(
                row_resolver._SAFE_ANNOTATION_METADATA_MESSAGE
            )
        ),
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: (_ for _ in ()).throw(
            AssertionError("classifier must not be constructed")
        ),
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="annotation metadata is temporarily unavailable",
    ):
        row_resolver._resolve_continuous_rows(
            _continuous_trace_task_for_filter(_has_annotation_filter()),
            ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
            sampling_rate=100.0,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_class",
    [
        SpanListQueryBuilderV2,
        TraceListQueryBuilderV2,
        SessionListQueryBuilderV2,
        VoiceCallListQueryBuilderV2,
    ],
)
@pytest.mark.parametrize(
    ("wants_annotation", "expected_predicate"),
    [(True, "1 = 1"), (False, "0 = 1")],
)
def test_continuous_builder_preserves_known_empty_annotation_label_semantics(
    builder_class,
    wants_annotation: bool,
    expected_predicate: str,
) -> None:
    kwargs = {
        "project_id": "00000000-0000-4000-8000-000000000093",
        "filters": [_has_annotation_filter(wants_annotation)],
        "annotation_label_ids": [],
        "bounded_internal_scan": True,
    }
    if builder_class is TraceListQueryBuilderV2:
        kwargs.update(bounded_identity_only=True, bounded_bulk_scan=True)
    builder = builder_class(**kwargs)
    candidate_id = (
        "candidate-session"
        if builder_class is SessionListQueryBuilderV2
        else "candidate"
    )

    sql, _ = builder.build_filter_match_query(
        [candidate_id],
        candidate_full_state=True,
    )

    assert builder._annotation_label_set_known is True
    assert "from model_hub_score" not in sql.lower()
    assert expected_predicate in sql


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_class",
    [
        SpanListQueryBuilderV2,
        TraceListQueryBuilderV2,
        SessionListQueryBuilderV2,
        VoiceCallListQueryBuilderV2,
    ],
)
def test_continuous_builder_requires_every_configured_annotation_label(
    builder_class,
) -> None:
    label_ids = [
        "00000000-0000-4000-8000-000000000094",
        "00000000-0000-4000-8000-000000000095",
    ]
    kwargs = {
        "project_id": "00000000-0000-4000-8000-000000000096",
        "filters": [_has_annotation_filter()],
        "annotation_label_ids": label_ids,
        "bounded_internal_scan": True,
    }
    if builder_class is TraceListQueryBuilderV2:
        kwargs.update(bounded_identity_only=True, bounded_bulk_scan=True)
    builder = builder_class(**kwargs)
    candidate_id = (
        "candidate-session"
        if builder_class is SessionListQueryBuilderV2
        else "candidate"
    )

    sql, params = builder.build_filter_match_query(
        [candidate_id],
        candidate_full_state=True,
    )

    assert builder._annotation_label_set_known is True
    assert "uniqExact(s.label_id) >= 2" in sql
    assert all(label_id in params.values() for label_id in label_ids)


@pytest.mark.unit
def test_eval_config_metadata_failure_is_sanitized() -> None:
    from unittest import mock

    from django.db import DatabaseError

    with mock.patch(
        "tracer.models.custom_eval_config.CustomEvalConfig.objects"
    ) as config_manager:
        config_manager.filter.side_effect = DatabaseError("metadata unavailable")
        with pytest.raises(
            row_resolver.EvalTaskReadBudgetExceeded,
            match="evaluation metadata is temporarily unavailable",
        ):
            row_resolver._eval_config_ids_for_filters(
                str(uuid.uuid4()),
                [
                    {
                        "column_id": "has_eval",
                        "filter_config": {
                            "filter_type": "boolean",
                            "filter_op": "equals",
                            "filter_value": True,
                        },
                    }
                ],
            )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_item", "recommended_read_settings"),
    [
        (_attribute_filter(), None),
        (
            _structured_attribute_filter(),
            {
                "max_block_size": 2_048,
                "preferred_max_column_in_block_size_bytes": 1_048_576,
            },
        ),
    ],
    ids=["scalar-map", "structured-json"],
)
def test_continuous_10k_custom_attribute_classifier_has_finite_exact_budget(
    monkeypatch: pytest.MonkeyPatch,
    filter_item: dict,
    recommended_read_settings: dict | None,
) -> None:
    """Batch ten can prove all 10k candidates without relaxing one query."""

    ids = tuple(f"trace-{index:05d}" for index in range(10_000))
    candidates = continuous_candidates.ContinuousCandidates(ids, ids)
    clock = SimpleNamespace(value=0.0)
    calls: list[dict] = []
    builder_kwargs: dict = {}

    class Builder:
        def __init__(self, **kwargs):
            builder_kwargs.update(kwargs)

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def recommended_filter_classify_batch_size() -> int:
            return 10

        @staticmethod
        def recommended_filter_classify_read_settings():
            return recommended_read_settings

        @staticmethod
        def build_filter_match_query(batch, *, candidate_full_state):
            assert candidate_full_state is True
            return "continuous_classifier", {"candidate_ids": tuple(batch)}

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert query == "continuous_classifier"
            calls.append(
                {
                    "ids": params["candidate_ids"],
                    "timeout_ms": timeout_ms,
                    "settings": settings,
                }
            )
            # Simulate a loaded but successful statement. The old fixed five
            # second classifier window would fail during the fourth batch.
            clock.value += 1.4
            return _FakeQueryResult(
                [
                    {
                        "trace_id": trace_id,
                        "filter_witness_0": (
                            f"span-{trace_id}",
                            datetime(2026, 8, 8, 11, 0, tzinfo=UTC),
                        ),
                    }
                    for trace_id in reversed(params["candidate_ids"])
                ]
            )

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        Analytics,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: Builder,
    )
    monkeypatch.setattr(
        row_resolver,
        "time",
        SimpleNamespace(monotonic=lambda: clock.value),
    )

    resolved = row_resolver._resolve_continuous_rows(
        _continuous_trace_task_for_filter(filter_item),
        ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        sampling_rate=100.0,
    )

    assert row_resolver._continuous_classifier_budget_ms(
        candidate_count=10_000,
        classify_size=10,
    ) == (1_000, 3_505_000)
    assert len(calls) == 1_000
    assert all(len(call["ids"]) == 10 for call in calls)
    assert all(call["timeout_ms"] == 3_000 for call in calls)
    for call in calls:
        settings = call["settings"]
        assert settings["max_execution_time"] == 3
        assert settings["timeout_overflow_mode"] == "throw"
        assert settings["max_threads"] == 1
        assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
        assert settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
        assert settings["read_overflow_mode"] == "throw"
        assert settings["max_result_rows"] == 10
        assert settings["result_overflow_mode"] == "throw"
        # The application-qualified block cap is universal for eval/task
        # classifiers; a builder recommendation may only tighten it.
        assert settings["max_block_size"] == 2_048
        if recommended_read_settings is None:
            assert "preferred_max_column_in_block_size_bytes" not in settings
        else:
            assert settings["preferred_max_column_in_block_size_bytes"] == 1_048_576
    assert builder_kwargs["bounded_internal_scan"] is True
    assert builder_kwargs["bounded_identity_only"] is True
    assert builder_kwargs["bounded_bulk_scan"] is True
    assert resolved.candidate_ids == ids
    assert resolved.matched_ids == ids
    assert resolved.full_state is False


@pytest.mark.unit
def test_continuous_discovery_restores_background_two_minute_wall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, float] = {}

    def discover(*_args, **kwargs):
        observed["deadline_seconds"] = kwargs["deadline_seconds"]
        return continuous_candidates.ContinuousCandidates((), ())

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        discover,
    )

    resolved = row_resolver._resolve_continuous_rows(
        _continuous_trace_task_for_filter(_attribute_filter()),
        ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        sampling_rate=100.0,
    )

    # Regression for the US-prod failure where 2.2s + 1.2s discovery reads
    # exhausted a new four-second (10s * 0.4) wall before the next statement.
    # Continuous reconciliation is background work and retains per-statement
    # caps, but its shared discovery proof gets the former two-minute envelope.
    assert observed == {"deadline_seconds": 120.0}
    assert resolved == row_resolver.ResolvedRowSet((), (), False)


@pytest.mark.unit
@pytest.mark.parametrize("row_type", [RowType.TRACES, RowType.VOICE_CALLS])
def test_continuous_candidate_overflow_proves_bounded_prefix_in_shared_budget(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
) -> None:
    floor = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    requested_ceiling = floor + timedelta(minutes=40)
    minimum_ceiling = floor + CONTINUOUS_MIN_PROOF_WINDOW
    attempted: list[datetime] = []
    budgets: list[continuous_candidates._ReadBudget] = []

    def prove_window(*_args, ceiling, budget, **_kwargs):
        attempted.append(ceiling)
        budgets.append(budget)
        if ceiling - floor > timedelta(minutes=10):
            raise continuous_candidates.ContinuousCandidateOverflow("too dense")
        return continuous_candidates.ContinuousCandidates(
            ("trace-a",),
            ("root-a" if row_type == RowType.VOICE_CALLS else "trace-a",),
        )

    monkeypatch.setattr(
        continuous_candidates,
        "_discover_continuous_candidate_window",
        prove_window,
    )

    result = continuous_candidates.discover_continuous_candidates(
        object(),
        project_id=str(uuid.uuid4()),
        row_type=row_type,
        filters=[],
        floor=floor,
        ceiling=requested_ceiling,
        salt="task",
        sampling_rate=100.0,
        deadline_seconds=120.0,
        minimum_ceiling=minimum_ceiling,
    )

    assert attempted == [
        floor + timedelta(minutes=40),
        floor + timedelta(minutes=20),
        floor + timedelta(minutes=10),
    ]
    assert len({id(budget) for budget in budgets}) == 1
    assert result.covered_through == floor + timedelta(minutes=10)
    assert result.classifier_ids == ("trace-a",)
    assert result.covered_through - CONTINUOUS_CURSOR_OVERLAP > floor


@pytest.mark.unit
def test_continuous_candidate_overflow_below_progress_floor_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    floor = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    requested_ceiling = floor + timedelta(minutes=40)
    minimum_ceiling = floor + CONTINUOUS_MIN_PROOF_WINDOW
    attempted: list[datetime] = []

    def always_overflow(*_args, ceiling, **_kwargs):
        attempted.append(ceiling)
        raise continuous_candidates.ContinuousCandidateOverflow("too dense")

    monkeypatch.setattr(
        continuous_candidates,
        "_discover_continuous_candidate_window",
        always_overflow,
    )

    with pytest.raises(
        continuous_candidates.ContinuousCandidateOverflow,
        match="too dense",
    ):
        continuous_candidates.discover_continuous_candidates(
            object(),
            project_id=str(uuid.uuid4()),
            row_type=RowType.TRACES,
            filters=[],
            floor=floor,
            ceiling=requested_ceiling,
            salt="task",
            sampling_rate=100.0,
            deadline_seconds=120.0,
            minimum_ceiling=minimum_ceiling,
        )

    assert attempted[-1] == minimum_ceiling
    assert all(
        attempted[index + 1] < attempted[index] for index in range(len(attempted) - 1)
    )


@pytest.mark.unit
def test_repeated_overflow_slices_catch_up_monotonically_without_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_floor = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    requested_ceiling = initial_floor + timedelta(hours=1)
    cursor = initial_floor
    proven_windows: list[tuple[datetime, datetime]] = []

    def prove_at_most_ten_minutes(*_args, floor, ceiling, **_kwargs):
        if ceiling - floor > timedelta(minutes=10):
            raise continuous_candidates.ContinuousCandidateOverflow("too dense")
        return continuous_candidates.ContinuousCandidates(("trace-a",), ("trace-a",))

    monkeypatch.setattr(
        continuous_candidates,
        "_discover_continuous_candidate_window",
        prove_at_most_ten_minutes,
    )

    for _ in range(100):
        result = continuous_candidates.discover_continuous_candidates(
            object(),
            project_id=str(uuid.uuid4()),
            row_type=RowType.TRACES,
            filters=[],
            floor=cursor,
            ceiling=requested_ceiling,
            salt="task",
            sampling_rate=100.0,
            deadline_seconds=120.0,
            minimum_ceiling=cursor + CONTINUOUS_MIN_PROOF_WINDOW,
        )
        assert result.covered_through is not None
        proven_windows.append((cursor, result.covered_through))
        if result.covered_through == requested_ceiling:
            break
        next_cursor = result.covered_through - CONTINUOUS_CURSOR_OVERLAP
        assert next_cursor > cursor
        cursor = next_cursor
    else:
        pytest.fail("bounded catch-up did not reach the requested ceiling")

    assert proven_windows[-1][1] == requested_ceiling
    assert all(
        later_floor <= earlier_ceiling
        for (_, earlier_ceiling), (later_floor, _) in zip(
            proven_windows,
            proven_windows[1:],
            strict=False,
        )
    )


@pytest.mark.unit
@pytest.mark.parametrize("row_type", [RowType.TRACES, RowType.VOICE_CALLS])
def test_continuous_resolver_publishes_exact_candidate_proof_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    row_type: str,
) -> None:
    task = _continuous_trace_task_for_filter(_attribute_filter())
    task.row_type = row_type
    requested_ceiling = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    proven_ceiling = requested_ceiling - timedelta(minutes=2)
    observed: dict[str, datetime | None] = {}

    def discover(*_args, **kwargs):
        observed["minimum_ceiling"] = kwargs["minimum_ceiling"]
        return continuous_candidates.ContinuousCandidates(
            (),
            (),
            proven_ceiling,
        )

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        discover,
    )

    resolved = row_resolver._resolve_continuous_rows(
        task,
        ceiling=requested_ceiling,
        sampling_rate=100.0,
    )

    assert observed["minimum_ceiling"] == (
        task.continuous_cursor + CONTINUOUS_MIN_PROOF_WINDOW
    )
    assert resolved.covered_through == proven_ceiling
    assert resolved.full_state is False


@pytest.mark.unit
def test_cursor_null_continuous_proof_cannot_be_sliced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _continuous_trace_task_for_filter(_attribute_filter())
    task.continuous_cursor = None
    observed: dict[str, datetime | None] = {}

    def discover(*_args, **kwargs):
        observed["minimum_ceiling"] = kwargs["minimum_ceiling"]
        return continuous_candidates.ContinuousCandidates((), ())

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        discover,
    )

    resolved = row_resolver._resolve_continuous_rows(
        task,
        ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        sampling_rate=100.0,
    )

    assert observed == {"minimum_ceiling": None}
    assert resolved.full_state is True


@pytest.mark.unit
def test_irreducibly_dense_candidate_window_remains_deterministic_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def overflow(*_args, **_kwargs):
        raise continuous_candidates.ContinuousCandidateOverflow("too dense")

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        overflow,
    )

    with pytest.raises(
        row_resolver.EvalTaskSelectionRejected,
        match="too large for safe row selection",
    ):
        row_resolver._resolve_continuous_rows(
            _continuous_trace_task_for_filter(_attribute_filter()),
            ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
            sampling_rate=100.0,
        )


@pytest.mark.unit
def test_adaptive_overflow_probe_pressure_remains_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _continuous_trace_task_for_filter(_attribute_filter())
    ceiling = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    task.continuous_cursor = ceiling - timedelta(minutes=40)
    calls = 0

    def fail_after_overflow(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise continuous_candidates.ContinuousCandidateOverflow("too dense")
        raise continuous_candidates.ContinuousCandidateReadError("temporary pressure")

    monkeypatch.setattr(
        continuous_candidates,
        "_discover_continuous_candidate_window",
        fail_after_overflow,
    )

    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded) as captured:
        row_resolver._resolve_continuous_rows(
            task,
            ceiling=ceiling,
            sampling_rate=100.0,
        )

    assert type(captured.value) is row_resolver.EvalTaskReadBudgetExceeded
    assert calls == 2


@pytest.mark.unit
def test_continuous_classifier_query_cap_fails_before_first_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = tuple(f"trace-{index:05d}" for index in range(10_000))
    candidates = continuous_candidates.ContinuousCandidates(ids, ids)
    calls = 0

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def recommended_filter_classify_batch_size() -> int:
            return 5

        @staticmethod
        def recommended_filter_classify_read_settings():
            return None

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            nonlocal calls
            calls += 1
            raise AssertionError("preflight must reject before the first query")

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        Analytics,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: Builder,
    )

    with pytest.raises(
        row_resolver.EvalTaskSelectionRejected,
        match="exceeded its read budget",
    ):
        row_resolver._resolve_continuous_rows(
            _continuous_trace_task_for_filter(_attribute_filter()),
            ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
            sampling_rate=100.0,
        )

    assert calls == 0


@pytest.mark.unit
def test_continuous_candidate_query_cap_is_deterministic_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = continuous_candidates._ReadBudget(
        continuous_candidates.time.monotonic() + 30,
        attempts=continuous_candidates._MAX_QUERY_ATTEMPTS,
    )

    def discover(*_args, **_kwargs):
        # Exercise the same cap classification through the public resolver.
        budget.timeout_ms()

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        discover,
    )

    with pytest.raises(
        row_resolver.EvalTaskSelectionRejected,
        match="exceeded its read budget",
    ):
        row_resolver._resolve_continuous_rows(
            _continuous_trace_task_for_filter(_attribute_filter()),
            ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
            sampling_rate=100.0,
        )


@pytest.mark.unit
def test_continuous_candidate_read_wraps_only_transient_failures() -> None:
    class Analytics:
        def __init__(self, failure: Exception):
            self.failure = failure

        def execute_ch_query(self, *_args, **_kwargs):
            raise self.failure

    def budget() -> continuous_candidates._ReadBudget:
        return continuous_candidates._ReadBudget(
            continuous_candidates.time.monotonic() + 30
        )

    with pytest.raises(
        continuous_candidates.ContinuousCandidateReadError,
        match="TimeoutError",
    ):
        continuous_candidates._execute(
            Analytics(TimeoutError("temporary ClickHouse timeout")),
            "SELECT 1",
            {},
            budget(),
        )

    programming_error = RuntimeError("malformed candidate query")
    with pytest.raises(RuntimeError, match="malformed candidate query") as captured:
        continuous_candidates._execute(
            Analytics(programming_error),
            "SELECT broken",
            {},
            budget(),
        )
    assert captured.value is programming_error


@pytest.mark.unit
def test_continuous_late_classifier_failure_never_returns_partial_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = tuple(f"trace-{index:02d}" for index in range(25))
    candidates = continuous_candidates.ContinuousCandidates(ids, ids)
    calls = 0

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def recommended_filter_classify_batch_size() -> int:
            return 10

        @staticmethod
        def recommended_filter_classify_read_settings():
            return {"max_block_size": 2_048}

        @staticmethod
        def build_filter_match_query(batch, *, candidate_full_state):
            assert candidate_full_state is True
            return "continuous_classifier", {"candidate_ids": tuple(batch)}

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            nonlocal calls
            calls += 1
            assert timeout_ms == 3_000
            if calls == 2:
                raise TimeoutError("simulated second-batch timeout")
            return _FakeQueryResult(
                [{"trace_id": trace_id} for trace_id in params["candidate_ids"]]
            )

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        Analytics,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: Builder,
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="exceeded its read budget",
    ):
        row_resolver._resolve_continuous_rows(
            _continuous_trace_task_for_filter(_structured_attribute_filter()),
            ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
            sampling_rate=100.0,
        )

    # The first ten matches were only local buffered state. No ResolvedRowSet
    # escaped after the second statement invalidated the exact proof.
    assert calls == 2


@pytest.mark.unit
def test_continuous_missing_later_filter_witness_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete ID set cannot escape with an incomplete witness matrix."""

    ids = tuple(f"trace-{index:02d}" for index in range(15))
    candidates = continuous_candidates.ContinuousCandidates(ids, ids)
    calls = 0
    witness_time = datetime(2026, 8, 8, 11, 0, tzinfo=UTC)

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan() -> bool:
            return True

        @staticmethod
        def recommended_filter_classify_batch_size() -> int:
            return 10

        @staticmethod
        def recommended_filter_classify_read_settings():
            return {"max_block_size": 2_048}

        @staticmethod
        def build_filter_match_query(batch, *, candidate_full_state):
            assert candidate_full_state is True
            return "continuous_classifier", {"candidate_ids": tuple(batch)}

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            nonlocal calls
            calls += 1
            assert query == "continuous_classifier"
            assert timeout_ms == 3_000
            rows = [
                {
                    "trace_id": trace_id,
                    "filter_witness_0": (f"span-{trace_id}", witness_time),
                }
                for trace_id in params["candidate_ids"]
            ]
            if calls == 2:
                rows[-1].pop("filter_witness_0")
            return _FakeQueryResult(rows)

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        Analytics,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.dispatch.get_v2_class",
        lambda _query_type: Builder,
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="exceeded its read budget",
    ):
        row_resolver._resolve_continuous_rows(
            _continuous_trace_task_for_filter(_attribute_filter()),
            ceiling=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
            sampling_rate=100.0,
        )

    assert calls == 2


@pytest.mark.unit
def test_eval_trigger_pages_unrelated_tenants_without_global_future_sets() -> None:
    """Unrelated volume is exhausted, not capped ahead of the target page."""

    relation_calls = 0
    observed_sql: list[str] = []
    target_session = str(uuid.uuid4())

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            nonlocal relation_calls
            observed_sql.append(query)
            assert timeout_ms > 0
            if "FROM tracer_eval_logger_v2" in query:
                relation_calls += 1
                if relation_calls == 1:
                    return _FakeQueryResult(
                        [
                            {
                                "arrival_order": index + 1,
                                "relation_id": str(uuid.uuid4()),
                                "trace_id": f"other-{index}",
                                "span_id": "",
                                "session_id": "",
                            }
                            for index in range(200)
                        ]
                    )
                if relation_calls == 2:
                    return _FakeQueryResult(
                        [
                            {
                                "arrival_order": 201,
                                "relation_id": str(uuid.uuid4()),
                                "trace_id": "target-trace",
                                "span_id": "",
                                "session_id": "",
                            }
                        ]
                    )
                raise AssertionError("keyset reader must stop after short page")
            if "FROM spans" in query:
                trace_ids = params.get("relation_trace_ids", ())
                if "target-trace" in trace_ids:
                    return _FakeQueryResult(
                        [
                            {
                                "trace_id": "target-trace",
                                "id": "target-span",
                                "session_id": target_session,
                            }
                        ]
                    )
                return _FakeQueryResult([])
            raise AssertionError(query)

    budget = continuous_candidates._ReadBudget(
        continuous_candidates.time.monotonic() + 5
    )
    affected = continuous_candidates._read_relation_refs_paged(
        Analytics(),
        project_id=str(uuid.uuid4()),
        table="tracer_eval_logger_v2",
        arrival_column="_version",
        arrival_predicate="_version >= 1 AND _version < 999",
        bounds={},
        project_predicate="",
        budget=budget,
    )

    assert relation_calls == 2
    assert affected == [("target-trace", "target-span", target_session)]
    assert all(" IN (\n            SELECT" not in sql for sql in observed_sql)
    span_queries = [sql for sql in observed_sql if "FROM spans" in sql]
    assert span_queries
    assert all("project_id = toUUID(%(project_id)s)" in sql for sql in span_queries)


@pytest.mark.unit
@pytest.mark.parametrize("configured_table", ["legacy", "unset"])
def test_changed_eval_refs_follow_authoritative_legacy_table(
    settings, configured_table
) -> None:
    if configured_table == "legacy":
        settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    else:
        del settings.CH25_EVAL_LOGGER_TABLE

    observed: dict[str, object] = {}

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            observed["query"] = query
            observed["params"] = params
            return _FakeQueryResult([])

    floor = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    ceiling = floor + timedelta(minutes=5)
    budget = continuous_candidates._ReadBudget(
        continuous_candidates.time.monotonic() + 5
    )

    affected = continuous_candidates._read_changed_eval_refs(
        Analytics(),
        project_id=str(uuid.uuid4()),
        floor=floor,
        ceiling=ceiling,
        budget=budget,
    )

    assert affected == []
    query = str(observed["query"])
    assert "FROM tracer_eval_logger\n" in query
    assert "FROM tracer_eval_logger_v2" not in query
    assert "status" not in query
    assert "skipped_reason" not in query
    assert "config_hash" not in query
    assert "attempts" not in query
    assert "_peerdb_synced_at >= %(arrival_floor)s" in query
    assert "_peerdb_synced_at < %(arrival_ceiling)s" in query
    assert observed["params"]["arrival_floor"] == floor
    assert observed["params"]["arrival_ceiling"] == ceiling


@pytest.mark.unit
def test_annotation_trigger_is_tenant_scoped_before_page_limit() -> None:
    observed: dict[str, object] = {}

    class Analytics:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            observed["query"] = query
            observed["params"] = params
            return _FakeQueryResult([])

    budget = continuous_candidates._ReadBudget(
        continuous_candidates.time.monotonic() + 5
    )
    project_id = str(uuid.uuid4())
    continuous_candidates._read_changed_annotation_refs(
        Analytics(),
        project_id=project_id,
        floor=datetime.now(UTC) - timedelta(minutes=5),
        ceiling=datetime.now(UTC),
        budget=budget,
    )

    assert "tracer_project_id = toUUID(%(project_id)s)" in observed["query"]
    assert observed["query"].index("tracer_project_id") < observed["query"].index(
        "LIMIT %(relation_page_size)s"
    )
    assert observed["params"]["project_id"] == project_id
