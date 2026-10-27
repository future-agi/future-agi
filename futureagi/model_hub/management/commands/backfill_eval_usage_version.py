"""
Backfill version info on existing APICallLog entries for user eval templates.

Standalone replacement for the expensive part of migration
``0115_eval_usage_version_backfill``. That migration stamps every
APICallLog row for every user template — O(usage-log rows), potentially
millions — and since migrations run automatically on every pod during
deploy, it blocks that pod's rollout for its full duration.

This command does the same work but as a manual, resumable, idempotent
step: run it once, on a single pod, after deploy — never as part of the
automatic migration path.

Optimizations over the migration's logic (same semantics, faster plan):
  - SINGLE keyset-paginated pass (``id > last_seen``) over the table that
    unwraps double-encoded string configs AND stamps the version in the
    same UPDATE. The migration walked the table twice (unwrap pass, then
    stamp pass) and re-searched from the start of the table on every batch.
  - Everything happens server-side in SQL (``config || jsonb_build_object``)
    for ALL templates at once via an ``unnest`` join, instead of hydrating
    every row into Python and writing back with bulk_update — and instead
    of one full table scan per template.

Usage:
    python manage.py backfill_eval_usage_version                 # full run
    python manage.py backfill_eval_usage_version --dry-run       # counts only
    python manage.py backfill_eval_usage_version --chunk-size 5000
    python manage.py backfill_eval_usage_version --sleep 0.1     # throttle
    python manage.py backfill_eval_usage_version --template-id <uuid>
    python manage.py backfill_eval_usage_version --database default_direct
"""

import time
from typing import Callable, Optional

import structlog
from django.core.management.base import BaseCommand

logger = structlog.get_logger(__name__)

# One batch of the combined unwrap+stamp pass.
#
# Row selection (the ``batch`` CTE) picks, in id order above the keyset
# cursor, every row that still needs work:
#   - double-encoded configs (JSONB string wrapping an object/array), or
#   - object configs missing version_id whose source is a user template.
# ``norm_config`` is the config with the string wrapper removed.
#
# The UPDATE then stamps version info only where it applies (object config,
# no version_id yet, source has a mapped version — LEFT JOIN, so unwrap-only
# rows such as arrays, system-template logs, or already-stamped strings are
# still normalized without being stamped).
#
# The mapping join uses ``unnest`` instead of a VALUES literal so the full
# mapping is transmitted once per batch as three arrays rather than N*3
# individual parameters — avoids the psycopg3 65535-parameter ceiling and
# keeps the query plan stable across batches.
_BACKFILL_BATCH_SQL = """
WITH batch AS (
    SELECT id, source_id,
           CASE WHEN jsonb_typeof(config) = 'string'
                THEN (config #>> '{{}}')::jsonb
                ELSE config END AS norm_config
    FROM usage_apicalllog
    WHERE id > %s
      AND deleted = false
      AND (
        (jsonb_typeof(config) = 'string'
         AND LEFT(config #>> '{{}}', 1) IN ('{{', '[')
         {unwrap_source_filter})
        OR
        (source_id = ANY(%s)
         AND jsonb_typeof(config) = 'object'
         AND COALESCE(config ->> 'version_id', '') = '')
      )
    ORDER BY id
    LIMIT %s
)
UPDATE usage_apicalllog AS l
SET config = CASE
    WHEN jsonb_typeof(b.norm_config) = 'object'
         AND COALESCE(b.norm_config ->> 'version_id', '') = ''
         AND m.version_id IS NOT NULL
    THEN b.norm_config || jsonb_build_object(
             'version_id', m.version_id,
             'version_number', m.version_number)
    ELSE b.norm_config
END
FROM batch AS b
LEFT JOIN unnest(%s::text[], %s::text[], %s::int[]) AS m(source_id, version_id, version_number)
    ON b.source_id = m.source_id
WHERE l.id = b.id
RETURNING l.id
"""


