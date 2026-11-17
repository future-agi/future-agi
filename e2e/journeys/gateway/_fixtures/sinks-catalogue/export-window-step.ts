import { expect, type TestInfo } from '@playwright/test';
import { isDeepStrictEqual } from 'node:util';
import type { StateProbe } from '../../../../lib/state-probe';
import { POLL } from '../../../../lib/state-probe';
import { attachSeededIds } from '../../_shared/contract';
import { requireCohort, requireUuid } from '../../_shared/identifiers';
import { readExportConnections, readExportSyncs } from '../../_shared/postgres-exports';
import { assertExactExportIds, readExportWindowIds } from '../../J15/export-state';
import { fragmentStep } from '../../J15/fragments';
import { readStorageExports, readWebhookExports, type BoundStorageReader, type WebhookBinding } from './export-readers';

// Callable consumer-extension step, NOT an alternate runner or an admitted E6
// binding. Accepted E6 fixtures must provide independent fresh seeded identities,
// an ID-bound storage reader and a quiesced, fixed-window Distributed stack.
// It deliberately emits only FRAGMENT records until those bindings are qualified.
export type Destination = 'datadog' | 'mixpanel' | 'posthog' | 'cloud_storage';
export interface ExportWindowBinding {
  probe: Pick<StateProbe, 'pg'>;
  organizationA: string; organizationB: string;
  currentIds: readonly string[]; oldId: string; foreignIds: readonly string[];
  webhookReadUrl: string; storage: BoundStorageReader; storagePrefix: string;
  destinations: {
    platform: Destination; connectionId: string;
    // Complete independent dispatch inventory, including failed runs. Nothing
    // is filtered to expected success IDs before checking for extra dispatches.
    expectedRuns: readonly { id: string; status: string }[];
    webhook?: WebhookBinding;
  }[];
}
const DESTINATIONS: Destination[] = ['cloud_storage', 'datadog', 'mixpanel', 'posthog'];

