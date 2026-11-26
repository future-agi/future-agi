import { expect } from '@playwright/test';
import { POLL, type StateProbe } from './state-probe';

// oss_cdc_bootstrap.LANDING; the capture functions store TG_TABLE_NAME (no schema) and id::text.
const SCORE_TABLE = 'model_hub_score';

// Mirror capture_installed's database-wide trigger name OR function discovery for absence.
// Validate trigger shape and snapshot readiness only on the exact Score source.
const CAPTURE_SQL = `
  WITH wanted(name, event_type, new_table, old_table, fn) AS (VALUES
    ('fi_cdc_ins',   4, 'fi_cdc_new', NULL,         'fi_cdc_capture_new'),
    ('fi_cdc_upd',  16, 'fi_cdc_new', NULL,         'fi_cdc_capture_new'),
    ('fi_cdc_del',   8, NULL,         'fi_cdc_old', 'fi_cdc_capture_old'),
    ('fi_cdc_trunc',32, NULL,         NULL,         'fi_cdc_capture_truncate')
  ), discovered AS (
    SELECT t.*, p.proname, p.pronamespace
    FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
    WHERE NOT t.tgisinternal
      AND (t.tgname IN (SELECT name FROM wanted) OR p.proname IN (SELECT fn FROM wanted))
  ), observed AS (
    SELECT * FROM discovered WHERE tgrelid = to_regclass('public.' || $1::text)
  ), triggers AS (
    SELECT coalesce(w.name, t.tgname::text) AS name, CASE
      WHEN t.tgname IS NULL THEN 'missing'
      WHEN w.name IS NULL THEN 'unexpected'
      WHEN t.tgtype <> w.event_type OR t.proname <> w.fn OR t.pronamespace <> 'public'::regnamespace
        OR t.tgattr::text <> '' OR t.tgqual IS NOT NULL
        OR t.tgnewtable::text IS DISTINCT FROM w.new_table OR t.tgoldtable::text IS DISTINCT FROM w.old_table THEN 'wrong'
      WHEN t.tgenabled IN ('O', 'A') THEN 'ok' ELSE 'disabled' END AS status
    FROM wanted w FULL JOIN observed t ON t.tgname::text = w.name
  ), objects AS (
    SELECT coalesce(jsonb_object_agg(relname, relkind), '{}'::jsonb) AS tables
    FROM pg_catalog.pg_class WHERE relnamespace = 'public'::regnamespace
      AND relname IN ('fi_cdc_state', 'fi_cdc_outbox', 'fi_cdc_deadletter')
  )
  SELECT tables, EXISTS(SELECT 1 FROM discovered) AS capture_trigger_present,
    (SELECT jsonb_object_agg(name, status) FROM triggers) AS triggers,
    ARRAY(SELECT s.snapshot_completed_at FROM XMLTABLE('/*/row' PASSING
      CASE WHEN tables->>'fi_cdc_state' = 'r' THEN
        table_to_xml(to_regclass('public.fi_cdc_state'), false, false, '') END
      COLUMNS table_name text, snapshot_completed_at text) s WHERE s.table_name = $1) AS snapshots
  FROM objects`;

type Capture = { tables: Record<string, string>; capture_trigger_present: boolean;
  triggers: Record<string, string>; snapshots: (string | null)[] };
type Counts = { pending: string; parked: string }; // PG count(*) is int8, returned as text.

// drain_batch applies before deleting the exact outbox seqs. A deadletter remains parked
// even if requeued_at is set, until a successful apply actually removes it.
const COUNTS_SQL = `SELECT
  (SELECT count(*) FROM public.fi_cdc_outbox WHERE table_name = $1 AND pk = ANY($2::text[])) AS pending,
  (SELECT count(*) FROM public.fi_cdc_deadletter WHERE table_name = $1 AND pk = ANY($2::text[])) AS parked`;

/** Called inside the existing CDC_VISIBLE step, after native commits and before the first CH baseline read. */
export async function waitForScoreOutbox(probe: StateProbe, scoreIds: string[],
  attach: (name: string, value: unknown) => Promise<void>) {
  expect(scoreIds).toHaveLength(3);
  expect(new Set(scoreIds).size).toBe(3);
  // A static FROM on a missing registry fails at parse time, even inside CASE. table_to_xml
  // accepts a regclass, not query text; only the small state registry is read, never the queues.
  const [capture] = await probe.pg<Capture>(CAPTURE_SQL, [SCORE_TABLE]);
  await attach('score-capture-state', { table: SCORE_TABLE, scoreIds, ...capture });
  const statuses = Object.values(capture.triggers);
  if (!Object.keys(capture.tables).length && !capture.capture_trigger_present
    && statuses.every(s => s === 'missing') && !capture.snapshots.length) return;
  if (capture.tables.fi_cdc_state !== 'r' || capture.tables.fi_cdc_outbox !== 'r' || capture.tables.fi_cdc_deadletter !== 'r'
    || !statuses.every(s => s === 'ok') || capture.snapshots.length !== 1 || capture.snapshots[0] === null) {
    throw new Error(`Incomplete Score outbox capture: ${JSON.stringify(capture)}`);
  }

  let last: Counts | undefined;
  await expect.poll(async () => {
    const [counts] = await probe.pg<Counts>(COUNTS_SQL, [SCORE_TABLE, scoreIds]);
    if (!last || last.pending !== counts.pending || last.parked !== counts.parked) {
      // Publish transitions before waiting: the enclosing 180 s step may expire first.
      await attach('score-outbox-counts', { table: SCORE_TABLE, scoreIds, ...counts });
      last = counts;
    }
    if (counts.parked !== '0') throw new Error(`Parked Score outbox key(s): ${JSON.stringify({ scoreIds, ...counts })}`);
    return counts;
  }, { ...POLL.CDC_VISIBLE, message: `Waiting for the three committed Score keys to drain: ${scoreIds.join(', ')}` })
    .toEqual({ pending: '0', parked: '0' });
}
