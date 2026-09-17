"""Pure regressions for transient ClickHouse eval-input reads."""

import importlib
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.eval_loader import (
    EvalTelemetryReadError,
    _hybrid_load_from_ch,
    eval_read_source,
    filter_observation_spans_by_trace,
    get_trace,
    get_trace_session,
)


class _FailingReader:
    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def get(self, _span_id, *, project_id=None):
        raise TimeoutError(f"CH timeout for {project_id}")

    def get_trace_row(self, _trace_id, *, project_id=None):
        raise TimeoutError(f"CH trace timeout for {project_id}")

    def list_by_trace(self, _trace_id, *, include_heavy, project_id=None):
        raise TimeoutError(f"CH child-span timeout for {project_id}")


class _HeavyFailingReader(_FailingReader):
    def list_by_trace(self, _trace_id, *, include_heavy, project_id=None):
        return [SimpleNamespace(id="span-1")]

    def list_by_ids(self, _span_ids, *, include_heavy, project_id=None):
        raise TimeoutError(f"CH heavy-span timeout for {project_id}")


class _MissingReader(_FailingReader):
    def get(self, _span_id, *, project_id=None):
        return None

    def get_trace_row(self, _trace_id, *, project_id=None):
        return None


class _SessionReader:
    def __init__(self, *, trace_ids=None, fail_discovery=False, fail_ordering=False):
        self.trace_ids = trace_ids or []
        self.fail_discovery = fail_discovery
        self.fail_ordering = fail_ordering
        self.closed = False
        self.discovery_args = None
        self.ordering_args = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def session_trace_ids(self, project_id, session_id):
        self.discovery_args = (project_id, session_id)
        if self.fail_discovery:
            raise TimeoutError("CH session trace discovery timeout")
        return self.trace_ids

    def per_trace_root_span_start_times(self, trace_ids, project_ids=None):
        self.ordering_args = (trace_ids, project_ids)
        if self.fail_ordering:
            raise TimeoutError("CH session trace ordering timeout")
        return {}


def test_forced_span_read_preserves_transient_failure_and_closes_reader(monkeypatch):
    reader = _FailingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    with eval_read_source("clickhouse"):
        with pytest.raises(EvalTelemetryReadError) as caught:
            _hybrid_load_from_ch("span-1", (), project_id="project-1")

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert reader.closed is True


def test_forced_trace_read_preserves_transient_failure_and_closes_reader(monkeypatch):
    reader = _FailingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    with eval_read_source("clickhouse"):
        with pytest.raises(EvalTelemetryReadError) as caught:
            get_trace("trace-1", project_id="project-1")

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert reader.closed is True


def test_forced_child_span_read_preserves_transient_failure_and_closes_reader(
    monkeypatch,
):
    reader = _FailingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    with eval_read_source("clickhouse"):
        with pytest.raises(EvalTelemetryReadError) as caught:
            filter_observation_spans_by_trace("trace-1", project_id="project-1")

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert reader.closed is True


def test_forced_heavy_child_span_read_is_retryable_and_closes_reader(monkeypatch):
    reader = _HeavyFailingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    with eval_read_source("clickhouse"):
        with pytest.raises(EvalTelemetryReadError) as caught:
            filter_observation_spans_by_trace(
                "trace-1",
                project_id="project-1",
                heavy_span_ids={"span-1"},
            )

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert reader.closed is True


def test_forced_session_read_preserves_transient_failure(monkeypatch):
    def _timeout(*_args, **_kwargs):
        raise TimeoutError("CH session timeout")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields",
        _timeout,
    )

    with eval_read_source("clickhouse"):
        with pytest.raises(EvalTelemetryReadError) as caught:
            get_trace_session("session-1", project=SimpleNamespace(id="project-1"))

    assert isinstance(caught.value.__cause__, TimeoutError)


