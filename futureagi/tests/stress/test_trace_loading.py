"""Lean-first trace span loading — S6 memory budget + lean/heavy parity.

S6 (moved from test_baselines.py): loading one fat voice trace stays below
TRACE_LOAD_MAX_PY_PEAK after the A4 lean-first fix (xfail marker removed).

Parity: a TRACE run_entry whose mapping references a heavy span field
(``spans.first.llm.messages.transcript``) produces the same resolved
mapping params as running through a wholesale include_heavy=True load.

Session parity: a SESSION run_entry whose mapping references a heavy inner-span
field (``traces.first.spans.first.llm.messages.transcript``) produces the same
resolved params whether the eval path uses lean-first loading or a wholesale
heavy load.  Before the session shim landed, the lean path returned an empty
value and raised "required attribute not found".
"""

from __future__ import annotations

import tracemalloc

import pytest

from tests.stress.budgets import TRACE_LOAD_MAX_PY_PEAK
from tests.stress.conftest import VOICE_PROJECT_ID

pytestmark = pytest.mark.stress

_TRANSCRIPT_MAPPING = {
    "input": "spans.first.llm.messages.transcript",
    "output": "output",
}

# Session-level mapping: heavy inner-span field + a lean trace-level field.
# Verified against the voice seed: first trace's first span (sorted by
# start_time) carries ``llm.messages.transcript`` in attributes_extra.
_SESSION_TRANSCRIPT_MAPPING = {
    "input": "traces.first.spans.first.llm.messages.transcript",
    "output": "traces.first.output",
}


def test_s6_trace_load_python_peak_bounded(stress_dataset):
    from tracer.services.clickhouse.v2.eval_loader import (
        eval_read_source,
        filter_observation_spans_by_trace,
    )

    manifest = stress_dataset.voice
    trace_id = manifest.trace_ids[0]
    with eval_read_source("clickhouse"):
        tracemalloc.start()
        try:
            spans = filter_observation_spans_by_trace(
                trace_id, project_id=manifest.project_id
            )
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    assert len(spans) > 0
    # Ceiling independent of payload fatness: below one 1.2 MiB transcript.
    assert peak <= TRACE_LOAD_MAX_PY_PEAK


@pytest.mark.django_db
def test_trace_eval_lean_heavy_parity(
    stress_dataset, eval_task_factory, stub_run_eval, stub_cost_log
):
    """A mapping that references ``spans.first.llm.messages.transcript`` (a
    heavy field in attributes_extra) resolves to the same value whether the
    eval path uses lean-first loading or a wholesale heavy load.

    Comparison approach:
      - lean-first: ``resolve_trace_mapping_lean_first`` with the contextvar
        mechanism (triggers a heavy second-pass for the root span only).
      - heavy-all: monkeypatch ``filter_observation_spans_by_trace`` to return
        spans loaded with all heavy columns, then call ``_process_trace_mapping``
        directly.
    """
    import tracer.services.clickhouse.v2.eval_loader as _loader_mod
    from tracer.services.clickhouse.v2 import get_reader
    from tracer.services.clickhouse.v2.eval_loader import (
        _construct_from_chspan,
        eval_read_source,
        get_trace,
    )
    from tracer.utils.eval import (
        _process_trace_mapping,
        resolve_trace_mapping_lean_first,
    )

    manifest = stress_dataset.voice
    trace_id = manifest.trace_ids[0]

    # Create PG rows: project + EvalTemplate (needed by _process_trace_mapping
    # for optional-key lookup).  eval_task_factory handles get_or_create.
    task = eval_task_factory(VOICE_PROJECT_ID, row_type="traces", n_evals=1)
    template_id = task.evals.first().eval_template_id

    with eval_read_source("clickhouse"):
        trace = get_trace(trace_id, project_id=VOICE_PROJECT_ID)

        # Pass 1: lean-first (the A4 path).
        lean_params = resolve_trace_mapping_lean_first(
            _TRANSCRIPT_MAPPING.copy(), trace, template_id
        )

        # Reference: wholesale heavy load — monkeypatch the loader so
        # filter_observation_spans_by_trace always returns fully-hydrated spans.
        with get_reader() as reader:
            heavy_ch_rows = reader.list_by_trace(
                trace_id, include_heavy=True, project_id=VOICE_PROJECT_ID
            )
        heavy_spans = sorted(
            [_construct_from_chspan(r) for r in heavy_ch_rows],
            key=lambda s: (s.start_time is None, s.start_time, str(s.id)),
        )

        _orig = _loader_mod.filter_observation_spans_by_trace

        def _always_heavy(tid, deleted=False, *, project_id=None, heavy_span_ids=None):
            return list(heavy_spans)

        _loader_mod.filter_observation_spans_by_trace = _always_heavy
        try:
            trace_heavy = get_trace(trace_id, project_id=VOICE_PROJECT_ID)
            heavy_params = _process_trace_mapping(
                _TRANSCRIPT_MAPPING.copy(), trace_heavy, template_id
            )
        finally:
            _loader_mod.filter_observation_spans_by_trace = _orig

    assert set(lean_params.keys()) == set(heavy_params.keys()), (
        f"key mismatch: lean={set(lean_params)} heavy={set(heavy_params)}"
    )
    for key in lean_params:
        assert lean_params[key] == heavy_params[key], (
            f"field '{key}' mismatch\n"
            f"  lean : {lean_params[key]!r:.200}\n"
            f"  heavy: {heavy_params[key]!r:.200}"
        )