def _ensure_default_version(template):
    """Return the template's default version, creating v1 if none exists."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    version = EvalTemplateVersion.objects.get_default(template)
    if version:
        return version
    try:
        return EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot=template.config or {},
            criteria=template.criteria or "",
            model=template.model or "",
            organization=template.organization,
            workspace=template.workspace,
        )
    except Exception:
        logger.warning(
            "ensure_default_version_failed",
            template_id=str(template.id),
            exc_info=True,
        )
        return None


def _build_version_mapping(
    only_template: Optional[str] = None,
    emit: Optional[Callable[[str], None]] = None,
) -> dict:
    """Map template id (str) -> (version_id, version_number), creating missing v1s."""
    from model_hub.models.evals_metric import EvalTemplate

    emit = emit or (lambda _msg: None)
    templates = EvalTemplate.objects.filter(deleted=False, owner="user")
    if only_template:
        templates = templates.filter(id=only_template)

    mapping = {}
    v1_created = 0
    v1_failed = 0
    for template in templates.iterator():
        version = _ensure_default_version(template)
        if version:
            mapping[str(template.id)] = (str(version.id), version.version_number)
            if version.version_number == 1:
                v1_created += 1
        else:
            v1_failed += 1
            emit(
                f"  WARNING: failed to create v1 for template {template.id} "
                f"— its rows will NOT be stamped"
            )

    if v1_created:
        emit(f"  v1 versions created: {v1_created}")
    if v1_failed:
        emit(f"  WARNING: v1 creation failed for {v1_failed} templates")
    return mapping


def _backfill_pass_sql(
    connection,
    mapping: dict,
    batch: int,
    sleep_s: float,
    emit: Callable[[str], None],
    scoped: bool = False,
) -> int:
    """One keyset-paginated pass: unwrap + stamp together, all server-side."""
    source_ids = list(mapping.keys())
    source_id_arr = source_ids or [None]
    version_id_arr = [mapping[s][0] for s in source_ids] if source_ids else [None]
    version_num_arr = [mapping[s][1] for s in source_ids] if source_ids else [None]

    unwrap_source_filter = (
        "AND source_id = ANY(%s)" if scoped else ""
    )
    sql = _BACKFILL_BATCH_SQL.format(unwrap_source_filter=unwrap_source_filter)

    total = 0
    last_id = 0
    while True:
        with connection.cursor() as cursor:
            params = [last_id]
            if scoped:
                params.append(source_ids)
            params.extend([source_ids, batch, source_id_arr, version_id_arr, version_num_arr])
            cursor.execute(sql, params)
            ids = [row[0] for row in cursor.fetchall()]
        if not ids:
            break
        last_id = max(ids)
        total += len(ids)
        emit(f"  processed batch of {len(ids)} (total={total}, last_id={last_id})")
        if sleep_s:
            time.sleep(sleep_s)
    return total


def backfill_usage_logs(
    chunk_size: int = 5000,
    sleep_s: float = 0.0,
    only_template: Optional[str] = None,
    database: str = "default_direct",
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """Unwrap double-encoded configs and stamp untagged APICallLog rows with
    their template's default version — one combined pass.

    Idempotent — re-runs (or interrupted runs resumed) only touch rows still
    needing work. Safe to call from the command or a shell.
    """
    emit = log or (lambda _msg: None)

    try:
        from ee.usage.models.usage import APICallLog  # noqa: F401
    except ImportError:
        emit("usage app not installed (OSS build) — nothing to backfill.")
        return {"updated": 0}

    from django.db import connections

    connection = connections[database]
    mapping = _build_version_mapping(only_template, emit=emit)
    emit(f"Templates to stamp: {len(mapping)}")

    if not mapping and not only_template:
        emit("No user templates found — nothing to do.")
        return {"updated": 0}

    total = _backfill_pass_sql(
        connection, mapping, chunk_size, sleep_s, emit, scoped=bool(only_template)
    )

    emit(f"Backfill complete: rows updated={total}")
    return {"updated": total}


class Command(BaseCommand):
    help = (
        "Backfill APICallLog.config.version_id for user eval templates. "
        "Run once, on a single pod, after deploy — not part of any migration."
    )

    def add_arguments(self, parser):
        parser.add_argument("--chunk-size", type=int, default=5000)
        parser.add_argument("--sleep", type=float, default=0.0)
        parser.add_argument("--template-id", type=str, default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--database",
            type=str,
            default="default_direct",
            help="Django DB alias (default: default_direct — bypasses PgBouncer).",
        )

    def handle(self, *args, **opts):
        if opts["dry_run"]:
            self._dry_run(opts["template_id"], opts["database"])
            return
        backfill_usage_logs(
            chunk_size=opts["chunk_size"],
            sleep_s=opts["sleep"],
            only_template=opts["template_id"],
            database=opts["database"],
            log=self.stdout.write,
        )

    def _dry_run(self, template_id, database):
        """Report scope without writing anything (no v1 creation either)."""
        from model_hub.models.evals_metric import EvalTemplate

        try:
            from ee.usage.models.usage import APICallLog  # noqa: F401
        except ImportError:
            self.stdout.write("usage app not installed (OSS build) — nothing to do.")
            return

        from django.db import connections

        connection = connections[database]
        templates = EvalTemplate.objects.filter(deleted=False, owner="user")
        if template_id:
            templates = templates.filter(id=template_id)
        source_ids = [str(pk) for pk in templates.values_list("id", flat=True)]
        self.stdout.write(f"Templates to scan: {len(source_ids)}")

        with connection.cursor() as cursor:
            if template_id and source_ids:
                cursor.execute(
                    "SELECT count(*) FROM usage_apicalllog "
                    "WHERE deleted = false AND source_id = ANY(%s) "
                    "  AND jsonb_typeof(config) = 'string' "
                    "  AND LEFT(config #>> '{}', 1) IN ('{', '[')",
                    [source_ids],
                )
            else:
                cursor.execute(
                    "SELECT count(*) FROM usage_apicalllog "
                    "WHERE deleted = false AND jsonb_typeof(config) = 'string' "
                    "  AND LEFT(config #>> '{}', 1) IN ('{', '[')"
                )
            self.stdout.write(f"Double-encoded configs to unwrap: {cursor.fetchone()[0]}")
            if source_ids:
                cursor.execute(
                    "SELECT count(*) FROM usage_apicalllog "
                    "WHERE deleted = false AND source_id = ANY(%s) "
                    "  AND jsonb_typeof(config) = 'object' "
                    "  AND COALESCE(config ->> 'version_id', '') = ''",
                    [source_ids],
                )
                self.stdout.write(
                    f"Rows pending version stamp: {cursor.fetchone()[0]}"
                )
