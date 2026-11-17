"""Session tasks evaluate complete snapshots and revisit changed conversations.

Run with the normal Django/ClickHouse integration harness, not agentic_eval's
standalone pytest configuration. Model calls and billing are stubbed.
"""

import hashlib
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.selectors.eval_tasks.row_resolver import ResolvedRowSet
from tracer.services.eval_tasks.entries import (
    claim_pending_batch,
    engine_entry_run,
    writing_onto_entry,
)
from tracer.services.eval_tasks.reconciler import (
    _apply_resolved,
    _requeue_and_drop,
    reconcile,
)
from tracer.services.eval_tasks.run_entry import run_entry
from tracer.tests._ch_seed import seed_ch_spans, seed_ch_trace, seed_ch_trace_sessions
from tracer.utils import eval as eval_utils
from tracer.views.observation_span import ObservationSpanView

COMPLETE = "conversation_complete"
REVISION = "_session_conversation_revision"


@pytest.mark.unit
def test_complete_conversation_preserves_every_payload_and_middle_turn(monkeypatch):
    traces = [
        SimpleNamespace(
            id=str(i),
            input={
                "messages": [
                    {"role": "system", "content": "Keep the context"},
                    {"role": "user", "content": f"question-{i}" + "x" * 5000},
                    {"role": "user", "content": "another message"},
                ]
            },
            output={"content": f"answer-{i}" + "y" * 5000},
        )
        for i in range(20)
    ]
    monkeypatch.setattr(eval_utils, "_session_traces_ch", lambda _: traces)
    from tracer.services.clickhouse.v2.eval_loader import eval_read_source

    with eval_read_source("clickhouse"):
        complete = eval_utils._resolve_session_path(
            SimpleNamespace(id="session"), COMPLETE
        )
    assert isinstance(complete, str)
    assert len(complete) > 60000
    rows = json.loads(complete)
    assert rows == [
        {"trace_id": t.id, "input": t.input, "output": t.output} for t in traces
    ]
    assert "question-10" in complete and "answer-10" in complete
    # Existing preview/Instant Eval mappings retain their bounded contract.
    assert "turns omitted" in eval_utils.render_session_transcript(traces)


@pytest.mark.unit
def test_attribute_endpoint_exposes_complete_and_bounded_mappings():
    paths = ObservationSpanView()._build_session_attribute_paths(
        "project", [], max_traces=0, max_spans=0
    )
    assert {COMPLETE, "transcript", "transcript_full"} <= set(paths)


@pytest.mark.unit
def test_revision_tracks_input_and_order_but_not_eval_metadata(monkeypatch):
    traces = [
        SimpleNamespace(id="first", input={"a": "hello", "b": "world"}, output="one"),
        SimpleNamespace(id="second", input="next", output="two"),
    ]
    monkeypatch.setattr(eval_utils, "_session_traces_ch", lambda _: traces)
    session = SimpleNamespace()
    original = eval_utils.session_conversation_revision(session)
    traces[0].input = {"b": "world", "a": "hello"}
    traces[0].metadata = {"eval_result": "new"}
    assert eval_utils.session_conversation_revision(session) == original
    traces[0].input["a"] = "corrected"
    corrected = eval_utils.session_conversation_revision(session)
    assert corrected != original
    traces.reverse()
    assert eval_utils.session_conversation_revision(session) != corrected


@pytest.mark.unit
def test_missing_member_payload_is_retryable_not_a_shortened_conversation(monkeypatch):
    from tracer.services.clickhouse.v2.eval_loader import EvalTelemetryReadError

    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader.session_trace_ids.return_value = ["not-yet-hydrated"]
    reader.per_trace_root_span_start_times.return_value = {}
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    def missing_trace(*args, **kwargs):
        raise Trace.DoesNotExist()

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.eval_loader.get_trace", missing_trace
    )
    with pytest.raises(EvalTelemetryReadError, match="not finished arriving"):
        eval_utils._session_traces_ch(
            SimpleNamespace(id="session", project_id="project")
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "mapping, covered",
    [
        ({"input": COMPLETE}, True),
        ({"input": COMPLETE, "preview": "transcript"}, True),
        ({"input": "transcript_full"}, False),
        ({"input": "name"}, False),
        ({"input": "traces.0.spans.0.span_attributes.answer"}, False),
        (
            {"input": COMPLETE, "answer": "traces.0.spans.0.span_attributes.answer"},
            False,
        ),
        ({}, False),
    ],
)
def test_conversation_revision_only_covers_trace_payload_mappings(mapping, covered):
    assert eval_utils.session_mapping_uses_conversation_revision(mapping) is covered


