import { isDeepStrictEqual } from 'node:util';
import { expect } from '@playwright/test';
import type { StateProbe } from '../../../lib/state-probe';
import { requireCohort, requireId, requireUuid } from '../_shared/identifiers';
import { readExportSyncs, type ExportSyncRecord } from '../_shared/postgres-exports';

export type ExportProbe = Pick<StateProbe, 'pg'>;

/** Source pins: integrations/models/{integration_connection,sync_log}.py;
 * integrations/temporal/activities.py:_get_export_queryset,
 * _scope_to_connection_workspace, _export_datadog and DATADOG_LOG_WINDOW.
 * Read ALL request IDs in this connection's actual stored window, not just a
 * supplied expected cohort. Keep the one-minute overlap across successful runs.
 * Datadog clips to 18 hours minus one minute; timestamps stay in PG (no JS
 * millisecond truncation at the half-open window boundary).
 * A live sink still needs accepted safe projection/pagination (J15-O09).
 */
export async function readExportWindowIds(
  probe: ExportProbe, organizationId: string, connectionId: string, syncId: string,
): Promise<string[]> {
  const args = [requireUuid(organizationId), requireUuid(connectionId), requireUuid(syncId)];
  const binding = await probe.pg<{ window_valid: boolean; platform: string }>(`
    SELECT (s.sync_from IS NOT NULL AND s.sync_to IS NOT NULL
            AND s.sync_from < s.sync_to) AS window_valid, c.platform
    FROM integrations_sync_log s JOIN integrations_connection c ON c.id = s.connection_id
    WHERE c.organization_id = $1 AND c.id = $2 AND s.id = $3`, args);
  if (binding.length !== 1 || binding[0].window_valid !== true ||
      !['datadog', 'mixpanel', 'posthog', 'cloud_storage'].includes(binding[0].platform)) {
    throw new Error('Export window is missing, invalid or outside the W17 platforms');
  }
  const rows = await probe.pg<{ request_id: string }>(`
    WITH scope AS (
      SELECT c.organization_id, c.workspace_id, cw.is_default,
        CASE WHEN c.platform = 'datadog'
          THEN GREATEST(s.sync_from, s.sync_to - interval '17 hours 59 minutes')
          ELSE s.sync_from END AS window_from, s.sync_to AS window_to
      FROM integrations_sync_log s JOIN integrations_connection c ON c.id = s.connection_id
      LEFT JOIN accounts_workspace cw ON cw.id = c.workspace_id
      WHERE c.organization_id = $1 AND c.id = $2 AND s.id = $3
    )
    SELECT l.request_id FROM scope c
    JOIN agentcc_request_log l ON l.organization_id = c.organization_id
    LEFT JOIN accounts_workspace lw ON lw.id = l.workspace_id
    WHERE l.deleted = false
      AND (c.workspace_id IS NULL OR l.workspace_id = c.workspace_id
        OR (c.is_default = true AND (l.workspace_id IS NULL
          OR (lw.is_default = true AND lw.organization_id = c.organization_id))))
      AND ((l.started_at >= c.window_from AND l.started_at < c.window_to)
        OR (l.started_at IS NULL AND l.created_at >= c.window_from AND l.created_at < c.window_to))
    ORDER BY l.request_id, l.id`, args);
  return rows.map(row => requireId(row.request_id));
}

/** A detector shared by the future live sink check and its data-only controls.
 * Input is IDs projected by an accepted sink reader, never raw bodies/headers.
 * expectedPerSuccessfulRun is independently read with readExportWindowIds and
 * MUST be joined to the exact nonempty successful-sync inventory first. Repeated
 * IDs from overlapping runs are retained; neither side is Set-deduplicated.
 * This proves ID multiplicities only, not payload field equality or sink delivery.
 */
export function assertExactExportIds(
  observedIds: readonly string[], expectedPerSuccessfulRun: readonly (readonly string[])[],
): void {
  if (!expectedPerSuccessfulRun.length) throw new Error('Expected successful export runs are required');
  const expected = expectedPerSuccessfulRun.flatMap(ids => requireCohort(ids)).sort();
  const actual = observedIds.map(requireId).sort();
  expect(isDeepStrictEqual(actual, expected), 'W17.A03 EXACT_EXPORT_ID_MULTISET').toBe(true);
}

/** Read the entire sync history, including incomplete/deleted/extra rows. Never
 * filter to four expected IDs or completed rows: that hides duplicate dispatch.
 * POLL.ASYNC_JOB is the caller's budget for a newly dispatched sync to finish;
 * it is not a cancellation barrier or a quiescence guarantee.
 */
export const readAllExportRuns = readExportSyncs;

export interface RecoveryCounts {
  traces_fetched: number;
  traces_created: number;
  traces_updated: number;
  spans_synced: number;
  scores_synced: number;
}

/** W17.A02 component. Values are source fields, NOT a guessed sink wire body.
 * Expected kinds are pinned to export_failures.py:KINDS by the chosen isolated
 * fault (unreachable for channel stop; credentials_refused for HTTP 401/403).
 * All five counters must match the independent destination-specific expectation.
 * Live outage/retry UI, role fixtures and safe diagnostics remain J15-O09/O02.
 */
export function assertRecoveryHistory(
  rows: readonly ExportSyncRecord[], connectionId: string,
  expectedCounts: readonly RecoveryCounts[],
  failureKind: 'unreachable' | 'credentials_refused',
): void {
  requireUuid(connectionId);
  if (expectedCounts.length !== 4 || expectedCounts.some(counts =>
    Object.keys(counts).sort().join(',') !== 'scores_synced,spans_synced,traces_created,traces_fetched,traces_updated' ||
    Object.values(counts).some(n => !Number.isSafeInteger(n) || n < 0))) {
    throw new Error('Four explicit complete export counter expectations are required');
  }
  if (!['unreachable', 'credentials_refused'].includes(failureKind)) {
    throw new Error('Unsupported recovery fault kind');
  }
  expect(isDeepStrictEqual(rows.map(row => row.status), ['success', 'failed', 'failed', 'success']),
    'W17.A02 EXACT_RECOVERY_STATUS_HISTORY').toBe(true);
  expect(new Set(rows.map(row => row.id)).size, 'W17.A02 UNIQUE_SYNC_ROWS').toBe(4);
  expect(rows.every(row => row.connection_id === connectionId && row.deleted === false &&
    typeof row.completed_at === 'string' && typeof row.sync_from === 'string' &&
    typeof row.sync_to === 'string' && Number.isFinite(Date.parse(row.started_at)) &&
    Number.isFinite(Date.parse(row.completed_at)) && Number.isFinite(Date.parse(row.sync_from)) &&
    Number.isFinite(Date.parse(row.sync_to)) && Date.parse(row.sync_from) < Date.parse(row.sync_to) &&
    Date.parse(row.completed_at) >= Date.parse(row.started_at)),
    'W17.A02 COMPLETED_SCOPED_HISTORY').toBe(true);
  expect(isDeepStrictEqual(rows.map(row => row.error_kind), [null, failureKind, failureKind, null]),
    'W17.A02 EXACT_RECOVERY_KINDS').toBe(true);
  const actualCounts = rows.map(row => ({ traces_fetched: row.traces_fetched,
    traces_created: row.traces_created, traces_updated: row.traces_updated,
    spans_synced: row.spans_synced, scores_synced: row.scores_synced }));
  expect(isDeepStrictEqual(actualCounts, expectedCounts), 'W17.A02 EXACT_RECOVERY_COUNTS').toBe(true);
}
