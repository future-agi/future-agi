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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2 import span_reader, trajectory_hydration
from tracer.services.clickhouse.v2.span_reader import (
    _FINAL_SKIP_INDEX_SETTINGS,
    CHSpanReader,
    UnscopedSpanReadError,
    _active_select_sql,
)
from tracer.services.clickhouse.v2.trajectory_hydration import (
    ENCODING_ITEMS_V1,
    payload_checksum,
)
from tracer.tests.trajectory_read_state_support import install_default_schema_probe

pytestmark = pytest.mark.unit

FUTUREAGI = Path(__file__).resolve().parents[2]
OWN = "11111111-1111-4111-8111-111111111111"
FOREIGN = "99999999-9999-4999-8999-999999999999"
SCOPE_ID = "22222222-2222-4222-8222-222222222222"
TRACE = "33333333-3333-4333-8333-333333333333"
OWN_MESSAGE = '{"role":"user","content":"own question"}'


@pytest.fixture(autouse=True)
def _scripted_read_state(monkeypatch):
    """The read state follows FI_TRAJECTORY_STORE_READ_ENABLED as before the
    schema probe: a scripted complete-040 schema with no items, so true is
    READY, false NOT_READY, and no probe statement reaches a fake client."""
    install_default_schema_probe(monkeypatch)
    yield
    trajectory_hydration.reset_read_state_cache()


def _one_message_envelope(content: str) -> str:
    """The v2 envelope of an input that is a one-message array whose message
    is ``content``, held in ``trajectory_items``."""
    original = f"[{content}]"
    return json.dumps(
        {
            "v": 2,
            "in": {
                "g": ["[", "]"],
                "r": "m",
                "il": len(original),
                "x": payload_checksum(original),
            },
        }
    )


OWN_ENVELOPE = _one_message_envelope(OWN_MESSAGE)


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

    def close(self):
        return None


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


@pytest.mark.parametrize("method", sorted(UNSCOPED_CALLS))
def test_empty_project_ids_scope_reads_nothing(method):
    """An empty project set (a tenant with no projects) is a scope that
    admits no row: nothing is read and nothing comes back."""
    client = _RecordingClient()
    result = UNSCOPED_CALLS[method](_reader(client), project_ids=[])
    assert not result
    assert client.calls == []


