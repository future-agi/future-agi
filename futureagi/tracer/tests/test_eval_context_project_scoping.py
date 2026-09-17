"""Tenant scoping for optional eval data-injection context reads."""

from types import SimpleNamespace

from tracer.utils.eval import build_session_context, build_trace_context


class _ContextReader:
    def __init__(self, *, fail_session_discovery=False):
        self.fail_session_discovery = fail_session_discovery
        self.trace_call = None
        self.session_discovery_call = None
        self.session_span_call = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def list_by_trace(self, trace_id, *, project_id=None):
        self.trace_call = (trace_id, project_id)
        return []

    def session_trace_ids(self, project_id, session_id):
        self.session_discovery_call = (project_id, session_id)
        if self.fail_session_discovery:
            raise TimeoutError("optional session context unavailable")
        return ["trace-1"]

    def list_by_session(self, session_id, *, project_id=None):
        self.session_span_call = (session_id, project_id)
        return []


class _EmptyTraceQuery:
    def order_by(self, *_args):
        return self

    def __getitem__(self, _key):
        return []

    def count(self):
        return 0


def test_trace_context_scopes_span_read_to_trace_project(monkeypatch):
    reader = _ContextReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    context = build_trace_context(
        SimpleNamespace(id="trace-1", project_id="project-1", created_at=None)
    )

    assert reader.trace_call == ("trace-1", "project-1")
    assert context["id"] == "trace-1"
    assert context["spans"] == []


def test_session_context_scopes_ch_and_pg_reads_to_session_project(monkeypatch):
    from tracer.models.trace import Trace

    reader = _ContextReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    pg_calls = []

    def _filter(**kwargs):
        pg_calls.append(kwargs)
        return _EmptyTraceQuery()

    monkeypatch.setattr(Trace.objects, "filter", _filter)
    session = SimpleNamespace(
        id="session-1",
        project_id="project-1",
        name="session",
        bookmarked=False,
        created_at=None,
    )

    context = build_session_context(session)

    assert context is not None
    assert reader.session_discovery_call == ("project-1", "session-1")
    assert reader.session_span_call == ("session-1", "project-1")
    assert pg_calls == [
        {
            "project_id": "project-1",
            "id__in": ["trace-1"],
            "deleted": False,
        }
    ]


def test_session_context_keeps_optional_failure_semantics(monkeypatch):
    reader = _ContextReader(fail_session_discovery=True)
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    session = SimpleNamespace(
        id="session-1",
        project_id="project-1",
        name="session",
        bookmarked=False,
        created_at=None,
    )

    assert build_session_context(session) is None