@pytest.mark.unit
def test_session_usage_identity_changes_with_source_not_on_retry():
    identities = []
    for revision in ("source-a", "source-a", "source-b"):
        with writing_onto_entry(
            "entry", config_hash="config", output_metadata={REVISION: revision}
        ):
            identities.append(engine_entry_run())
    assert identities[0] == identities[1]
    assert identities[1] != identities[2]
    assert engine_entry_run() is None


def _turn(project, session, index, *, name="conversation-turn", message_size=0):
    started = timezone.now() - timedelta(minutes=10 - index)
    trace = Trace(
        id=uuid.uuid4(),
        project=project,
        session=session,
        name=name,
        input={
            "messages": [
                {"role": "user", "content": f"question-{index}" + "x" * message_size}
            ]
        },
        output={"content": f"answer-{index}"},
        created_at=started,
    )
    seed_ch_trace(trace)
    span = ObservationSpan(
        id=f"conversation-{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=name,
        observation_type="llm",
        parent_span_id=None,
        span_attributes={"conversation.match": name},
        start_time=started,
        end_time=started + timedelta(seconds=1),
        input=trace.input,
        output=trace.output,
        created_at=timezone.now() - timedelta(seconds=1),
    )
    seed_ch_spans([span], version_from_created_at=True)
    return trace


@pytest.fixture
def conversation_task(observe_project, eval_template, monkeypatch):
    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry._usage_refusal", lambda *_: None
    )
    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry._reseed_eval_clustering", lambda *_: None
    )
    session = TraceSession.objects.create(project=observe_project, name="Conversation")
    seed_ch_trace_sessions([session])
    config = CustomEvalConfig.objects.create(
        name="Complete conversation",
        project=observe_project,
        eval_template=eval_template,
        config={"threshold": 0.8},
        mapping={"input": COMPLETE},
        filters={},
    )
    task = EvalTask.objects.create(
        project=observe_project,
        name="Continuous conversations",
        filters={},
        row_type=RowType.SESSIONS,
        sampling_rate=100.0,
        spans_limit=100,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        start_time=timezone.now() - timedelta(hours=1),
    )
    task.evals.add(config)
    return task, session, config


def _run_one(task):
    entries = claim_pending_batch(task, 10)
    assert len(entries) == 1
    return run_entry(entries[0]), entries[0]


