"""Each Instant eval row's latest answer, kept narrow and indexed.

The eval logger holds an answer with everything around it (explanations,
metadata, the run's bookkeeping); counting or filtering 200,000 of those per
poll is what made a big run slow. ``tracer_instant_eval_answer`` keeps only
what the Ask page counts and filters by: the row, the question, the label and
Jev's confidence.

- ``record_answer`` is called when an entry reaches its terminal state
  (``entries.mark_terminal``), so a run's answers land here as they land.
- ``record_answers`` does the same for many entries of one task at once
  (the reaper failing entries that kept dying).
- ``forget_answers`` drops answers that are being asked again (retry).
- ``sync_answers`` copies a whole run over in batches, for runs whose answers
  were written some other way (runs from before this table, bulk imports),
  and ``ensure_synced`` decides when that is needed.
"""

from __future__ import annotations

import uuid

import structlog
from django.db import connection

from tracer.services.instant_eval_rows import ANALYZE_AFTER, analyze

logger = structlog.get_logger(__name__)

BATCH = 1000

# The entry's row id as text, by what the entry targets.
_ENTRY_RID = """CASE l.target_type
  WHEN 'session' THEN l.trace_session_id::text
  WHEN 'trace' THEN l.trace_id::text
  ELSE l.observation_span_id END"""

SETTLED = "l.status NOT IN ('pending', 'running')"


def _label_sql() -> tuple[str, str]:
    from tracer.services.instant_eval_summary import CONFIDENCE, LABEL

    return LABEL, CONFIDENCE


def _uuid(value) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


# task id -> (is an Instant eval run, monotonic expiry). A run is created with
# its task, before any entry lands, so a positive answer never goes stale; a
# negative one is re-checked after a minute. Entries that land unrecorded are
# caught up when the run finishes (``ensure_synced``).
_INSTANT_TASKS: dict[str, tuple[bool, float]] = {}
_INSTANT_TASKS_MAX = 4096
_NEGATIVE_TTL_S = 60.0


def is_instant_task(task_id) -> bool:
    """Whether ``task_id`` is an Instant eval run, cached per process so a
    landed entry of any other eval task costs no SQL."""
    import time

    from tracer.models.instant_eval import InstantEvalRun

    key = str(task_id)
    now = time.monotonic()
    cached = _INSTANT_TASKS.get(key)
    if cached is not None and (cached[0] or cached[1] > now):
        return cached[0]
    found = InstantEvalRun.objects.filter(eval_task_id=task_id).exists()
    if len(_INSTANT_TASKS) >= _INSTANT_TASKS_MAX:
        _INSTANT_TASKS.clear()
    _INSTANT_TASKS[key] = (found, now + _NEGATIVE_TTL_S)
    return found


def record_answer(entry_id) -> int:
    """Copy one entry's settled answer onto its run's row, if the entry
    belongs to an Instant eval run. A no-op for every other task."""
    label, confidence = _label_sql()
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT l.eval_task_id, {_ENTRY_RID} FROM tracer_eval_logger l "
            "WHERE l.id = %(id)s AND l.deleted = false",
            {"id": str(entry_id)},
        )
        found = cur.fetchone()
        task_id = _uuid(found[0]) if found and found[0] else None
        if not task_id or not found[1]:
            return 0
        cur.execute(
            "INSERT INTO tracer_instant_eval_answer "
            "(task_id, row_id, config_id, label, confidence, time, answered_at) "
            f"SELECT r.task_id, r.id, l.custom_eval_config_id, {label}, {confidence}, r.time, now() "
            "FROM tracer_eval_logger l JOIN tracer_instant_eval_row r "
            "ON r.task_id = %(task)s AND r.row_id = %(rid)s "
            f"WHERE l.id = %(id)s AND l.custom_eval_config_id IS NOT NULL AND {SETTLED} "
            "ON CONFLICT (task_id, row_id, config_id) DO UPDATE "
            "SET label = EXCLUDED.label, confidence = EXCLUDED.confidence, "
            "answered_at = EXCLUDED.answered_at",
            {"id": str(entry_id), "task": task_id, "rid": str(found[1])},
        )
        return cur.rowcount


