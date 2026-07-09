"""One-time baseline backfill.

Stamps ``config_hash`` and corrects ``status`` on legacy live ``EvalLogger``
rows so the reconciler's "did the config change?" comparison has a real baseline
("as of migration, every result reflects the current config"). Without it the
first reconcile would either re-run all history (empty != any hash) or suppress
every legitimate re-run.

Built to run as a migration over a multi-million-row table:
- The status pass is a single keyset-paginated sweep (advance by primary key),
  so every row is visited once instead of re-scanning the table per batch.
- The hash pass computes each config's hash once (in Python) and stamps that
  config's rows with a batched server-side UPDATE — no row ids are shipped to
  Python.
- Updates run in bounded batches; under the ``atomic=False`` migration each
  batch auto-commits, so locks/transactions stay small.

Idempotent: the status pass skips rows already on the target status and the
hash pass only touches rows still missing a hash, so re-running it (e.g. right
before cutover, to catch rows the old cron created after the first run) is cheap.
``deleted = false`` is filtered explicitly because raw SQL bypasses the default
manager — soft-deleted rows are never touched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db import connection

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.services.eval_tasks.config_hash import resolved_config_hash

_TABLE = EvalLogger._meta.db_table
_BATCH = 5_000


@dataclass
class BackfillResult:
    status_changed: int = 0
    hashed: int = 0


def backfill_config_hash_and_status(*, batch_size: int = _BATCH) -> BackfillResult:
    """Run the baseline backfill over all live entries. Returns rows changed."""
    return BackfillResult(
        status_changed=_backfill_status(batch_size),
        hashed=_backfill_hashes(batch_size),
    )


def _backfill_status(batch_size: int) -> int:
    """Flip ``error`` rows to ``errored`` and ``skipped_reason`` rows to
    ``skipped`` in one keyset sweep ordered by primary key."""
    errored = EvalEntryStatus.ERRORED.value
    skipped = EvalEntryStatus.SKIPPED.value
    sql = (
        f"UPDATE {_TABLE} SET status = CASE "
        f"WHEN error THEN %s "
        f"WHEN skipped_reason IS NOT NULL AND skipped_reason <> '' THEN %s "
        f"ELSE status END "
        f"WHERE id IN ("
        f"  SELECT id FROM {_TABLE} "
        f"  WHERE id > %s AND deleted = false "
        f"    AND (error = true OR (skipped_reason IS NOT NULL AND skipped_reason <> '')) "
        f"    AND status NOT IN (%s, %s) "
        f"  ORDER BY id LIMIT %s"
        f") RETURNING id"
    )  # noqa: S608 — table name is a trusted model attribute; values are bound
    last_id = uuid.UUID(int=0)
    total = 0
    with connection.cursor() as cur:
        while True:
            cur.execute(sql, [errored, skipped, last_id, errored, skipped, batch_size])
            ids = [row[0] for row in cur.fetchall()]
            if not ids:
                break
            total += len(ids)
            last_id = max(ids)
    return total


def _backfill_hashes(batch_size: int) -> int:
    """Stamp ``config_hash`` per config — one hash computation each, only on rows
    that don't already have one. Rows whose config is gone are left null."""
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT custom_eval_config_id FROM {_TABLE} "  # noqa: S608
            f"WHERE config_hash IS NULL AND custom_eval_config_id IS NOT NULL "
            f"AND deleted = false"
        )
        config_ids = [row[0] for row in cur.fetchall()]  # bounded by config count

    update_sql = (
        f"UPDATE {_TABLE} SET config_hash = %s WHERE id IN ("
        f"  SELECT id FROM {_TABLE} "
        f"  WHERE custom_eval_config_id = %s AND config_hash IS NULL "
        f"    AND deleted = false "
        f"  LIMIT %s"
        f")"
    )  # noqa: S608 — table name is a trusted model attribute; values are bound

    total = 0
    with connection.cursor() as cur:
        for config_id in config_ids:
            # no_workspace_objects matches the raw scan's global scope; the
            # default manager would workspace-filter and silently leave configs
            # from other workspaces null if this ever runs in a request context.
            config = CustomEvalConfig.no_workspace_objects.filter(id=config_id).first()
            if config is None:
                continue  # eval no longer exists — outside the current eval-set
            config_hash = resolved_config_hash(config)
            while True:
                cur.execute(update_sql, [config_hash, config_id, batch_size])
                if cur.rowcount < batch_size:
                    total += cur.rowcount
                    break
                total += cur.rowcount
    return total
