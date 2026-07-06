"""Benchmark: OLD full-scan JOIN vs NEW column_id IN — TH-5508.

Run explicitly:

    docker exec backend bash -c "DJANGO_SETTINGS_MODULE=tfc.settings.test \
        pytest model_hub/tests/test_delete_eval_query_bench.py -v -s"

Seeds a Dataset with an eval column + reason column + N cells, then measures:

  1. `EXPLAIN` for the OLD predicate  (`Q(column=col) | Q(column__source_id__startswith=...)`) — JOIN + LIKE
  2. `EXPLAIN` for the NEW predicate  (`column_id IN (...)`)                                  — indexed FK IN

  3. Wall-clock time for both against real data.

Prints both plans + timings so the PR body can carry the evidence.
"""

import time
import uuid

import pytest
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

CELL_COUNT_PER_COL = 5_000        # target: 5k eval + 5k reason  = 10k
DISTRACTOR_COLUMNS = 40           # unrelated columns (other evals, other datasets)
DISTRACTOR_CELLS_PER_COL = 5_000  # 40 x 5k = 200k unrelated cells
# The OLD Seq-Scan approach must pass over ALL 210k cells to decide which to
# delete; the NEW column_id-IN approach uses the FK index to touch only the
# ~10k cells belonging to the 2 target columns. That's the prod shape at
# any scale (millions of cells across thousands of columns).