@pytest.mark.parametrize("method", sorted(set(UNSCOPED_CALLS) - {"get"}))
def test_project_ids_scope_binds_an_in_predicate(method):
    """Every scoped read takes the caller's projects (``project_ids``), so an
    org-wide caller can pin its projects instead of ``org_id``, which a
    collector row may carry as NULL."""
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
            "payload_envelope": OWN_ENVELOPE,
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
        items=[(SCOPE_ID, own_hash, OWN_MESSAGE)],
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
                "payload_envelope": OWN_ENVELOPE,
            }
        )
        return tuple(values[a] for a in aliases)

    client = _RecordingClient(
        rows=[row(OWN, "own", "a" * 32), row(FOREIGN, "foreign", "b" * 32)],
        items=[(SCOPE_ID, "a" * 32, OWN_MESSAGE)],
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


def test_org_scoped_dict_rows_from_another_org_are_not_hydrated(settings, caplog):
    """An org-only read checks each dict row's ``org_id`` before hydrating."""
    settings.FI_TRAJECTORY_STORE_READ_ENABLED = True
    own_org, foreign_org = OWN, FOREIGN

    def row(span_id, org_id, input_hash):
        return {
            "id": span_id,
            "project_id": SCOPE_ID,
            "org_id": org_id,
            "input": "preview...",
            "input_item_hashes": [input_hash],
            "output_item_hashes": [],
            "tool_def_hashes": [],
            "item_scope_id": SCOPE_ID,
            "payload_encoding": ENCODING_ITEMS_V1,
            "payload_envelope": OWN_ENVELOPE,
        }

    client = _RecordingClient(items=[(SCOPE_ID, "a" * 32, OWN_MESSAGE)])
    rows = [row("own", own_org, "a" * 32), row("foreign", foreign_org, "b" * 32)]
    with caplog.at_level(logging.ERROR, logger=span_reader.__name__):
        _reader(client)._hydrate_dict_rows(
            rows, scope=span_reader._ReadScope.of(org_id=own_org)
        )

    assert json.loads(rows[0]["input"]) == [{"role": "user", "content": "own question"}]
    assert rows[1]["input"] == "preview..."
    item_reads = [p for sql, p, _ in client.calls if "FROM trajectory_items" in sql]
    assert [set(p["traj_pairs"]) for p in item_reads] == [{(SCOPE_ID, "a" * 32)}]
    assert "span_id=foreign" in caplog.text


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


def test_runner_span_read_uses_span_row_or_version_config_project(monkeypatch):
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
        self.trace_map_calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def trace_ids_for_span_ids(self, span_ids, **scope):
        self.trace_map_calls.append(scope)
        return {str(s): TRACE for s in span_ids}

    def child_tree_spans_by_trace_ids(self, trace_ids, **scope):
        self.tree_calls.append(scope)
        return {}


def _foreign_org_project(project):
    """A project in a new organization, shaped like ``project``."""
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project

    org = Organization.objects.create(name="Foreign organization")
    user = User.objects.create_user(
        email=f"foreign-{uuid.uuid4().hex}@example.invalid", organization=org
    )
    workspace = Workspace.objects.create(
        name="Foreign workspace", organization=org, created_by=user
    )
    return Project.objects.create(
        name="Foreign project",
        organization=org,
        workspace=workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )


@pytest.mark.django_db
def test_org_wide_dataset_child_tree_reads_in_the_orgs_projects(
    monkeypatch, organization, project
):
    """An org-wide export (no project) maps span ids to traces and reads the
    child trees in the organization's projects, never in another
    organization's copy of the same ids."""
    from tracer.tasks import dataset as dataset_tasks

    foreign = _foreign_org_project(project)
    reader = _ChildTreeReader()
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    dataset_tasks._serialize_span_trees_batch(["span-1"], org_id=str(organization.id))
    expected = {str(project.id)}
    (map_call,) = reader.trace_map_calls
    (tree_call,) = reader.tree_calls
    for call in (map_call, tree_call):
        assert call["project_id"] is None
        assert set(call["project_ids"]) == expected
        assert str(foreign.id) not in call["project_ids"]

    dataset_tasks._serialize_span_trees_batch(["span-1"], project_id=str(project.id))
    assert reader.trace_map_calls[-1] == {
        "project_id": str(project.id),
        "project_ids": None,
    }
    assert reader.tree_calls[-1] == {"project_id": str(project.id), "project_ids": None}


def test_span_to_trace_map_binds_the_project_scope():
    client = _RecordingClient(rows=[("span-1", TRACE)])
    reader = _reader(client)
    assert reader.trace_ids_for_span_ids(["span-1"], project_ids=[OWN, OWN]) == {
        "span-1": TRACE
    }
    sql, params, _ = client.calls[0]
    assert "project_id IN %(pids)s" in sql
    assert params["pids"] == (OWN,)
    assert reader.trace_ids_for_span_ids(["span-1"], project_ids=[]) == {}
    assert len(client.calls) == 1


# ── a missing scope fails loudly ───────────────────────────────────────────────


def _unscoped_reader(monkeypatch):
    """Routes ``get_reader()`` to a real reader over a recording client, so
    an unscoped read raises the reader's own ``UnscopedSpanReadError``."""
    client = _RecordingClient()
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _reader(client)
    )
    return client


_FAIL_OPEN_WRAPPERS = {
    "span_for_item": lambda h: h._ch_span_for_item("span-1", project_ids=None),
    "root_span_for_trace": lambda h: h._ch_root_span_for_trace(TRACE, project_ids=None),
    "batch_spans": lambda h: h._batch_ch_spans(["span-1"], project_id=None),
    "batch_trace_roots": lambda h: h._batch_ch_trace_roots([TRACE], project_id=None),
}