def _copy(cur, task_id, entry_ids) -> int:
    """Copy the settled answers of these entries (one task's) onto its rows."""
    label, confidence = _label_sql()
    cur.execute(
        "INSERT INTO tracer_instant_eval_answer "
        "(task_id, row_id, config_id, label, confidence, time, answered_at) "
        f"SELECT r.task_id, r.id, l.custom_eval_config_id, {label}, {confidence}, "
        "r.time, l.updated_at "
        "FROM tracer_eval_logger l JOIN tracer_instant_eval_row r "
        f"ON r.task_id = %(task)s AND r.row_id = {_ENTRY_RID} "
        "WHERE l.id = ANY(%(ids)s::uuid[]) AND l.custom_eval_config_id IS NOT NULL "
        f"AND {SETTLED} "
        "ON CONFLICT (task_id, row_id, config_id) DO UPDATE "
        "SET label = EXCLUDED.label, confidence = EXCLUDED.confidence, "
        "answered_at = EXCLUDED.answered_at",
        {"task": str(task_id), "ids": [str(i) for i in entry_ids]},
    )
    return cur.rowcount


def record_answers(task_id, entry_ids) -> int:
    """Copy many settled entries of one task over, a thousand at a time. A
    no-op unless the task is an Instant eval run."""
    from tracer.models.instant_eval import InstantEvalRun

    if not InstantEvalRun.objects.filter(eval_task_id=task_id).exists():
        return 0
    ids = list(entry_ids)
    written = 0
    with connection.cursor() as cur:
        for start in range(0, len(ids), BATCH):
            written += _copy(cur, task_id, ids[start : start + BATCH])
    return written


def forget_answers(task_id, entry_ids) -> int:
    """Drop the answers of entries that are being asked again."""
    ids = [str(i) for i in entry_ids]
    if not ids:
        return 0
    removed = 0
    with connection.cursor() as cur:
        for start in range(0, len(ids), BATCH):
            cur.execute(
                "DELETE FROM tracer_instant_eval_answer a USING tracer_eval_logger l, "
                "tracer_instant_eval_row r "
                f"WHERE l.id = ANY(%(ids)s::uuid[]) AND r.task_id = %(task)s AND r.row_id = {_ENTRY_RID} "
                "AND a.row_id = r.id AND a.config_id = l.custom_eval_config_id",
                {"ids": ids[start : start + BATCH], "task": str(task_id)},
            )
            removed += cur.rowcount
    return removed


def sync_answers(task_id, row_type: str) -> int:
    """Copy every settled answer of a run onto its rows, a thousand entries
    at a time in the eval logger's (task, created) order, and drop answers
    whose entry is being read again."""
    written = 0
    after = None
    with connection.cursor() as cur:
        while True:
            keyset = "AND (l.created_at, l.id) > (%(c)s, %(i)s) " if after else ""
            cur.execute(
                "SELECT l.id, l.created_at FROM tracer_eval_logger l "
                "WHERE l.eval_task_id = %(task_s)s AND l.deleted = false "
                f"{keyset}ORDER BY l.created_at, l.id LIMIT %(n)s",
                {
                    "task_s": str(task_id),
                    "n": BATCH,
                    **({"c": after[1], "i": after[0]} if after else {}),
                },
            )
            batch = cur.fetchall()
            if not batch:
                break
            after = batch[-1]
            written += _copy(cur, task_id, [i for i, _ in batch])
            if len(batch) < BATCH:
                break
        # Answers whose entry is being read again.
        cur.execute(
            "SELECT l.id FROM tracer_eval_logger l WHERE l.eval_task_id = %(task_s)s "
            "AND l.deleted = false AND l.status IN ('pending', 'running')",
            {"task_s": str(task_id)},
        )
        waiting = [row[0] for row in cur.fetchall()]
    forget_answers(task_id, waiting)
    if written >= ANALYZE_AFTER:
        analyze("tracer_instant_eval_answer")
    logger.info("instant_eval_answers_synced", task_id=str(task_id), written=written)
    return written


def settled_counts(task_id) -> tuple[int, int]:
    """(settled entries in the eval logger, answers recorded here) for a run's
    rows: equal once every answer has been copied."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM tracer_eval_logger l "
            f"WHERE l.eval_task_id = %(task_s)s AND l.deleted = false AND {SETTLED}",
            {"task_s": str(task_id)},
        )
        entries = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM tracer_instant_eval_answer WHERE task_id = %(task)s",
            {"task": str(task_id)},
        )
        answers = cur.fetchone()[0]
    return int(entries), int(answers)


def ensure_synced(run, *, check: bool) -> bool:
    """Copy a run's answers over when they weren't recorded as they landed,
    or (``check``, for a run that just finished) when the counts disagree.
    Returns whether anything was copied."""
    from tracer.models.instant_eval import InstantEvalRun

    task_id = run.eval_task_id
    needed = not run.answers_synced
    if not needed and check:
        entries, answers = settled_counts(task_id)
        needed = entries != answers
    if not needed:
        return False
    sync_answers(task_id, run.row_type)
    InstantEvalRun.objects.filter(id=run.id).update(
        answers_synced=True, answers_version=run.answers_version + 1
    )
    run.answers_synced = True
    run.answers_version += 1
    return True
