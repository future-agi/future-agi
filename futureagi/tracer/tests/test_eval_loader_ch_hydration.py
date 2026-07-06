"""eval_loader CH hydration: loaders build Django objects from ClickHouse, a
CH-hydrated span resolves .trace without a PG hit and never writes PG on save,
and the un-forced (legacy) path still reads Postgres."""

import uuid
from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
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
        assert ObservationSpan.objects.count() == 0

    def test_ch_hydrated_span_save_does_not_insert_pg(self, project):
        trace = Trace.objects.create(project=project, name="t")
        span = _ch_only_span(project, trace)
        with eval_read_source("clickhouse"):
            obj = get_observation_span(span.id)
        obj.eval_status = "COMPLETED"
        obj.save()  # bound no-op — must not INSERT into PG
        assert ObservationSpan.objects.count() == 0

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
        assert Trace.objects.count() == 0  # came from CH, not PG

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