@pytest.mark.parametrize("wrapper", sorted(_FAIL_OPEN_WRAPPERS))
def test_fail_open_annotation_reads_raise_a_missing_scope(monkeypatch, wrapper):
    """A render read that treats a CH error as "deleted" still raises when it
    was called without a scope: that is a bug, not a missing span."""
    from model_hub.utils import annotation_queue_helpers as helpers

    client = _unscoped_reader(monkeypatch)
    with pytest.raises(UnscopedSpanReadError):
        _FAIL_OPEN_WRAPPERS[wrapper](helpers)
    assert client.calls == []


@pytest.mark.parametrize("helper", ["export_rows", "child_trees"])
def test_dataset_export_does_not_bisect_a_missing_scope(helper):
    """The export bisects a failed batch down to single ids; a missing scope
    fails every half the same way, so it is raised on the first read."""
    from tracer.tasks import dataset as dataset_tasks

    reader = _reader(_RecordingClient())
    seen = []
    if helper == "export_rows":
        real = reader.export_fields_by_ids
        reader.export_fields_by_ids = lambda ids, *a, **kw: (
            seen.append(ids) or real(ids, *a, **kw)
        )
        call = lambda: dataset_tasks._export_span_rows(  # noqa: E731
            reader, ["a", "b", "c"], {"input"}
        )
    else:
        real = reader.child_tree_spans_by_trace_ids
        reader.child_tree_spans_by_trace_ids = lambda ids, **kw: (
            seen.append(ids) or real(ids, **kw)
        )
        call = lambda: dataset_tasks._child_tree_spans_by_trace_ids(  # noqa: E731
            reader, ["a", "b", "c"]
        )
    with pytest.raises(UnscopedSpanReadError):
        call()
    assert seen == [["a", "b", "c"]]


# ── deep analysis runs in the verified project ─────────────────────────────────


@pytest.mark.django_db
def test_deep_analysis_reads_and_dispatches_in_the_clusters_project(
    monkeypatch, project
):
    """The feed's deep analysis works in the project of the cluster that
    links the trace: another organization's analysis of the same trace id is
    neither shown nor taken as done, and the worker is told the project."""
    from tracer.models.trace_error_analysis import (
        ErrorClusterTraces,
        TraceErrorAnalysis,
    )
    from tracer.queries import deep_analysis_state, feed
    from tracer.tests.test_error_analysis import make_feed_group

    trace_id = str(uuid.uuid4())
    cluster = make_feed_group(project, "tenancy-deep-analysis")
    ErrorClusterTraces.objects.create(cluster=cluster, trace_id=trace_id)
    TraceErrorAnalysis.objects.create(
        trace_id=trace_id, project=_foreign_org_project(project)
    )
    enqueue = Mock()
    monkeypatch.setattr("tracer.tasks.run_deep_analysis_on_demand.delay", enqueue)
    args = (cluster.cluster_id, trace_id, [str(project.pk)])
    deep_analysis_state.clear(trace_id)
    try:
        assert feed.get_deep_analysis(*args).status == "idle"
        assert feed.dispatch_deep_analysis(*args).status == "running"
        enqueue.assert_called_once_with(trace_id, False, project_id=str(project.pk))
    finally:
        deep_analysis_state.clear(trace_id)


@pytest.mark.django_db
def test_queued_deep_analysis_without_a_project_reads_nothing(monkeypatch, project):
    """A job queued before the dispatcher sent the project is marked failed;
    it neither deletes another tenant's analysis nor reads the trace."""
    from tracer.models.trace_error_analysis import TraceErrorAnalysis
    from tracer.queries import deep_analysis_state
    from tracer.tasks import error_analysis as task_module

    trace_id = str(uuid.uuid4())
    kept = TraceErrorAnalysis.objects.create(trace_id=trace_id, project=project)
    analyze = Mock()
    monkeypatch.setattr(task_module, "analyze_single_trace", analyze)
    # The worker's DB-lifecycle hook would close the test transaction.
    monkeypatch.setattr(task_module, "close_old_connections", lambda: None)
    deep_analysis_state.clear(trace_id)
    try:
        result = task_module.run_deep_analysis_on_demand._original_func(trace_id, True)
        assert result["success"] is False
        analyze.assert_not_called()
        assert TraceErrorAnalysis.objects.filter(pk=kept.pk).exists()
        assert deep_analysis_state.status(trace_id, has_analysis=False) == "failed"
    finally:
        deep_analysis_state.clear(trace_id)


