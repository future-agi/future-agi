// W17 shared checks. Source: ec94c6dba4 integrations/models, serializers and
// views/integration_connection.py; frontend/api/integrations/useSyncCompletion.js.
import { expect, type Page } from '@playwright/test';
import type { TestActor } from '../provisioning';
import type { StateProbe } from '../state-probe';
import { POLL } from '../state-probe';
import { CONNECTIONS_PATH, SYNC_LOGS_PATH, INTEGRATIONS_ROUTE, finishedSyncs, expectedRequestIds,
  waitOutSyncCooldown, pressSync, syncAfter, syncLogsApi } from '../integration-export';
import { WebhookSink } from '../sinks';
import { StorageSink } from '../export-sinks';
import { UI_READY, deliveries, fingerprint, setDestinationFailure, type Destination } from './export-destinations';
import type { WalkReceipt } from './receipt';

// c2.6 shared StatusBadge renders display labels, not raw API status strings.
const historyStatusLabel = (status: string) =>
  ({ success: 'Success', failed: 'Failed' } as Record<string, string>)[status] ?? status;

export const connectionRows = (probe: StateProbe, org: string) => probe.pg(
  `SELECT id::text, status, host_url, sync_interval_seconds, deleted, md5(encrypted_credentials) AS stored
   FROM integrations_connection WHERE organization_id = $1 ORDER BY id`, [org]);
export const orgExportSnapshot = async (probe: StateProbe, org: string) => ({
  connections: await connectionRows(probe, org),
  syncs: await probe.pg(`SELECT row_to_json(s) AS row FROM integrations_sync_log s
    JOIN integrations_connection c ON s.connection_id = c.id WHERE c.organization_id = $1 ORDER BY s.id`, [org]),
});

/** A receipt row commits to queried values without putting those values in evidence. */
export function verified(receipt: WalkReceipt, feature: string, lane: 'storage' | 'api' | 'ui', value: unknown) {
  receipt.verdict({ feature, lane, where: 'W17 export lifecycle', verdict: 'RECORDED',
    detail: { result: 'pass', queriedDigest: fingerprint(value) } });
}

export async function assertDeliveryHistory(d: Destination, actor: TestActor, probe: StateProbe,
  sink: WebhookSink, storage: StorageSink, initialIds: string[], oldId: string) {
  const syncs = await finishedSyncs(probe, d.id!);
  const successful = syncs.filter(s => s.status === 'success');
  expect(successful.length).toBeGreaterThan(0);
  const expected: string[] = [];
  for (const sync of successful) {
    const ids = await expectedRequestIds(probe, actor, sync);
    expect(ids.includes(oldId), 'the 20-hour-old decoy must never enter a window').toBe(false);
    expect(sync.traces_fetched).toBe(ids.length);
    expected.push(...ids);
  }
  // An independent minted anchor prevents a vacuous SQL-window/sink agreement.
  expect(await expectedRequestIds(probe, actor, successful[0])).toEqual([...initialIds].sort());
  expect(await deliveries(d, sink, storage)).toEqual(expected.sort());
  // export_integration_to_service (activities.py:549-579) increments the
  // connection's request-log delivery total. total_spans_synced belongs to
  // imports; per-sync spans_synced instead stores this export's metric count.
  expect(await probe.pg(`SELECT total_traces_synced AS logs, total_spans_synced AS records
    FROM integrations_connection WHERE id = $1`, [d.id])).toEqual([{ logs: expected.length, records: 0 }]);
  return { syncs, deliveryIds: expected };
}