@pytest.mark.django_db
def test_session_eval_lean_heavy_parity(
    stress_dataset, eval_task_factory, stub_run_eval, stub_cost_log
):
    """A session mapping that references ``traces.first.spans.first.llm.messages.transcript``
    (a heavy field in attributes_extra on an inner trace span) resolves to the same
    value whether the session eval path uses lean-first loading or a wholesale heavy
    load.  Without the session shim this test fails with "required attribute not found"
    because inner spans load lean and the heavy overflow field is absent.

    Comparison approach:
      - lean-first: ``resolve_session_mapping_lean_first`` — triggers a heavy
        second-pass for the identified span only.
      - heavy-all: monkeypatch ``filter_observation_spans_by_trace`` to always
        return spans loaded with all heavy columns, then call plain
        ``_process_session_mapping``.
    """
    import tracer.services.clickhouse.v2.eval_loader as _loader_mod
    from tracer.services.clickhouse.v2 import get_reader
    from tracer.services.clickhouse.v2.eval_loader import (
        _construct_from_chspan,
        eval_read_source,
        get_trace_session,
    )
    from tracer.utils.eval import (
        _process_session_mapping,
        resolve_session_mapping_lean_first,
    )

    task = eval_task_factory(VOICE_PROJECT_ID, row_type="sessions", n_evals=1)
    template_id = task.evals.first().eval_template_id

    from tracer.models.project import Project

    project = Project.objects.get(id=VOICE_PROJECT_ID)

    # The manifest records external_session_id values (e.g. ``session-00000000``),
    # not the internal CH UUID.  Fetch the actual UUID from trace_sessions so
    # get_trace_session and session_trace_ids receive a valid UUID argument.
    from tests.stress.ch_asserts import _client

    ch = _client()
    try:
        session_uuid_rows = ch.query(
            "SELECT toString(trace_session_id) FROM trace_sessions FINAL "
            "WHERE project_id = %(p)s AND is_deleted = 0 LIMIT 1",
            parameters={"p": VOICE_PROJECT_ID},
        ).result_rows
    finally:
        ch.close()
    assert session_uuid_rows, "No seeded sessions found for voice project"
    session_id = session_uuid_rows[0][0]

    with eval_read_source("clickhouse"):
        session = get_trace_session(session_id, project=project)

        # Pass 1: lean-first (the session shim path).
        lean_params = resolve_session_mapping_lean_first(
            _SESSION_TRANSCRIPT_MAPPING.copy(), session, template_id
        )

        # Reference: wholesale heavy load — pre-load heavy spans for every trace
        # in the session and monkeypatch so filter_observation_spans_by_trace
        # always returns them fully-hydrated.
        with get_reader() as reader:
            trace_ids = reader.session_trace_ids(str(project.id), str(session_id))
        heavy_spans_by_trace: dict[str, list] = {}
        for tid in trace_ids:
            with get_reader() as reader:
                heavy_rows = reader.list_by_trace(
                    tid, include_heavy=True, project_id=str(project.id)
                )
            heavy_spans_by_trace[tid] = sorted(
                [_construct_from_chspan(r) for r in heavy_rows],
                key=lambda s: (s.start_time is None, s.start_time, str(s.id)),
            )

        _orig = _loader_mod.filter_observation_spans_by_trace

        def _always_heavy(tid, deleted=False, *, project_id=None, heavy_span_ids=None):
            return list(heavy_spans_by_trace.get(str(tid), []))

        _loader_mod.filter_observation_spans_by_trace = _always_heavy
        try:
            session_heavy = get_trace_session(session_id, project=project)
            heavy_params = _process_session_mapping(
                _SESSION_TRANSCRIPT_MAPPING.copy(), session_heavy, template_id
            )
        finally:
            _loader_mod.filter_observation_spans_by_trace = _orig

    assert set(lean_params.keys()) == set(heavy_params.keys()), (
        f"key mismatch: lean={set(lean_params)} heavy={set(heavy_params)}"
    )
    for key in lean_params:
        assert lean_params[key] == heavy_params[key], (
            f"field '{key}' mismatch\n"
            f"  lean : {lean_params[key]!r:.200}\n"
            f"  heavy: {heavy_params[key]!r:.200}"
        )