class _RootIdReader:
    """``root_ids_by_trace_ids`` over two tenants' roots of one trace id; the
    foreign root sorts first, as an older copy would."""

    def __init__(self, roots):
        self._roots = roots  # [(root_id, project_id)] in CH order
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def root_ids_by_trace_ids(self, trace_ids, project_ids=None):
        self.calls.append({"project_ids": project_ids})
        for root_id, pid in self._roots:
            if project_ids is None or pid in project_ids:
                return {trace_ids[0]: (root_id, pid)}
        return {}


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_error_analysis_reads_bills_and_stores_in_the_given_project(
    monkeypatch, project
):
    """The analysis agent, the billing row and the skip check use the
    project the caller verified, not whichever tenant's root of the trace id
    ClickHouse returns first."""
    import ee.agenthub.traceerroragent.traceerror as agent_module
    from tracer.tasks import error_analysis as task_module

    foreign = _foreign_org_project(project)
    trace_id = str(uuid.uuid4())
    # The worker's DB-lifecycle hook would close the test transaction.
    monkeypatch.setattr(task_module, "close_old_connections", lambda: None)
    reader = _RootIdReader(
        [("foreign-root", str(foreign.id)), ("own-root", str(project.id))]
    )
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    agents = []

    class _Agent:
        def __init__(self, **kwargs):
            agents.append(kwargs)
            self.cost = {"total_cost": 0}

        def summarize(self):
            return {"errors": []}

    monkeypatch.setattr(agent_module, "TraceErrorAnalysisAgent", _Agent)
    billed = []

    def _bill(organization, **kwargs):
        billed.append((str(organization.id), kwargs["config"]["project_id"]))
        return Mock()

    monkeypatch.setattr(
        "ee.usage.utils.usage_entries.log_and_deduct_cost_for_api_request", _bill
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_a, **_kw: SimpleNamespace(allowed=True, reason=None),
    )
    monkeypatch.setattr("ee.usage.services.emitter.emit", Mock())

    result = task_module.analyze_single_trace(
        trace_id, None, ingest_embeddings=False, project_id=str(project.id)
    )

    assert result["success"] is True, result
    assert reader.calls == [{"project_ids": [str(project.id)]}]
    assert [a["project_id"] for a in agents] == [str(project.id)]
    assert billed == [(str(project.organization_id), str(project.id))]


@pytest.mark.django_db
def test_run_evaluation_fan_out_reads_each_span_in_the_versions_project(
    monkeypatch, auth_client, organization, workspace, custom_eval_config
):
    """The spans are listed in the version's project; each queued evaluation
    is told that project, even when the config belongs to another project."""
    from tracer.models.project import Project
    from tracer.models.project_version import ProjectVersion
    from tracer.views import custom_eval_config as view_module

    other = Project.objects.create(
        name="Version project",
        organization=organization,
        workspace=workspace,
        model_type=custom_eval_config.project.model_type,
        trace_type="observe",
    )
    version = ProjectVersion.objects.create(
        project=other,
        name="v",
        version="v1",
        eval_tags=[
            {"custom_eval_config_id": str(custom_eval_config.id), "value": "LLM"}
        ],
    )
    reader = _ScopeRecordingReader(result=[SimpleNamespace(id="span-1")])
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    delay = Mock()
    monkeypatch.setattr(view_module.evaluate_observation_span, "delay", delay)

    response = auth_client.post(
        "/tracer/custom-eval-config/run_evaluation/",
        {
            "custom_eval_config_id": str(custom_eval_config.id),
            "project_version_id": str(version.id),
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    assert reader.calls[0][1] == (str(other.id),)
    delay.assert_called_once_with(
        "span-1", custom_eval_config.id, project_id=str(other.id)
    )
