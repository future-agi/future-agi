"""A1+A2+A3 root-lookup tests: S4 project-scoped read budget
(moved + un-xfailed from test_baselines once A1 landed), S5 golden root-pick
parity against the fabricator manifest, and the IN-list chunk fan-out.
"""

from __future__ import annotations

import math
from itertools import cycle, islice

import pytest

from tests.stress.budgets import (
    ROOT_LOOKUP_MAX_MEMORY,
    ROOT_LOOKUP_MAX_READ_ROWS_FACTOR,
)
from tests.stress.ch_asserts import ch_query_budget
from tracer.models.eval_task import RowType
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.eval_tasks.entries import _FK_CHUNK, _resolve_entry_fks

pytestmark = pytest.mark.stress


@pytest.mark.django_db
def test_s4_root_lookup_prunes_to_project(stress_dataset, eval_task_factory):
    manifest = stress_dataset.target
    eval_task_factory(manifest.project_id, row_type=RowType.TRACES)
    with ch_query_budget("stress:A1:root-lookup") as b:
        with get_reader() as reader:
            reader.list_root_spans_by_trace_ids(
                manifest.trace_ids[:_FK_CHUNK],
                include_heavy=False,
                project_id=manifest.project_id,
            )
    assert (
        b.total("read_rows") <= manifest.span_count * ROOT_LOOKUP_MAX_READ_ROWS_FACTOR
    )
    assert b.max("memory_usage") <= ROOT_LOOKUP_MAX_MEMORY


def test_root_pick_parity_agent_deep(stress_dataset):
    # agent-deep dataset seeded by the loadgen fixture (seed 44). The manifest
    # records the fabricator's root per trace — the parity oracle is the
    # generator, not a snapshot of old code.
    manifest = stress_dataset.agent_deep
    with get_reader() as reader:
        roots = reader.list_root_spans_by_trace_ids(
            manifest.trace_ids,
            include_heavy=False,
            project_id=manifest.project_id,
        )
    got = {tid: root.id for tid, root in roots.items()}
    assert got == manifest.root_span_id_by_trace


def test_fk_resolve_chunks_in_list(stress_dataset):
    # 2,500 ids -> ceil(2500/1000) = 3 CH queries, each bounded to a single
    # _FK_CHUNK-id call's read_rows (chunking keeps per-query cost flat).
    # Reader is constructed *inside* each budget block so query tagging (which
    # applies at client construction) is captured by the budget context.
    manifest = stress_dataset.target
    ids = list(islice(cycle(manifest.trace_ids), 2 * _FK_CHUNK + _FK_CHUNK // 2))
    expected_chunks = math.ceil(len(ids) / _FK_CHUNK)

    with ch_query_budget("stress:A3:chunk-baseline") as base:
        with get_reader() as reader:
            _resolve_entry_fks(
                reader,
                RowType.TRACES,
                manifest.trace_ids[:_FK_CHUNK],
                project_id=manifest.project_id,
            )
    with ch_query_budget("stress:A3:chunk-fanout") as b:
        with get_reader() as reader:
            _resolve_entry_fks(
                reader, RowType.TRACES, ids, project_id=manifest.project_id
            )

    assert base.count == 1
    assert b.count == expected_chunks
    assert (
        b.max("read_rows") <= base.max("read_rows") * ROOT_LOOKUP_MAX_READ_ROWS_FACTOR
    )