export async function checkExportWindowStep(info: TestInfo, binding: ExportWindowBinding): Promise<void> {
  const { probe } = binding;
  requireUuid(binding.organizationA); requireUuid(binding.organizationB);
  if (binding.organizationA === binding.organizationB || binding.currentIds.length !== 3 || !binding.foreignIds.length) {
    throw new Error('EXPORT_WINDOW_SEED_BINDING');
  }
  requireCohort([...binding.currentIds, binding.oldId, ...binding.foreignIds]);
  if (!isDeepStrictEqual(binding.destinations.map(d => d.platform).sort(), DESTINATIONS)) {
    throw new Error('EXPORT_FOUR_DESTINATIONS_REQUIRED');
  }
  requireCohort(binding.destinations.map(d => requireUuid(d.connectionId)));
  await attachSeededIds(info, 'J15', {
    organizationIds: [binding.organizationA, binding.organizationB],
    requestIds: [...binding.currentIds, binding.oldId, ...binding.foreignIds],
    connectionIds: binding.destinations.map(d => d.connectionId),
    runIds: requireCohort(binding.destinations.flatMap(d => d.expectedRuns.map(r => requireUuid(r.id)))),
  });
  await fragmentStep(info, 'W17.A03', 'four-sink-fixed-window-exact-multiplicities', async () => {
    // Read source-pinned request IDs and org ownership, never bodies/headers.
    // da4c81cc62 futureagi/agentcc/models/request_log.py and activities.py.
    const records = await probe.pg<{ request_id: string; organization_id: string; deleted: boolean }>(
      `SELECT request_id, organization_id::text, deleted FROM agentcc_request_log
       WHERE request_id = ANY($1) AND organization_id = ANY($2::uuid[])
       ORDER BY request_id, organization_id`,
      [[...binding.currentIds, binding.oldId, ...binding.foreignIds], [binding.organizationA, binding.organizationB]]);
    const wanted = [...binding.currentIds, binding.oldId].map(request_id => ({ request_id,
      organization_id: binding.organizationA, deleted: false })).concat(binding.foreignIds.map(request_id => ({
        request_id, organization_id: binding.organizationB, deleted: false })));
    const order = (a: { request_id: string }, b: { request_id: string }) => a.request_id.localeCompare(b.request_id);
    expect(isDeepStrictEqual(records.sort(order), wanted.sort(order)), 'W17 seeded request ownership').toBe(true);
    const connections = await readExportConnections(probe, binding.organizationA);
    for (const destination of binding.destinations) {
      const connection = connections.filter(c => c.id === destination.connectionId);
      expect(connection.length, 'W17 connection exists in A').toBe(1);
      expect(connection[0].platform, 'W17 connection destination').toBe(destination.platform);
      expect(connection[0].deleted, 'W17 connection not deleted').toBe(false);
      const expectedRuns = destination.expectedRuns;
      requireCohort(expectedRuns.map(r => requireUuid(r.id)));
      if (!expectedRuns.some(r => r.status === 'success')) throw new Error('EXPORT_SUCCESS_INVENTORY_REQUIRED');
      const readRuns = () => readExportSyncs(probe, binding.organizationA, destination.connectionId);
      // Named for the real Temporal export completion, not a fixed sleep.
      const EXPORT_RUN_TERMINAL = POLL.ASYNC_JOB;
      await expect.poll(async () => {
        const rows = await readRuns();
        return isDeepStrictEqual(rows.map(r => ({ id: r.id, status: r.status })), expectedRuns) &&
          rows.every(r => r.completed_at !== null && r.deleted === false);
      }, { ...EXPORT_RUN_TERMINAL, message: 'W17 complete export-run terminal inventory' }).toBe(true);
      const runs = await readRuns();
      expect(isDeepStrictEqual(runs.map(r => ({ id: r.id, status: r.status })), expectedRuns),
        'W17 dispatch inventory remains exact').toBe(true);
      const windows: string[][] = [];
      for (const run of runs.filter(r => r.status === 'success')) {
        const ids = await readExportWindowIds(probe, binding.organizationA, destination.connectionId, run.id);
        expect(isDeepStrictEqual([...ids].sort(), [...binding.currentIds].sort()),
          'W17 exactly three current A records in every fixed window; no old/B').toBe(true);
        // Positive old-record witness: it really exists 20 hours before this
        // window ends, not just an invented excluded ID. Timestamp comparison
        // stays in Postgres (no JS truncation at the export boundary).
        const old = await probe.pg<{ is_old: boolean }>(`SELECT
          COALESCE(l.started_at, l.created_at) <= s.sync_to - interval '20 hours' AS is_old
          FROM agentcc_request_log l CROSS JOIN integrations_sync_log s
          JOIN integrations_connection c ON c.id = s.connection_id
          WHERE l.organization_id = $1 AND l.request_id = $2 AND s.id = $3
            AND c.id = $4 AND c.organization_id = $1`,
        [binding.organizationA, binding.oldId, run.id, destination.connectionId]);
        expect(old.length === 1 && old[0].is_old === true, 'W17 genuine old exclusion witness').toBe(true);
        windows.push(ids);
      }
      const delivered = destination.platform === 'cloud_storage'
        ? await readStorageExports(binding.storage, binding.storagePrefix)
        : await readWebhookExports(binding.webhookReadUrl, (() => {
          if (!destination.webhook || destination.webhook.vendor !== destination.platform) {
            throw new Error('EXPORT_WEBHOOK_BINDING_REQUIRED');
          }
          return destination.webhook;
        })());
      // No deduplication/intersection, including repeats across successful runs.
      assertExactExportIds(delivered.deliveredIds, windows);
      await info.attach('export-window-counts', { contentType: 'application/json', body: JSON.stringify({
        platform: destination.platform, connectionId: destination.connectionId,
        successfulRuns: windows.length, deliveries: delivered.deliveredIds.length,
        failedBatches: delivered.failedBatches, completeRow: false, walkedStatus: 'NOT_WALKED',
      }) });
    }
  });
}