def test_session_trace_discovery_failure_is_retryable_and_closes_reader(monkeypatch):
    from tracer.utils.eval import _session_traces_ch

    reader = _SessionReader(fail_discovery=True)
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    with pytest.raises(EvalTelemetryReadError, match="session traces") as caught:
        _session_traces_ch(SimpleNamespace(id="session-1", project_id="project-1"))

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert reader.discovery_args == ("project-1", "session-1")
    assert reader.closed is True


def test_session_trace_ordering_failure_is_retryable_scoped_and_closes_readers(
    monkeypatch,
):
    from tracer.utils.eval import _session_traces_ch

    discovery_reader = _SessionReader(trace_ids=["trace-1"])
    ordering_reader = _SessionReader(fail_ordering=True)
    readers = iter([discovery_reader, ordering_reader])
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: next(readers)
    )

    with pytest.raises(EvalTelemetryReadError, match="trace ordering") as caught:
        _session_traces_ch(SimpleNamespace(id="session-1", project_id="project-1"))

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert ordering_reader.ordering_args == (["trace-1"], ["project-1"])
    assert discovery_reader.closed is True
    assert ordering_reader.closed is True


def test_direct_span_eval_reload_preserves_transient_failure_and_project_scope(
    monkeypatch,
):
    from tracer.utils.eval import OBSERVE, _execute_evaluation

    calls = []

    def _fail_reload(span_id, **kwargs):
        calls.append((span_id, kwargs))
        raise EvalTelemetryReadError("transient")

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.eval_loader.get_observation_span",
        _fail_reload,
    )

    with pytest.raises(EvalTelemetryReadError, match="transient"):
        _execute_evaluation(
            observation_span_id="span-1",
            custom_eval_config_id="config-1",
            eval_task_id="task-1",
            type=OBSERVE,
            run_params={},
            project_id="project-1",
        )

    assert calls == [
        (
            "span-1",
            {
                "select_related": (
                    "project",
                    "project__organization",
                    "project__workspace",
                ),
                "project_id": "project-1",
            },
        )
    ]


def test_run_target_reuses_single_task_scoped_span_for_composite(monkeypatch):
    run_entry_module = importlib.import_module("tracer.services.eval_tasks.run_entry")
    eval_module = importlib.import_module("tracer.utils.eval")
    from tracer.models.observation_span import EvalTargetType

    project = SimpleNamespace(id="task-project")
    monkeypatch.setattr(
        run_entry_module,
        "writing_onto_entry",
        lambda *_args, **_kwargs: nullcontext(),
    )

    span = SimpleNamespace(id="span-1")
    span_calls = []

    def _load_span(span_id, **kwargs):
        span_calls.append((span_id, kwargs))
        if len(span_calls) > 1:
            raise EvalTelemetryReadError("second read must not happen")
        return span

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.eval_loader.get_observation_span",
        _load_span,
    )
    monkeypatch.setattr(eval_module, "_process_mapping", lambda *_args: {})

    composite_calls = []

    def _composite(**kwargs):
        composite_calls.append(kwargs)
        return {"value": 1}

    monkeypatch.setattr(eval_module, "_execute_composite_on_span", _composite)

    template = SimpleNamespace(id="template-1", template_type="composite", config={})
    config = SimpleNamespace(
        id="config-1",
        eval_template_id=template.id,
        eval_template=template,
        mapping={},
    )
    monkeypatch.setattr(
        eval_module.CustomEvalConfig.objects,
        "get",
        lambda **_kwargs: config,
    )
    entry = SimpleNamespace(
        id="entry-1",
        target_type=EvalTargetType.SPAN,
        observation_span_id="span-1",
        eval_task_id="task-1",
        output_metadata={},
    )

    run_entry_module._run_for_target(entry, config, task_project=project)

    assert len(span_calls) == 1
    assert span_calls[0][1]["project_id"] == "task-project"
    assert composite_calls[0]["observation_span"] is span
    assert composite_calls[0]["project_id"] == "task-project"


