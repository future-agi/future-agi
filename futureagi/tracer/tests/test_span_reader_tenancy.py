"""Span reads that can return span content require the caller's tenant scope.

A trace, session, parent or span id is not unique across tenants: a replay,
re-import or shared provider account writes the same id into several projects
and organizations. Every ``CHSpanReader`` read keyed by such an id therefore
refuses to run without ``project_id``, ``project_ids`` or ``org_id``, keeps the
SQL a scoped caller already sent byte-for-byte, and never fills a row outside
that scope from ``trajectory_items``. The callers pass the scope they verified.

Offline: a recording fake stands in for the ClickHouse client.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2 import span_reader
from tracer.services.clickhouse.v2.span_reader import (
    _FINAL_SKIP_INDEX_SETTINGS,
    CHSpanReader,
    UnscopedSpanReadError,
    _active_select_sql,
)
from tracer.services.clickhouse.v2.trajectory_hydration import ENCODING_ITEMS_V1

pytestmark = pytest.mark.unit

FUTUREAGI = Path(__file__).resolve().parents[2]
OWN = "11111111-1111-4111-8111-111111111111"
FOREIGN = "99999999-9999-4999-8999-999999999999"
SCOPE_ID = "22222222-2222-4222-8222-222222222222"
TRACE = "33333333-3333-4333-8333-333333333333"


class _RecordingClient:
    """Records every query; answers with ``rows`` (and ``items`` for a
    ``trajectory_items`` read)."""

    def __init__(self, rows=(), items=()):
        self.calls: list[tuple[str, dict, dict]] = []
        self._rows = list(rows)
        self._items = list(items)

    @property
    def sql(self):
        return self.calls[-1][0] if self.calls else None

    def query(self, sql, parameters=None, settings=None):
        self.calls.append((sql, dict(parameters or {}), dict(settings or {})))
        rows = self._items if "FROM trajectory_items" in sql else self._rows
        return SimpleNamespace(result_rows=rows)


def _reader(client) -> CHSpanReader:
    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = client
    return reader


# Every span-keyed read that can return span content, called with no scope.
UNSCOPED_CALLS = {
    "get": lambda r, **kw: r.get("span-1", **kw),
    "list_by_trace": lambda r, **kw: r.list_by_trace(TRACE, **kw),
    "list_by_trace_ids": lambda r, **kw: r.list_by_trace_ids([TRACE], **kw),
    "first_span_by_type": lambda r, **kw: r.first_span_by_type(TRACE, "llm", **kw),
    "list_by_session": lambda r, **kw: r.list_by_session("session-1", **kw),
    "list_by_parent": lambda r, **kw: r.list_by_parent("parent-1", **kw),
    "roots_by_trace_ids": lambda r, **kw: r.roots_by_trace_ids([TRACE], **kw),
    "list_by_ids": lambda r, **kw: r.list_by_ids(["span-1"], **kw),
    "export_fields_by_ids": lambda r, **kw: r.export_fields_by_ids(
        ["span-1"], {"input"}, **kw
    ),
    "child_tree_spans_by_trace_ids": lambda r, **kw: r.child_tree_spans_by_trace_ids(
        [TRACE], **kw
    ),
    "list_root_spans_by_trace_ids": lambda r, **kw: r.list_root_spans_by_trace_ids(
        [TRACE], **kw
    ),
}


@pytest.mark.parametrize("method", sorted(UNSCOPED_CALLS))
@pytest.mark.parametrize(
    "scope",
    [{}, {"project_id": None}, {"project_id": ""}],
    ids=["none", "None", "empty"],
)
def test_unscoped_trace_reads_raise(method, scope):
    client = _RecordingClient()
    with pytest.raises(UnscopedSpanReadError, match=method):
        UNSCOPED_CALLS[method](_reader(client), **scope)
    assert client.calls == []  # refused before any SQL


@pytest.mark.parametrize(
    "method",
    [
        "get",
        "list_by_trace",
        "list_by_trace_ids",
        "first_span_by_type",
        "list_by_parent",
        "child_tree_spans_by_trace_ids",
    ],
)
def test_empty_project_ids_scope_reads_nothing(method):
    """An empty project set (a tenant with no projects) is a scope that
    admits no row: nothing is read and nothing comes back."""
    client = _RecordingClient()
    result = UNSCOPED_CALLS[method](_reader(client), project_ids=[])
    assert not result
    assert client.calls == []


@pytest.mark.parametrize(
    "method",
    ["list_by_trace", "list_by_trace_ids", "first_span_by_type", "list_by_parent"],
)
def test_project_ids_scope_binds_an_in_predicate(method):
    client = _RecordingClient()
    UNSCOPED_CALLS[method](_reader(client), project_ids=[OWN, FOREIGN, OWN])
    sql, params, _ = client.calls[0]
    assert "project_id IN %(pids)s" in sql
    assert params["pids"] == (OWN, FOREIGN)


# The SQL a caller that already passed ``project_id`` sent before this change.
def _dev_sql():
    heavy, lean = _active_select_sql(True), _active_select_sql(False)
    return {
        "get": (
            lambda r: r.get("span-1", project_id=OWN),
            f"SELECT {heavy} FROM spans FINAL "
            "WHERE id = %(span_id)s AND project_id = %(pid)s LIMIT 1",
            {"span_id": "span-1", "pid": OWN},
        ),
        "list_by_trace": (
            lambda r: r.list_by_trace(TRACE, project_id=OWN),
            f"SELECT {heavy} FROM spans FINAL "
            "WHERE trace_id = %(trace_id)s AND project_id = %(pid)s "
            "ORDER BY start_time, id",
            {"trace_id": TRACE, "pid": OWN},
        ),
        "list_by_trace_ids": (
            lambda r: r.list_by_trace_ids([TRACE], project_id=OWN),
            f"SELECT {heavy} FROM spans FINAL "
            "WHERE trace_id IN %(trace_ids)s AND project_id = %(pid)s "
            "ORDER BY trace_id, start_time, id",
            {"trace_ids": (TRACE,), "pid": OWN},
        ),
        "first_span_by_type": (
            lambda r: r.first_span_by_type(TRACE, "LLM", project_id=OWN),
            f"SELECT {lean} FROM spans FINAL "
            "WHERE trace_id = %(trace_id)s AND lower(observation_type) = %(otype)s "
            "AND project_id = %(pid)s ORDER BY start_time, id LIMIT 1",
            {"trace_id": TRACE, "otype": "llm", "pid": OWN},
        ),
        "roots_by_trace_ids": (
            lambda r: r.roots_by_trace_ids([TRACE], project_id=OWN),
            f"SELECT {lean} FROM spans FINAL "
            "WHERE trace_id IN %(trace_ids)s "
            "AND (parent_span_id IS NULL OR parent_span_id = '') "
            "AND project_id = %(pid)s ORDER BY trace_id, start_time, id",
            {"trace_ids": (TRACE,), "pid": OWN},
        ),
        "list_by_ids": (
            lambda r: r.list_by_ids(["span-1"], project_id=OWN),
            f"SELECT {heavy} FROM spans FINAL "
            "WHERE id IN %(ids)s AND project_id = %(pid)s ORDER BY id",
            {"ids": ("span-1",), "pid": OWN},
        ),
        "list_root_spans_by_trace_ids": (
            lambda r: r.list_root_spans_by_trace_ids([TRACE], project_id=OWN),
            f"SELECT {heavy} FROM spans FINAL "
            "WHERE trace_id IN %(tids)s AND parent_span_id = '' "
            "AND spans.project_id = %(pid)s "
            "ORDER BY trace_id, start_time, id LIMIT 1 BY trace_id",
            {"tids": (TRACE,), "pid": OWN},
        ),
    }


@pytest.mark.parametrize(
    "method",
    [
        "get",
        "list_by_trace",
        "list_by_trace_ids",
        "first_span_by_type",
        "roots_by_trace_ids",
        "list_by_ids",
        "list_root_spans_by_trace_ids",
    ],
)
def test_scoped_read_sql_is_unchanged(method, settings):
    """A caller that already passed its project sends the same statement,
    parameters and settings as before; the scope check adds no SQL."""
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = False
    call, sql, params = _dev_sql()[method]
    client = _RecordingClient()
    call(_reader(client))
    assert len(client.calls) == 1
    sent_sql, sent_params, sent_settings = client.calls[0]
    assert sent_sql == sql
    assert sent_params == params
    assert sent_settings == _FINAL_SKIP_INDEX_SETTINGS


def _items_v1_row(project_id, *, span_id, input_hash):
    """A full positional spans row (flag on) for an ``items_v1`` span whose
    input is one message held in ``trajectory_items``."""
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    row = dict.fromkeys(span_reader._TRAJECTORY_DATA_KEYS, "")
    row.update(
        {
            "id": span_id,
            "project_id": project_id,
            "trace_id": TRACE,
            "start_time": now,
            "end_time": now,
            "latency_ms": 1,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "cost": 0.0,
            "org_id": None,
            "input": "preview...",
            "output": "ok",
            "attributes_extra": "{}",
            "attrs_string": {},
            "attrs_number": {},
            "attrs_bool": {},
            "is_deleted": 0,
            "created_at": now,
            "input_item_hashes": [input_hash],
            "output_item_hashes": [],
            "tool_def_hashes": [],
            "item_scope_id": SCOPE_ID,
            "payload_encoding": ENCODING_ITEMS_V1,
            "payload_envelope": json.dumps({"v": 1, "in": {"f": "msg_array"}}),
        }
    )
    return tuple(row[k] for k in span_reader._TRAJECTORY_DATA_KEYS)


def test_hydration_never_fills_a_row_outside_the_read_scope(settings, caplog):
    """Defence in depth: if a scoped read ever returned another project's
    row, that row keeps its stored preview and is logged; only the caller's
    project is looked up in ``trajectory_items``."""
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    own_hash, foreign_hash = "a" * 32, "b" * 32
    client = _RecordingClient(
        rows=[
            _items_v1_row(OWN, span_id="own", input_hash=own_hash),
            _items_v1_row(FOREIGN, span_id="foreign", input_hash=foreign_hash),
        ],
        items=[(SCOPE_ID, own_hash, '{"role":"user","content":"own question"}')],
    )
    with caplog.at_level(logging.ERROR, logger=span_reader.__name__):
        spans = _reader(client).list_by_trace(TRACE, project_id=OWN)

    assert [s.id for s in spans] == ["own", "foreign"]  # order and length kept
    assert json.loads(spans[0].input) == [{"role": "user", "content": "own question"}]
    assert spans[1].input == "preview..."
    item_reads = [p for sql, p, _ in client.calls if "FROM trajectory_items" in sql]
    assert [p["traj_project_id"] for p in item_reads] == [OWN]
    assert "trajectory_hydration_scope_violation" in caplog.text
    assert "span_id=foreign" in caplog.text


def test_child_tree_rows_outside_the_scope_are_not_hydrated(settings, caplog):
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    aliases = CHSpanReader._CHILD_TREE_TRAJ_ALIASES

    def row(project_id, span_id, input_hash):
        values = dict.fromkeys(aliases, "")
        values.update(
            {
                "id": span_id,
                "trace_id": TRACE,
                "input": "preview...",
                "attributes_extra": "{}",
                "start_time": "2026-11-20 12:00:00",
                "project_id": project_id,
                "input_item_hashes": [input_hash],
                "output_item_hashes": [],
                "tool_def_hashes": [],
                "item_scope_id": SCOPE_ID,
                "payload_encoding": ENCODING_ITEMS_V1,
                "payload_envelope": json.dumps({"v": 1, "in": {"f": "msg_array"}}),
            }
        )
        return tuple(values[a] for a in aliases)

    client = _RecordingClient(
        rows=[row(OWN, "own", "a" * 32), row(FOREIGN, "foreign", "b" * 32)],
        items=[(SCOPE_ID, "a" * 32, '{"role":"user","content":"own question"}')],
    )
    with caplog.at_level(logging.ERROR, logger=span_reader.__name__):
        tree = _reader(client).child_tree_spans_by_trace_ids([TRACE], project_id=OWN)

    by_id = {d["id"]: d for d in tree[TRACE]}
    assert json.loads(by_id["own"]["input"]) == [
        {"role": "user", "content": "own question"}
    ]
    assert by_id["foreign"]["input"] == "preview..."
    item_reads = [p for sql, p, _ in client.calls if "FROM trajectory_items" in sql]
    assert [p["traj_project_id"] for p in item_reads] == [OWN]
    assert "trajectory_hydration_scope_violation" in caplog.text


# ── every call site passes a scope ─────────────────────────────────────────────

SCOPED_METHODS = frozenset(UNSCOPED_CALLS) - {"get"}
SCOPE_KWARGS = frozenset({"project_id", "project_ids", "org_id"})


def _is_reader(node: ast.AST) -> bool:
    """A ``.get`` receiver that is a span reader: a name such as ``reader`` or
    ``self._reader``, or a ``get_reader()`` call (not a dict a reader method
    returned, such as ``reader.scope_by_ids(...).get``)."""
    if isinstance(node, ast.Name):
        return "reader" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "reader" in node.attr.lower()
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name.endswith("get_reader")
    return False


_SKIPPED_DIRS = {"tests", "migrations", ".venv", "node_modules", "__pycache__"}


def _source_files():
    for root, dirs, files in os.walk(FUTUREAGI):
        dirs[:] = [d for d in dirs if d not in _SKIPPED_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(".py") and not name.startswith("test_"):
                yield Path(root) / name


def _span_read_calls():
    """``(where, call)`` for every call of a scoped read in non-test code. A
    ``.get`` counts when its receiver is a reader; the other method names are
    specific to the reader."""
    for path in sorted(_source_files()):
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            ):
                continue
            name = node.func.attr
            if name in SCOPED_METHODS or (
                name == "get" and _is_reader(node.func.value)
            ):
                yield f"{path.relative_to(FUTUREAGI)}:{node.lineno}", node


def test_all_call_sites_pass_scope():
    """Every production call of a scoped read names its scope. A call with no
    scope keyword, or with a literal ``None`` as its only scope, fails here
    before it can raise ``UnscopedSpanReadError`` in production. (A value that
    can be None at run time still raises before any SQL.)"""
    calls = list(_span_read_calls())
    # The scan really found the call sites (guards a scan that matches nothing).
    assert len(calls) >= 40, len(calls)
    missing = []
    for where, node in calls:
        if any(k.arg is None for k in node.keywords):  # **kwargs forwarding
            continue
        scope = [
            k.value
            for k in node.keywords
            if k.arg in SCOPE_KWARGS
            and not (isinstance(k.value, ast.Constant) and k.value.value is None)
        ]
        if not scope:
            missing.append(f"{where} {ast.unparse(node.func)}")
    assert not missing, "span reads without a tenant scope:\n" + "\n".join(missing)


# ── callers fixed in this change ───────────────────────────────────────────────


class _ScopeRecordingReader:
    """Context-manager reader recording the keyword scope of each call."""

    def __init__(self, result=()):
        self.calls: list[tuple[str, tuple, dict]] = []
        self._result = list(result)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def close(self):
        return None

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return list(self._result)

        return _call


def test_eval_trace_context_without_project_skips_the_read(monkeypatch):
    """``build_trace_context`` reads spans only in the trace's project; with
    no project it returns empty context instead of an unscoped read."""
    from tracer.utils.eval import build_trace_context

    reader = _ScopeRecordingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    context = build_trace_context(
        SimpleNamespace(id="trace-1", project_id=None, created_at=None)
    )
    assert reader.calls == []
    assert context["spans"] == []


@pytest.mark.parametrize("forced", [False, True], ids=["fallback", "forced"])
def test_eval_span_load_without_project_refuses_before_reading(monkeypatch, forced):
    """The eval loader refuses before its try block, so a missing scope is
    neither retried nor turned into a Postgres read."""
    from tracer.services.clickhouse.v2 import eval_loader

    def _no_reader():
        raise AssertionError("an unscoped eval load reached the reader")

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", _no_reader)
    monkeypatch.setattr(eval_loader, "_forced_clickhouse", lambda: forced)
    monkeypatch.setattr(eval_loader, "_read_source", lambda: "clickhouse")
    with pytest.raises(UnscopedSpanReadError):
        eval_loader._hybrid_load_from_ch("span-1", (), project_id=None)
    with pytest.raises(UnscopedSpanReadError):
        eval_loader.filter_observation_spans_by_trace("trace-1", project_id=None)


def test_legacy_span_eval_reads_in_the_given_or_config_project():
    from tracer.utils.eval import _legacy_eval_span_project_id

    config = SimpleNamespace(project_id=OWN)
    assert _legacy_eval_span_project_id(FOREIGN, config) == FOREIGN
    assert _legacy_eval_span_project_id(None, config) == OWN
    assert _legacy_eval_span_project_id("", config) == OWN


def test_eval_trace_read_without_trace_project_uses_config_project(monkeypatch):
    """The tag-triggered runner reads its span in the Postgres span row's
    project, or in the project of the version's eval configs when that row
    cannot be read; it never reads unscoped."""
    from django.db import DatabaseError

    from tracer.utils import eval as eval_module

    class _Chain:
        def __init__(self, value=None, error=None):
            self._value, self._error = value, error

        def using(self, *_a):
            return self

        def filter(self, **_kw):
            return self

        def values_list(self, *_a, **_kw):
            return self

        def first(self):
            if self._error:
                raise self._error
            return self._value

    monkeypatch.setattr(
        eval_module.ObservationSpan, "no_workspace_objects", _Chain(value=OWN)
    )
    monkeypatch.setattr(eval_module.CustomEvalConfig, "objects", _Chain(value=FOREIGN))
    tags = [{"type": "OBSERVATION_SPAN_TYPE", "custom_eval_config_id": "cfg"}]
    assert eval_module._runner_span_project_id("span-1", tags) == OWN

    monkeypatch.setattr(
        eval_module.ObservationSpan,
        "no_workspace_objects",
        _Chain(error=DatabaseError("legacy table gone")),
    )
    assert eval_module._runner_span_project_id("span-1", json.dumps(tags)) == FOREIGN

    monkeypatch.setattr(eval_module.CustomEvalConfig, "objects", _Chain(value=None))
    with pytest.raises(eval_module.ObservationSpan.DoesNotExist):
        eval_module._runner_span_project_id("span-1", tags)


def test_voice_conversation_span_is_read_in_the_callers_projects(monkeypatch):
    from simulate.utils import session_comparison

    reader = _ScopeRecordingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    with pytest.raises(ValueError):
        session_comparison.fetch_voice_conversation_span(TRACE)
    assert reader.calls == []

    with pytest.raises(ValueError, match="No conversation span"):
        session_comparison.fetch_voice_conversation_span(TRACE, project_ids=[OWN])
    assert reader.calls == [("list_by_trace", (TRACE,), {"project_ids": [OWN]})]


def test_trace_to_graph_reads_in_the_trace_project(monkeypatch):
    from agent_playground.services import trace_to_graph

    reader = _ScopeRecordingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    assert trace_to_graph._legacy_spans_from_clickhouse(TRACE, project_id=OWN) == []
    assert reader.calls == [("list_by_trace", (TRACE,), {"project_id": OWN})]


def test_error_analysis_span_reads_are_scoped(monkeypatch):
    from tracer.queries import error_analysis

    reader = _ScopeRecordingReader()
    monkeypatch.setattr(error_analysis, "get_reader", lambda: reader)
    db = error_analysis.TraceErrorAnalysisDB()

    db.get_all_spans(TRACE, project_id=OWN)
    assert reader.calls[-1] == ("list_by_trace", (TRACE,), {"project_id": OWN})

    db.get_error_spans_for_trace(SimpleNamespace(id=TRACE, project_id=OWN))
    assert reader.calls[-1] == ("list_by_trace", (TRACE,), {"project_id": OWN})

    calls_before = len(reader.calls)
    assert db.get_error_spans_for_trace(SimpleNamespace(id=TRACE)) == []
    assert len(reader.calls) == calls_before


class _TraceQuery:
    """The slice of a Trace queryset the replay helpers use."""

    def __init__(self, rows):
        self._rows = rows

    def values_list(self, *fields, **_kw):
        assert fields == ("id", "project_id")
        return self

    def __getitem__(self, key):
        return self._rows[key]

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


def test_replay_voice_reads_are_pinned_to_the_traces_projects(monkeypatch):
    from tracer.utils import replay_session

    reader = _ScopeRecordingReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    query = _TraceQuery([("t1", OWN), ("t2", OWN)])

    assert replay_session._is_voice_trace_query(query) is False
    assert replay_session._load_voice_conversation_spans(query) == []
    assert replay_session._get_first_voice_span_raw_log(query) is None
    assert reader.calls == [
        ("list_by_trace_ids", (["t1", "t2"],), {"project_ids": [OWN]}),
        ("list_by_trace_ids", (["t1", "t2"],), {"project_ids": [OWN]}),
        ("list_by_trace", ("t1",), {"project_id": OWN}),
    ]


class _ChildTreeReader:
    """Context-manager reader for the dataset child-tree export."""

    def __init__(self):
        self.tree_calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def trace_ids_for_span_ids(self, span_ids, *, project_id=None):
        return {str(s): TRACE for s in span_ids}

    def child_tree_spans_by_trace_ids(self, trace_ids, **scope):
        self.tree_calls.append(scope)
        return {}


@pytest.mark.django_db
def test_org_wide_dataset_child_tree_reads_in_the_orgs_projects(
    monkeypatch, organization, project
):
    """An org-wide export (no project) reads child trees in the
    organization's projects, not in every tenant's copy of the trace."""
    from tracer.tasks import dataset as dataset_tasks

    reader = _ChildTreeReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    from tracer.models.project import Project

    foreign_org_project = FOREIGN
    dataset_tasks._serialize_span_trees_batch(["span-1"], org_id=str(organization.id))
    (call,) = reader.tree_calls
    org_projects = {
        str(pid)
        for pid in Project.with_simulation.filter(
            organization=organization
        ).values_list("id", flat=True)
    }
    assert call["project_id"] is None
    assert str(project.id) in call["project_ids"]
    assert set(call["project_ids"]) == org_projects
    assert foreign_org_project not in call["project_ids"]

    dataset_tasks._serialize_span_trees_batch(["span-1"], project_id=str(project.id))
    assert reader.tree_calls[-1] == {"project_id": str(project.id), "project_ids": None}