@pytest.mark.django_db
def test_bench_old_vs_new_delete_query(organization, workspace):
    print()
    print("=" * 78)
    print(f"  DELETE-EVAL QUERY BENCHMARK  —  {CELL_COUNT_PER_COL:,} cells per column")
    print("=" * 78)

    # ── Seed ────────────────────────────────────────────────────────────────
    dataset = Dataset.objects.create(
        name="bench-ds", organization=organization, workspace=workspace,
    )
    metric_id = uuid.uuid4()
    eval_col = Column.objects.create(
        name="Eval Bench",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric_id),
    )
    reason_col = Column.objects.create(
        name="Eval Bench-reason",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{eval_col.id}-sourceid-{metric_id}",
    )
    rows = Row.objects.bulk_create(
        [Row(dataset=dataset, order=i) for i in range(CELL_COUNT_PER_COL)],
        batch_size=1000,
    )
    Cell.objects.bulk_create(
        [Cell(dataset=dataset, row=rows[i], column=eval_col, value=f"v{i}")
         for i in range(CELL_COUNT_PER_COL)],
        batch_size=1000,
    )
    Cell.objects.bulk_create(
        [Cell(dataset=dataset, row=rows[i], column=reason_col, value=f"r{i}")
         for i in range(CELL_COUNT_PER_COL)],
        batch_size=1000,
    )

    # Distractor columns + cells — unrelated to the target eval. This is
    # what makes the NEW plan look meaningfully better: OLD Seq Scan must
    # pass over these too; NEW's column_id IN uses the FK index to skip
    # them entirely.
    distractor_cols = Column.objects.bulk_create([
        Column(
            name=f"Other Col {i}",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(uuid.uuid4()),
        )
        for i in range(DISTRACTOR_COLUMNS)
    ], batch_size=100)
    distractor_rows = Row.objects.bulk_create(
        [Row(dataset=dataset, order=CELL_COUNT_PER_COL + i)
         for i in range(DISTRACTOR_CELLS_PER_COL)],
        batch_size=1000,
    )
    for col in distractor_cols:
        Cell.objects.bulk_create(
            [Cell(dataset=dataset, row=distractor_rows[i], column=col, value=f"x{i}")
             for i in range(DISTRACTOR_CELLS_PER_COL)],
            batch_size=1000,
        )

    total_cells = (CELL_COUNT_PER_COL * 2) + (DISTRACTOR_COLUMNS * DISTRACTOR_CELLS_PER_COL)
    print(f"  Seeded: 2 target cols with {CELL_COUNT_PER_COL:,} cells each")
    print(f"          {DISTRACTOR_COLUMNS} distractor cols with "
          f"{DISTRACTOR_CELLS_PER_COL:,} cells each")
    print(f"          Total Cell rows: {total_cells:,}")
    print()

    # Force ANALYZE so the planner has fresh stats — matters on small test DBs.
    with connection.cursor() as cur:
        cur.execute("ANALYZE model_hub_cell, model_hub_column")

    # Show relevant indexes so we know what the planner can use.
    print("─" * 78)
    print("  Cell indexes")
    print("─" * 78)
    with connection.cursor() as cur:
        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'model_hub_cell' ORDER BY indexname"
        )
        for name, defn in cur.fetchall():
            print(f"    {name}")
            print(f"      {defn}")
    print()

    now = timezone.now()

    # ── Show query plans BEFORE any UPDATE so the actual row counts in the
    #    EXPLAIN ANALYZE output aren't corrupted by prior soft-deletes. ─────

    # OLD predicate — the shape we're removing
    old_qs = Cell.objects.filter(
        Q(column=eval_col)
        | Q(column__source_id__startswith=f"{eval_col.id}-sourceid-"),
        deleted=False,
    )
    old_sql, old_params = old_qs.query.sql_with_params()

    print("─" * 78)
    print("  OLD predicate: JOIN + LIKE on column.source_id  (Sarthak's concern)")
    print("─" * 78)
    with connection.cursor() as cur:
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, TIMING) {old_sql}", old_params)
        for row in cur.fetchall():
            print("   ", row[0])
    print()

    # NEW predicate — two-step
    step1_qs = Column.objects.filter(
        Q(id=eval_col.id)
        | Q(source_id__startswith=f"{eval_col.id}-sourceid-"),
        deleted=False,
    ).values_list("id", flat=True)
    col_ids_preview = list(step1_qs)
    step1_sql, step1_params = step1_qs.query.sql_with_params()
    step2_qs = Cell.objects.filter(column_id__in=col_ids_preview, deleted=False)
    step2_sql, step2_params = step2_qs.query.sql_with_params()

    print("─" * 78)
    print("  NEW two-step: (1) resolve col_ids from Column, (2) column_id IN")
    print("─" * 78)
    print("  Step 1 — resolve col_ids from Column:")
    with connection.cursor() as cur:
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, TIMING) {step1_sql}", step1_params)
        for row in cur.fetchall():
            print("   ", row[0])
    print()
    print("  Step 2 — Cell delete via indexed column_id IN:")
    with connection.cursor() as cur:
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, TIMING) {step2_sql}", step2_params)
        for row in cur.fetchall():
            print("   ", row[0])
    print()

    # Prod-scale simulation: on 50M cells the planner would prefer an index
    # scan for a 2-item column_id IN, but on 100k rows here the cost model
    # picks Seq Scan. Force seqscan off to show what the query does on
    # prod-scale data where the index is preferred.
    print("  Step 2 — same query with `SET enable_seqscan=OFF` (prod-scale sim):")
    with connection.cursor() as cur:
        cur.execute("SET LOCAL enable_seqscan = OFF")
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, TIMING) {step2_sql}", step2_params)
        for row in cur.fetchall():
            print("   ", row[0])
        cur.execute("SET LOCAL enable_seqscan = ON")
    print()

    # Same forced-index run on the OLD predicate, for a fair prod-scale sim.
    print("  OLD predicate — same query with `SET enable_seqscan=OFF` "
          "(prod-scale sim):")
    with connection.cursor() as cur:
        cur.execute("SET LOCAL enable_seqscan = OFF")
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, TIMING) {old_sql}", old_params)
        for row in cur.fetchall():
            print("   ", row[0])
        cur.execute("SET LOCAL enable_seqscan = ON")
    print()

    # ── Now time the actual UPDATEs.  Reset via all_objects between runs
    #    because BaseModelManager filters ``deleted=False`` by default. ────

    # Time OLD UPDATE
    t0 = time.perf_counter()
    Cell.objects.filter(
        Q(column=eval_col)
        | Q(column__source_id__startswith=f"{eval_col.id}-sourceid-"),
        deleted=False,
    ).update(deleted=True, deleted_at=now)
    old_wall = time.perf_counter() - t0

    # Reset — use all_objects to bypass BaseModelManager's deleted=False filter.
    Cell.all_objects.filter(column_id__in=[eval_col.id, reason_col.id]).update(
        deleted=False, deleted_at=None,
    )

    # Time NEW UPDATE
    t0 = time.perf_counter()
    col_ids = list(
        Column.objects.filter(
            Q(id=eval_col.id)
            | Q(source_id__startswith=f"{eval_col.id}-sourceid-"),
            deleted=False,
        ).values_list("id", flat=True)
    )
    Cell.objects.filter(column_id__in=col_ids, deleted=False).update(
        deleted=True, deleted_at=now,
    )
    new_wall = time.perf_counter() - t0

    # ── Report ──────────────────────────────────────────────────────────────
    print("=" * 78)
    print("  WALL-CLOCK COMPARISON")
    print("=" * 78)
    print(f"    OLD (JOIN + LIKE):   {old_wall * 1000:>8.2f} ms")
    print(f"    NEW (column_id IN):  {new_wall * 1000:>8.2f} ms")
    speedup = old_wall / new_wall if new_wall else float("inf")
    print(f"    Speedup:             {speedup:>8.2f}x")
    print("=" * 78)
    print()

    # Correctness check: both delete the same rows
    assert Cell.objects.filter(
        column_id__in=[eval_col.id, reason_col.id], deleted=False,
    ).count() == 0, "NEW approach should have soft-deleted all seeded cells"