def _revisit(task, session):
    return _requeue_and_drop(
        task, resolved=ResolvedRowSet((str(session.id),), (str(session.id),), False)
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestSessionConversationTasks:
    def test_ordered_all_turns_one_session_result_and_foreign_project_control(
        self,
        conversation_task,
        project,
        stub_run_eval,
        stub_cost_log,
    ):
        task, session, _ = conversation_task
        last = _turn(task.project, session, 3)
        first = _turn(task.project, session, 1)
        middle = _turn(task.project, session, 2)
        foreign_session = TraceSession.objects.create(project=project, name="Foreign")
        seed_ch_trace_sessions([foreign_session])
        _turn(project, foreign_session, 0)

        assert reconcile(task).created == 1
        status, entry = _run_one(task)
        entry.refresh_from_db()
        assert status == EvalEntryStatus.COMPLETED, entry.error_message
        assert len(stub_run_eval.requests) == 1
        payload = stub_run_eval.requests[0].inputs["input"]
        assert json.loads(payload) == [
            {"trace_id": str(t.id), "input": t.input, "output": t.output}
            for t in (first, middle, last)
        ]
        assert entry.trace_session_id == session.id
        assert entry.trace_id is None and entry.observation_span_id is None
        assert EvalLogger.objects.filter(eval_task_id=str(task.id)).count() == 1
        assert (
            entry.output_metadata[REVISION]
            == hashlib.sha256(payload.encode()).hexdigest()
        )
        assert reconcile(task).requeued == 0

    def test_filter_selects_session_without_discarding_its_unmatched_turns(
        self,
        conversation_task,
        stub_run_eval,
        stub_cost_log,
    ):
        task, session, _ = conversation_task
        first = _turn(task.project, session, 1)
        middle = _turn(task.project, session, 2, name="selected")
        last = _turn(task.project, session, 3)
        other = TraceSession.objects.create(project=task.project, name="Not selected")
        seed_ch_trace_sessions([other])
        _turn(task.project, other, 1)
        task.filters = {
            "filters": [
                {
                    "column_id": "conversation.match",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_op": "in",
                        "filter_type": "text",
                        "filter_value": ["selected"],
                    },
                }
            ]
        }
        task.save(update_fields=["filters"])
        assert reconcile(task).created == 1
        assert _run_one(task)[0] == EvalEntryStatus.COMPLETED
        rows = json.loads(stub_run_eval.requests[0].inputs["input"])
        assert [row["trace_id"] for row in rows] == [
            str(t.id) for t in (first, middle, last)
        ]

    def test_pause_resume_preserves_snapshot_and_notices_new_turn(
        self,
        conversation_task,
        stub_run_eval,
        stub_cost_log,
    ):
        task, session, _ = conversation_task
        _turn(task.project, session, 1)
        reconcile(task)
        assert _run_one(task)[0] == EvalEntryStatus.COMPLETED
        entry = EvalLogger.objects.get(eval_task_id=str(task.id))
        revision, config_hash = entry.output_metadata[REVISION], entry.config_hash
        task.status = EvalTaskStatus.PAUSED
        task.save(update_fields=["status"])
        _turn(task.project, session, 2)
        entry.refresh_from_db()
        assert entry.output_metadata[REVISION] == revision
        task.status = EvalTaskStatus.PENDING
        task.save(update_fields=["status"])
        assert reconcile(task).requeued == 1
        assert _run_one(task)[0] == EvalEntryStatus.COMPLETED
        entry.refresh_from_db()
        assert entry.output_metadata[REVISION] != revision
        assert entry.config_hash == config_hash

    def test_append_requeues_once_but_eval_metadata_and_overlap_do_not(
        self,
        conversation_task,
        stub_run_eval,
        stub_cost_log,
    ):
        task, session, config = conversation_task
        _turn(task.project, session, 1)
        reconcile(task)
        status, entry = _run_one(task)
        assert status == EvalEntryStatus.COMPLETED
        entry.refresh_from_db()
        revision = entry.output_metadata[REVISION]
        EvalLogger.objects.filter(id=entry.id).update(
            output_metadata={
                **entry.output_metadata,
                "judge_diagnostics": "changed without a new turn",
            }
        )
        assert _revisit(task, session) == (0, 0)
        _turn(task.project, session, 2)
        assert reconcile(task).requeued == 1
        assert _revisit(task, session) == (0, 0)
        assert _run_one(task)[0] == EvalEntryStatus.COMPLETED
        entry.refresh_from_db()
        assert entry.output_metadata[REVISION] != revision
        assert _revisit(task, session) == (0, 0)
        assert len(stub_run_eval.requests) == 2
        # A config edit still requeues an unchanged conversation.
        config.config = {"threshold": 0.9}
        config.save(update_fields=["config"])
        assert _revisit(task, session) == (1, 0)

    def test_snapshot_revision_is_not_replaced_by_a_turn_arriving_during_call(
        self,
        conversation_task,
        stub_run_eval,
        stub_cost_log,
        monkeypatch,
    ):
        task, session, _ = conversation_task
        _turn(task.project, session, 1)
        reconcile(task)
        execute = eval_utils._execute_evaluation_for_session
        captured = {}

        def append_during_call(**kwargs):
            captured["payload"] = kwargs["run_params"]["input"]
            _turn(task.project, session, 2)
            running = EvalLogger.objects.get(eval_task_id=str(task.id))
            # A reconcile during a slow call must not advance past turns it
            # cannot requeue while the entry is still RUNNING.
            task.refresh_from_db()
            _apply_resolved(
                task,
                resolved=ResolvedRowSet(
                    (str(session.id),),
                    (str(session.id),),
                    False,
                ),
                now=running.updated_at + timedelta(minutes=20),
            )
            task.refresh_from_db()
            assert task.continuous_cursor <= running.updated_at
            return execute(**kwargs)

        monkeypatch.setattr(
            eval_utils, "_execute_evaluation_for_session", append_during_call
        )
        status, entry = _run_one(task)
        assert status == EvalEntryStatus.COMPLETED
        entry.refresh_from_db()
        assert (
            entry.output_metadata[REVISION]
            == hashlib.sha256(captured["payload"].encode()).hexdigest()
        )
        assert len(json.loads(captured["payload"])) == 1
        assert reconcile(task).requeued == 1

    @pytest.mark.parametrize("include_conversation", [False, True])
    def test_corrected_span_mapping_retries_with_legacy_usage_identity(
        self,
        conversation_task,
        stub_run_eval,
        stub_cost_log,
        monkeypatch,
        include_conversation,
    ):
        task, session, config = conversation_task
        config.mapping = {"input": "traces.0.spans.0.span_attributes.answer"}
        if include_conversation:
            config.mapping["conversation"] = COMPLETE
        config.save(update_fields=["mapping"])
        trace = Trace(
            id=uuid.uuid4(),
            project=task.project,
            session=session,
            input="unchanged question",
            output="unchanged answer",
            created_at=timezone.now() - timedelta(minutes=5),
        )
        seed_ch_trace(trace)
        span = ObservationSpan(
            id=f"answer-{uuid.uuid4().hex[:16]}",
            project=task.project,
            trace=trace,
            name="answer",
            observation_type="llm",
            parent_span_id=None,
            span_attributes={"answer": "incorrect"},
            start_time=trace.created_at,
            end_time=trace.created_at + timedelta(seconds=1),
            created_at=timezone.now() - timedelta(seconds=1),
        )
        seed_ch_spans([span], version_from_created_at=True)
        identities = []
        execute = eval_utils._execute_evaluation_for_session

        def capture_identity(**kwargs):
            identities.append(engine_entry_run())
            return execute(**kwargs)

        monkeypatch.setattr(
            eval_utils, "_execute_evaluation_for_session", capture_identity
        )
        stub_run_eval(failure="Correct the mapped answer before retrying")
        assert reconcile(task).created == 1
        status, entry = _run_one(task)
        assert status == EvalEntryStatus.ERRORED
        entry.refresh_from_db()
        original_hash = entry.config_hash
        assert stub_run_eval.requests[0].inputs["input"] == "incorrect"
        assert identities[0] == (str(entry.id), original_hash)
        assert REVISION not in entry.output_metadata

        # Upgrade control: an entry written before the fix can still carry a
        # conversation-only revision, which is NOT proof about this span field.
        # Direct snapshot reads need the same CH context as the task engine;
        # these telemetry fixtures deliberately have no Postgres trace rows.
        from tracer.services.clickhouse.v2.eval_loader import eval_read_source

        with eval_read_source("clickhouse"):
            old_revision = eval_utils.session_conversation_revision(session)
        EvalLogger.objects.filter(id=entry.id).update(
            output_metadata={
                **entry.output_metadata,
                REVISION: old_revision,
            }
        )
        task.continuous_cursor = entry.updated_at + timedelta(seconds=1)
        span.span_attributes = {"answer": "corrected"}
        span.created_at = task.continuous_cursor + timedelta(seconds=1)
        seed_ch_spans([span], version_from_created_at=True)
        with eval_read_source("clickhouse"):
            assert eval_utils.session_conversation_revision(session) == old_revision
        assert _revisit(task, session) == (1, 0)
        stub_run_eval(failure=None)
        assert _run_one(task)[0] == EvalEntryStatus.COMPLETED
        entry.refresh_from_db()
        assert stub_run_eval.requests[-1].inputs["input"] == "corrected"
        assert entry.config_hash == original_hash
        assert identities[-1] == (str(entry.id), original_hash)
        assert REVISION not in entry.output_metadata

    def test_provider_context_failure_is_explicit_and_does_not_loop(
        self,
        conversation_task,
        stub_run_eval,
        stub_cost_log,
    ):
        task, session, _ = conversation_task
        trace = _turn(task.project, session, 1, message_size=70000)
        stub_run_eval(
            failure="The complete conversation exceeds the model context limit."
        )
        reconcile(task)
        status, entry = _run_one(task)
        assert status == EvalEntryStatus.ERRORED
        entry.refresh_from_db()
        assert entry.error
        assert "context limit" in entry.error_message
        assert entry.output_metadata[REVISION]
        assert (
            json.loads(stub_run_eval.requests[0].inputs["input"])[0]["input"]
            == trace.input
        )
        # Even beyond the overlap, eval-result candidacy is not new source content.
        task.continuous_cursor = timezone.now() + timedelta(minutes=10)
        assert _revisit(task, session) == (0, 0)
        _turn(task.project, session, 2)
        assert _revisit(task, session) == (1, 0)