export async function assertHistoryPage(page: Page, d: Destination, actor: TestActor, probe: StateProbe) {
  await page.goto(`${INTEGRATIONS_ROUTE}/${d.id}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByText('Sync History', { exact: true })).toBeVisible({ timeout: UI_READY });
  const stored = (await finishedSyncs(probe, d.id!)).reverse();
  const api = await syncLogsApi(actor, d.id!);
  expect(api.map(s => [s.id, s.status, s.traces_fetched, s.error_details]))
    .toEqual(stored.map(s => [s.id, s.status, s.traces_fetched, s.error_details]));
  await expect(page.locator('.ag-row [col-id="status"]')).toHaveText(stored.map(s => historyStatusLabel(s.status)), { timeout: UI_READY });
  await expect(page.locator('.ag-row [col-id="traces_fetched"]'))
    .toHaveText(stored.map(s => s.traces_fetched.toLocaleString('en-US')), { timeout: UI_READY });
  const total = stored.filter(s => s.status === 'success').reduce((n, s) => n + s.traces_fetched, 0);
  const count = page.locator('.MuiGrid-item').filter({ has: page.getByText('Request Log Deliveries (Including Repeats)', { exact: true }) });
  await expect(count.getByText(total.toLocaleString('en-US'), { exact: true })).toBeVisible({ timeout: UI_READY });
  return stored;
}

/** Hold a REAL preparation GET, never fabricate its answer. React Router's
 * unmount is asynchronous, so same-turn DOM clicks do not prove cancellation
 * before GET begins. Navigate while preparation is held, then release it:
 * exactly one preparation, zero POSTs, rows and deliveries must result. */
export async function pendingPreparation(page: Page, d: Destination, probe: StateProbe,
  sink: WebhookSink, storage: StorageSink, mode: 'double' | 'cancel') {
  await waitOutSyncCooldown(probe, d.id!);
  await page.goto(`${INTEGRATIONS_ROUTE}/${d.id}`, { waitUntil: 'domcontentloaded' });
  const button = page.getByRole('button', { name: 'Sync Now', exact: true });
  await expect(button).toBeEnabled({ timeout: UI_READY });
  const before = await finishedSyncs(probe, d.id!);
  await expect(page.locator('.ag-row [col-id="status"]')).toHaveText(before.map(s => historyStatusLabel(s.status)).reverse(), { timeout: UI_READY });
  const beforeDelivery = await deliveries(d, sink, storage);
  let posts = 0; let preparations = 0;
  const listener = (r: { method(): string; url(): string }) => {
    const url = new URL(r.url());
    if (r.method() === 'POST' && url.pathname === `${CONNECTIONS_PATH}${d.id}/sync_now/`) posts++;
    if (r.method() === 'GET' && url.pathname === SYNC_LOGS_PATH && url.searchParams.get('connection_id') === d.id) preparations++;
  };
  page.on('request', listener);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void;
  const blocked = new Promise<void>(resolve => { entered = resolve; });
  const pattern = `**${SYNC_LOGS_PATH}*`;
  await page.route(pattern, async route => { entered(); await gate; await route.continue(); });
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    if (mode === 'double') await button.dblclick();
    else await button.click();
    await Promise.race([blocked, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('sync preparation GET never started')), UI_READY);
    })]);
    expect(posts).toBe(0);
    if (mode === 'cancel') {
      await page.getByRole('button', { name: 'Back to Integrations' }).click();
      await expect(page).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/?$`), { timeout: UI_READY });
      release();
      await page.waitForTimeout(3000); // bounded negative observation after the pending GET is released
      expect(preparations).toBe(1);
      expect(posts).toBe(0);
      expect(await finishedSyncs(probe, d.id!)).toEqual(before);
      expect(await deliveries(d, sink, storage)).toEqual(beforeDelivery);
      return { mode, preparations, posts, rows: 0 };
    }
    release();
    await expect.poll(async () => (await finishedSyncs(probe, d.id!)).length, POLL.ASYNC_JOB).toBe(before.length + 1);
    expect(posts).toBe(1);
    expect((await finishedSyncs(probe, d.id!)).map(s => s.status)).toEqual([...before.map(s => s.status), 'success']);
    return { mode, posts, rows: (await finishedSyncs(probe, d.id!)).length - before.length };
  } finally {
    clearTimeout(timer); release(); page.off('request', listener); await page.unroute(pattern);
  }
}

/** Admin recovery only: the menu button and connection badge share a Box.
 * IntegrationDetail.jsx:203-213; IntegrationAdminOnly.jsx:8-9 at c2.6.
 * The separate Sync History panel (IntegrationDetail.jsx:414-430) is excluded. */
export function connectionErrorStatus(page: Page) {
  return page.getByRole('button', { name: 'Integration options menu', exact: true })
    .locator('..').getByText('Error', { exact: true });
}

/** Real failures at each input sink, without intercepted product answers. */
export async function recoverDestination(page: Page, d: Destination, probe: StateProbe, sink: WebhookSink) {
  const first = (await finishedSyncs(probe, d.id!)).at(-1)!;
  const toggle = (down: boolean) => setDestinationFailure(d, sink, down);
  await toggle(true);
  try {
    for (const name of ['Sync Now', 'Retry'] as const) {
      await waitOutSyncCooldown(probe, d.id!);
      await page.goto(`${INTEGRATIONS_ROUTE}/${d.id}`, { waitUntil: 'domcontentloaded' });
      const since = await pressSync(page, d.id!, name, UI_READY);
      const failed = await syncAfter(probe, d.id!, since, 'failed');
      if (d.kind === 'datadog') expect(failed.error_details).toEqual({ kind: 'credentials_refused',
        type: 'HTTPError', http_status: 403, vendor_code: null });
      // Token-only Mixpanel uses /track: HTTP 200 with body 0 is a refusal,
      // classified by export_failures.py as MixpanelRejected, not HTTPError.
      else if (d.kind === 'mixpanel') expect(failed.error_details).toEqual({ kind: 'events_refused',
        type: 'MixpanelRejected', http_status: null, vendor_code: null });
      else if (d.kind === 's3') expect(failed.error_details).toEqual({ kind: 'credentials_refused',
        type: 'ClientError', http_status: 403, vendor_code: 'InvalidAccessKeyId' });
      else expect(failed.error_message).toBe('Could not reach PostHog. It is retried automatically; Retry tries again now.');
      expect(await probe.pg('SELECT status FROM integrations_connection WHERE id = $1', [d.id]))
        .toEqual([{ status: 'error' }]);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(connectionErrorStatus(page)).toHaveCount(1, { timeout: UI_READY });
      await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled({ timeout: UI_READY });
    }
    await toggle(false);
    await waitOutSyncCooldown(probe, d.id!);
    await page.goto(`${INTEGRATIONS_ROUTE}/${d.id}`, { waitUntil: 'domcontentloaded' });
    const since = await pressSync(page, d.id!, 'Retry', UI_READY);
    await syncAfter(probe, d.id!, since, 'success');
    const rows = (await finishedSyncs(probe, d.id!)).filter(s => Date.parse(s.started) >= Date.parse(first.started));
    expect(rows.map(s => s.status)).toEqual(['success', 'failed', 'failed', 'success']);
    expect(await probe.pg(`SELECT status, COALESCE(status_message, '') AS reason FROM integrations_connection WHERE id = $1`, [d.id]))
      .toEqual([{ status: 'active', reason: '' }]);
    return rows;
  } finally { await toggle(false); }
}