def test_successful_reads_with_absent_rows_remain_does_not_exist(monkeypatch):
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace
    from tracer.models.trace_session import TraceSession

    reader = _MissingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields",
        lambda *_args, **_kwargs: {},
    )

    with eval_read_source("clickhouse"):
        with pytest.raises(ObservationSpan.DoesNotExist, match="project-1"):
            _hybrid_load_from_ch("span-1", (), project_id="project-1")
        with pytest.raises(Trace.DoesNotExist, match="project-1"):
            get_trace("trace-1", project_id="project-1")
        with pytest.raises(TraceSession.DoesNotExist, match="project-1"):
            get_trace_session("session-1", project=SimpleNamespace(id="project-1"))


def test_run_entry_does_not_terminalize_transient_span_read(monkeypatch):
    run_entry_module = importlib.import_module("tracer.services.eval_tasks.run_entry")

    fresh = SimpleNamespace(
        id="entry-1",
        custom_eval_config_id="config-1",
        eval_task_id="task-1",
    )
    config = SimpleNamespace(id="config-1")
    filtered = SimpleNamespace(first=lambda: fresh)
    monkeypatch.setattr(
        run_entry_module,
        "EvalLogger",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: filtered)),
    )
    configs = SimpleNamespace(get=lambda **_kwargs: config)
    monkeypatch.setattr(
        run_entry_module,
        "CustomEvalConfig",
        SimpleNamespace(objects=SimpleNamespace(select_related=lambda *_args: configs)),
    )
    monkeypatch.setattr(run_entry_module, "resolved_config_hash", lambda _cfg: "hash")
    task_query = SimpleNamespace(
        get=lambda **_kwargs: SimpleNamespace(project=SimpleNamespace(id="project-1"))
    )
    monkeypatch.setattr(
        run_entry_module,
        "EvalTask",
        SimpleNamespace(
            objects=SimpleNamespace(select_related=lambda *_args: task_query)
        ),
    )
    monkeypatch.setattr(
        run_entry_module,
        "_run_for_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EvalTelemetryReadError("transient")
        ),
    )
    terminal_writes = []
    monkeypatch.setattr(
        run_entry_module,
        "mark_terminal",
        lambda *_args, **_kwargs: terminal_writes.append((_args, _kwargs)),
    )

    with pytest.raises(EvalTelemetryReadError, match="transient"):
        run_entry_module.run_entry(SimpleNamespace(id="entry-1"))

    assert terminal_writes == []


def test_run_entry_does_not_terminalize_transient_task_project_lookup(monkeypatch):
    from django.db import OperationalError

    run_entry_module = importlib.import_module("tracer.services.eval_tasks.run_entry")

    fresh = SimpleNamespace(
        id="entry-1",
        custom_eval_config_id="config-1",
        eval_task_id="task-1",
    )
    filtered = SimpleNamespace(first=lambda: fresh)
    monkeypatch.setattr(
        run_entry_module,
        "EvalLogger",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **_kwargs: filtered)),
    )
    config_query = SimpleNamespace(get=lambda **_kwargs: SimpleNamespace(id="config-1"))
    monkeypatch.setattr(
        run_entry_module,
        "CustomEvalConfig",
        SimpleNamespace(
            objects=SimpleNamespace(select_related=lambda *_args: config_query)
        ),
    )
    task_query = SimpleNamespace(
        get=lambda **_kwargs: (_ for _ in ()).throw(OperationalError("PG timeout"))
    )
    monkeypatch.setattr(
        run_entry_module,
        "EvalTask",
        SimpleNamespace(
            objects=SimpleNamespace(select_related=lambda *_args: task_query)
        ),
    )
    terminal_writes = []
    monkeypatch.setattr(
        run_entry_module,
        "mark_terminal",
        lambda *_args, **_kwargs: terminal_writes.append((_args, _kwargs)),
    )

    with pytest.raises(OperationalError, match="PG timeout"):
        run_entry_module.run_entry(SimpleNamespace(id="entry-1"))

    assert terminal_writes == []
