"""eval_loader CH hydration: loaders build Django objects from ClickHouse, a
CH-hydrated span resolves .trace without a PG hit and never writes PG on save,
and the un-forced (legacy) path still reads Postgres."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.clickhouse.v2 import eval_loader
from tracer.services.clickhouse.v2.eval_loader import (
    eval_read_source,
    get_observation_span,
    get_trace,
    get_trace_session,
)
from tracer.tests._ch_seed import (
    seed_ch_span,
    seed_ch_trace,
    seed_ch_trace_sessions,
)


def _ch_only_span(project, trace, *, parent_span_id=""):
    span = ObservationSpan(
        id=f"ch-{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        parent_span_id=parent_span_id,
        name="s",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=2),
        end_time=timezone.now(),
        input={"k": "v"},
        output={"o": "p"},
        status="OK",
    )
    seed_ch_span(span)
    return span


def test_hybrid_point_load_closes_reader(monkeypatch):
    """Every task entry releases its point-reader, including successful reads."""
    ch_row = object()

    class Reader:
        entered = False
        closed = False

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, *_exc):
            self.closed = True

        def get(self, span_id, *, project_id=None):
            assert span_id == "span-1"
            assert project_id == "project-1"
            return ch_row

    reader = Reader()
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader",
        lambda: reader,
    )
    monkeypatch.setattr(eval_loader, "_construct_from_chspan", lambda row: row)

    result = eval_loader._hybrid_load_from_ch(
        "span-1",
        (),
        project_id="project-1",
    )

    assert result is ch_row
    assert reader.entered is True
    assert reader.closed is True


def test_trace_load_closes_reader_after_lean_and_heavy_passes(monkeypatch):
    """Trace evals release their reader after the optional heavy second pass."""
    lean_a = SimpleNamespace(id="span-a")
    lean_b = SimpleNamespace(id="span-b")
    heavy_b = SimpleNamespace(id="span-b")

    class Reader:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.closed = True

        def list_by_trace(self, trace_id, *, include_heavy, project_id):
            assert trace_id == "trace-1"
            assert include_heavy is False
            assert project_id == "project-1"
            return [lean_a, lean_b]

        def list_by_ids(self, span_ids, *, include_heavy, project_id):
            assert span_ids == ["span-b"]
            assert include_heavy is True
            assert project_id == "project-1"
            return [heavy_b]

    reader = Reader()
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader",
        lambda: reader,
    )
    monkeypatch.setattr(eval_loader, "_construct_from_chspan", lambda row: row)

    with eval_read_source("clickhouse"):
        result = eval_loader.filter_observation_spans_by_trace(
            "trace-1",
            project_id="project-1",
            heavy_span_ids={"span-b"},
        )

    assert result == [lean_a, heavy_b]
    assert reader.closed is True


def test_production_rejects_postgres_eval_telemetry_source():
    with override_settings(TESTING=False, EVAL_SPAN_READ_SOURCE="postgres"):
        with pytest.raises(RuntimeError, match="must be read from ClickHouse"):
            eval_loader._read_source()


def test_unforced_clickhouse_miss_never_falls_back_to_postgres(monkeypatch):
    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def get(self, span_id, *, project_id=None):
            return None

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader",
        lambda: Reader(),
    )

    with override_settings(TESTING=False, EVAL_SPAN_READ_SOURCE="clickhouse"):
        with pytest.raises(ObservationSpan.DoesNotExist, match="not in ClickHouse"):
            get_observation_span("missing-span", project_id="project-1")


def test_session_context_summarizes_ch_only_traces(monkeypatch):
    """Net-new sessions do not need any PostgreSQL Trace rows for context."""
    from tracer.utils.eval import build_session_context

    now = timezone.now()
    trace_a = str(uuid.uuid4())
    trace_b = str(uuid.uuid4())
    spans = [
        SimpleNamespace(
            id="span-a",
            trace_id=trace_a,
            parent_span_id="",
            name="root-a",
            trace_name="Trace A",
            observation_type="agent",
            status="OK",
            start_time=now - timedelta(seconds=3),
            end_time=now - timedelta(seconds=1),
            total_tokens=5,
            latency_ms=20,
            cost=0.1,
        ),
        SimpleNamespace(
            id="span-b",
            trace_id=trace_b,
            parent_span_id="",
            name="root-b",
            trace_name="Trace B",
            observation_type="agent",
            status="ERROR",
            start_time=now - timedelta(seconds=2),
            end_time=now,
            total_tokens=7,
            latency_ms=30,
            cost=0.2,
        ),
    ]

    class Reader:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.closed = True

        def list_by_session(self, session_id):
            assert session_id == "session-1"
            return spans

    reader = Reader()
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader",
        lambda: reader,
    )
    session = SimpleNamespace(
        id="session-1",
        name="Session",
        project_id=str(uuid.uuid4()),
        bookmarked=False,
        created_at=now - timedelta(minutes=1),
    )

    context = build_session_context(session)

    assert context["trace_count"] == 2
    assert {trace["id"] for trace in context["traces"]} == {trace_a, trace_b}
    assert context["total_spans"] == 2
    assert context["error_count"] == 1
    assert reader.closed is True


@pytest.mark.integration
@pytest.mark.django_db
class TestEvalLoaderChHydration:
    def test_get_observation_span_hydrates_trace_no_pg(self, project):
        trace = Trace.objects.create(project=project, name="t")
        span = _ch_only_span(project, trace)
        with eval_read_source("clickhouse"):
            obj = get_observation_span(span.id)
        assert str(obj.id) == span.id
        assert obj.input == {"k": "v"}
        assert str(obj.trace.id) == str(trace.id)  # resolved from CH, no PG span
        # CH-hydrated span must not be written to PG. Scope by id — other tests
        # can leave committed rows in the shared PG table, so a global count is
        # not isolation-safe.
        assert not ObservationSpan.objects.filter(id=span.id).exists()

    def test_ch_hydrated_span_save_does_not_insert_pg(self, project):
        trace = Trace.objects.create(project=project, name="t")
        span = _ch_only_span(project, trace)
        with eval_read_source("clickhouse"):
            obj = get_observation_span(span.id)
        obj.eval_status = "COMPLETED"
        obj.save()  # bound no-op — must not INSERT into PG
        assert not ObservationSpan.objects.filter(id=span.id).exists()

    def test_get_trace_hydrates_trace_level_fields_from_ch(self, project):
        # get_trace reads the CH `traces` table, so trace-level fields
        # (input/output/tags/metadata) come through — not just root-span fields.
        trace = Trace(
            id=uuid.uuid4(),
            project=project,
            name="t",
            input={"q": "hi"},
            output={"a": "yo"},
            tags=["x", "y"],
            metadata={"m": 1},
        )
        seed_ch_trace(trace)
        with eval_read_source("clickhouse"):
            t = get_trace(str(trace.id))
        assert str(t.id) == str(trace.id)
        assert t.name == "t"
        assert t.input == {"q": "hi"}
        assert t.output == {"a": "yo"}
        assert t.tags == ["x", "y"]
        assert t.metadata == {"m": 1}
        assert not Trace.objects.filter(id=trace.id).exists()  # from CH, not PG

    def test_get_trace_session_builds_vehicle(self, observe_project):
        session = TraceSession.objects.create(project=observe_project, name="sess-x")
        seed_ch_trace_sessions([session])
        with eval_read_source("clickhouse"):
            s = get_trace_session(str(session.id), project=observe_project)
        assert str(s.id) == str(session.id)
        assert s.name

    def test_legacy_pg_path_unchanged_without_force(self, project, monkeypatch):
        # No force context + postgres source → reads PG; a CH-only span misses.
        monkeypatch.setattr(
            settings, "EVAL_SPAN_READ_SOURCE", "postgres", raising=False
        )
        trace = Trace.objects.create(project=project, name="t")
        span = _ch_only_span(project, trace)
        with pytest.raises(ObservationSpan.DoesNotExist):
            get_observation_span(span.id)
