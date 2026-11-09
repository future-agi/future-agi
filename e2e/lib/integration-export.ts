// The steps the integration export flows (flows/integrations/) share. The
// request logs are delivered on the gateway's own logs webhook, as GW-E2E-001
// does: it is the entry point the gateway posts each batch to
// (agentcc/views/webhook.py). The connection is made in the browser, through
// the Add Integration wizard; its first sync is the product's own
// (integrations/temporal/activities.py _sync_export_integration), dispatched
// by the create. What a sync must have sent is read independently from
// Postgres: the org's request logs in the sync's window, scoped as Request
// Logs scopes them in the connection's (default) workspace.
import { expect, request, type Page, type Response, type TestInfo } from '@playwright/test';
import { E2E } from './env';
import type { TestActor } from './provisioning';
import { POLL, type StateProbe } from './state-probe';

// Pinned from agentcc/views/webhook.py (GatewayWebhookView) and
// e2e/stack/e2e.env AGENTCC_WEBHOOK_SECRET; integrations/urls.py and
// frontend/src/utils/axios.js endpoints.integrations.
export const LOGS_WEBHOOK_PATH = '/agentcc/webhook/logs/';
const WEBHOOK_SECRET = process.env.AGENTCC_WEBHOOK_SECRET || 'e2e-agentcc-webhook-secret';
export const CONNECTIONS_PATH = '/integrations/connections/';
export const VALIDATE_PATH = '/integrations/connections/validate/';
export const SYNC_LOGS_PATH = '/integrations/sync-logs/';
export const INTEGRATIONS_ROUTE = '/dashboard/settings/integrations';
// How long a flow watches for a toast or request that must not follow an
// action (a refetch answers well within it on the stack).
export const TOAST_SETTLE_MS = 3_000;

/** Starts recording every toast the page shows (notistack's #notistack-snackbar
 * message), however briefly; returns a reader of the texts seen, in order. */
export async function recordToasts(page: Page): Promise<() => Promise<string[]>> {
  await page.evaluate(() => {
    const w = window as unknown as { __e2eToasts?: string[]; __e2eToastObserver?: MutationObserver };
    w.__e2eToastObserver?.disconnect();
    const seen = new Set<Element>();
    w.__e2eToasts = [];
    const scan = () => document.querySelectorAll('#notistack-snackbar').forEach((el) => {
      if (seen.has(el)) return;
      seen.add(el);
      w.__e2eToasts!.push((el.textContent || '').trim());
    });
    w.__e2eToastObserver = new MutationObserver(scan);
    w.__e2eToastObserver.observe(document.body, { childList: true, subtree: true });
    scan();
  });
  return () => page.evaluate(() => (window as unknown as { __e2eToasts?: string[] }).__e2eToasts ?? []);
}

/** One request log as the gateway delivers it (agentcc/services/log_ingestion.py _log_row). */
export interface MintedLog {
  request_id: string; model: string; provider: string; status_code: number;
  latency_ms: number; input_tokens: number; output_tokens: number; total_tokens: number;
  cost: string; timestamp: string; is_error?: boolean; error_message?: string;
}

/** Delivers `count` request logs under a fresh gateway key; the last one is a 500. With
 * `hoursAgo` they are dated that many hours back. */
export async function mintRequestLogs(actor: TestActor, probe: StateProbe, label: string, count = 3,
  hoursAgo = 0): Promise<MintedLog[]> {
  const created = await actor.api.post<{ result: { gateway_key_id: string } }>('/agentcc/api-keys/', { name: label });
  const keyId = created.result.gateway_key_id;
  const now = Date.now() - hoursAgo * 3600_000;
  const logs: MintedLog[] = Array.from({ length: count }, (_, i) => {
    const failed = i === count - 1;
    return {
      request_id: `${label}-${i + 1}`, model: 'gpt-4o-mini', provider: 'openai',
      status_code: failed ? 500 : 200, latency_ms: 100 + i, input_tokens: 10 + i, output_tokens: 20 + i,
      total_tokens: 30 + 2 * i, cost: `0.00${i + 1}000`, timestamp: new Date(now - (count - i) * 1000).toISOString(),
      ...(failed ? { is_error: true, error_message: `${label} upstream failed` } : {}),
    };
  });
  const req = await request.newContext();
  try {
    const res = await req.post(`${E2E.apiUrl}${LOGS_WEBHOOK_PATH}`, {
      headers: { 'X-Webhook-Secret': WEBHOOK_SECRET },
      data: { logs: logs.map((log) => ({ ...log, auth_key_id: keyId })) },
    });
    expect(res.status(), await res.text()).toBe(200);
  } finally {
    await req.dispose();
  }
  const stored = await probe.pg<{ request_id: string }>(
    'SELECT request_id FROM agentcc_request_log WHERE organization_id = $1 AND request_id = ANY($2) ORDER BY request_id',
    [actor.organizationId, logs.map((l) => l.request_id)]);
  expect(stored.map((r) => r.request_id)).toEqual(logs.map((l) => l.request_id).sort());
  return logs;
}

const isPost = (r: Response, path: string) =>
  r.request().method() === 'POST' && new URL(r.url()).pathname === path;

/** A full-page screenshot of the page as it stands, saved in the test's output
 * directory as <name>.png (the options walk reads every one). */
export async function shot(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
}

/** Opens the platform's card on the Integrations page: the wizard starts at its credentials. */
export async function openPlatform(page: Page, name: string, timeout: number) {
  await page.goto(INTEGRATIONS_ROUTE, { waitUntil: 'domcontentloaded' });
  await page.getByText(name, { exact: true }).click({ timeout });
  await expect(page.getByRole('button', { name: 'Validate & Continue' })).toBeVisible({ timeout });
}

/** Clicks Validate & Continue and returns the validate call's status. */
export async function validateCredentials(page: Page, timeout: number): Promise<number> {
  const validated = page.waitForResponse((r) => isPost(r, VALIDATE_PATH), { timeout });
  await page.getByRole('button', { name: 'Validate & Continue' }).click();
  return (await validated).status();
}

export interface ConnectOptions {
  /** The Sync Interval to pick (its label); the default "Every 5 minutes" otherwise. */
  interval?: string;
  /** "Export from a specific date" with this start (a datetime-local value); the default start otherwise. */
  fromDate?: string;
  /** The hours the default start exports: 24, or Datadog's 18 (its log intake keeps no older log). */
  recentHours?: 18 | 24;
  /** What the create must send: the destination as host_url and the connection's name. */
  sends?: { host_url: string; display_name: string };
}

/** A datetime-local value `hoursAgo` hours before now in the browser's own time zone,
 * with the instant it stands for (to the minute). */
export async function browserLocalTime(page: Page, hoursAgo: number): Promise<{ value: string; ms: number }> {
  return page.evaluate((h) => {
    const d = new Date(Date.now() - h * 3600_000);
    d.setSeconds(0, 0);
    const pad = (n: number) => String(n).padStart(2, '0');
    return {
      value: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`,
      ms: d.getTime(),
    };
  }, hoursAgo);
}

/** On the export sync settings: picks the interval and start asked for (by default
 * "Export the last 24 hours", Datadog's "Export the last 18 hours"), connects, and returns
 * the new connection's id. */
export async function connectExport(page: Page, timeout: number, options: ConnectOptions = {}): Promise<string> {
  const recent = `Export the last ${options.recentHours ?? 24} hours`;
  await expect(page.getByRole('radio', { name: new RegExp(recent) })).toBeChecked({ timeout });
  await expect(page.getByText('Then each new request log', { exact: true })).toBeVisible();
  if (options.interval) await page.locator('select').first().selectOption({ label: options.interval });
  if (options.fromDate) {
    await page.getByRole('radio', { name: 'Export from a specific date' }).check();
    await page.getByLabel('Start Date & Time').fill(options.fromDate);
  }
  const created = page.waitForResponse((r) => isPost(r, CONNECTIONS_PATH), { timeout });
  await page.getByRole('button', { name: 'Connect Integration' }).click();
  const res = await created;
  expect(res.status(), await res.text()).toBe(201);
  if (options.sends) expect(res.request().postDataJSON()).toMatchObject(options.sends);
  const id = ((await res.json()) as { result: { id: string } }).result.id;
  await expect(page.getByText('Integration Connected!')).toBeVisible({ timeout });
  await expect(page.getByText('Your integration is now active. Request logs will begin exporting shortly.'))
    .toBeVisible({ timeout });
  return id;
}

/** Leaves the wizard's "Integration Connected!" state by one of its two buttons:
 * View Integration opens the connection's page, Done closes the drawer on Integrations. */
export async function leaveConnected(page: Page, connectionId: string, via: 'View Integration' | 'Done',
  timeout: number) {
  const buttons = page.getByRole('button', { name: /^(View Integration|Done)$/ });
  await expect(buttons).toHaveText(['View Integration', 'Done']);
  await page.getByRole('button', { name: via, exact: true }).click();
  await expect(page.getByText('Integration Connected!')).toHaveCount(0, { timeout });
  await expect(page).toHaveURL(via === 'Done'
    ? new RegExp(`${INTEGRATIONS_ROUTE}/?$`)
    : new RegExp(`${INTEGRATIONS_ROUTE}/${connectionId}$`), { timeout });
}

export interface SyncRow {
  status: string; traces_fetched: number; spans_synced: number;
  sync_from: string; sync_to: string; error_message: string;
}

/** The connection's first finished sync (PG integrations_sync_log), waited on as the Temporal job it is. */
export async function firstSync(probe: StateProbe, connectionId: string): Promise<SyncRow> {
  const read = async () => (await probe.pg<SyncRow>(
    `SELECT status, traces_fetched, spans_synced, sync_from::text AS sync_from, sync_to::text AS sync_to,
            COALESCE(error_message, '') AS error_message FROM integrations_sync_log
      WHERE connection_id = $1 AND completed_at IS NOT NULL ORDER BY started_at LIMIT 1`, [connectionId]))[0];
  await expect.poll(async () => (await read())?.status ?? 'pending', POLL.ASYNC_JOB).not.toBe('pending');
  return read();
}

/** The request ids the sync must have sent: the org's logs in its window, as Request Logs
 * shows them in the actor's default workspace (no workspace, the default workspace's own),
 * in JavaScript's sort order, the order the flows sort what a sink received in (Postgres
 * collation orders "ph2-" before "ph-"). */
export async function expectedRequestIds(probe: StateProbe, actor: TestActor, sync: SyncRow): Promise<string[]> {
  const rows = await probe.pg<{ request_id: string }>(
    `SELECT l.request_id FROM agentcc_request_log l LEFT JOIN accounts_workspace w ON w.id = l.workspace_id
      WHERE l.organization_id = $1 AND l.deleted = false
        AND (l.workspace_id IS NULL OR l.workspace_id = $2 OR (w.is_default AND w.organization_id = $1))
        AND ((l.started_at >= $3::timestamptz AND l.started_at < $4::timestamptz)
          OR (l.started_at IS NULL AND l.created_at >= $3::timestamptz AND l.created_at < $4::timestamptz))
      `, [actor.organizationId, actor.workspaceId, sync.sync_from, sync.sync_to]);
  return rows.map((r) => r.request_id).sort();
}

/** The connection's page: its status, and the one Sync History row with the count it exported. */
export async function expectSyncHistory(page: Page, connectionId: string, exported: number, timeout: number) {
  await page.goto(`${INTEGRATIONS_ROUTE}/${connectionId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByText('Sync History')).toBeVisible({ timeout });
  await expect(page.getByRole('columnheader', { name: 'Request logs' })).toBeVisible({ timeout });
  await expect(page.locator('.ag-row [col-id="traces_fetched"]')).toHaveText([exported.toLocaleString('en-US')], { timeout });
  await expect(page.locator('.ag-row [col-id="status"]')).toHaveText(['success'], { timeout });
}

/** An InfoRow of the connection page (label over value), by its label. */
const infoRow = (page: Page, label: string) =>
  page.locator('.MuiGrid-item').filter({ has: page.getByText(label, { exact: true }) });

export interface ShownConnection {
  /** The connection's name (card title and page header). */
  name: string;
  /** Where it delivers, exactly as the card and the header read it. */
  destination: string;
  /** The platform's name on the connection page. */
  platform: string;
  /** The request logs it has exported, as the card and the page count them. */
  exported: number;
  /** The Sync Interval row ("5 min"). */
  interval: string;
}

/** From the connection's page, Back to Integrations; then the Connections card and the
 * connection page, read for exactly what they say: a placeholder host or a raw platform id
 * fails them. */
export async function expectConnectionShown(page: Page, testInfo: TestInfo, connectionId: string,
  shown: ShownConnection, timeout: number) {
  const count = `${shown.exported.toLocaleString('en-US')} request log deliveries (including repeats)`;
  await expect(page).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/${connectionId}$`));
  await page.getByRole('button', { name: 'Back to Integrations' }).click();
  await expect(page).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/?$`), { timeout });
  const card = page.locator('.MuiCard-root').filter({
    has: page.getByTestId('integration-card-destination').filter({ hasText: shown.destination }),
  });
  await expect(card).toHaveCount(1, { timeout });
  await expect(card.getByTestId('integration-card-destination')).toHaveText(shown.destination);
  await expect(card.getByText(shown.name, { exact: true })).toBeVisible();
  await expect(card.getByText(count, { exact: true })).toBeVisible();
  await expect(card.getByText(/^Synced /)).toBeVisible();
  await shot(page, testInfo, 'connections-card');
  await card.click();
  await expect(page).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/${connectionId}$`), { timeout });
  await expect(page.getByTestId('integration-header-destination')).toHaveText(shown.destination, { timeout });
  await expect(page.getByTestId('integration-header-destination').locator('xpath=preceding-sibling::*[1]'))
    .toHaveText(shown.name);
  await expect(infoRow(page, 'Platform').getByText(shown.platform, { exact: true })).toBeVisible();
  await expect(infoRow(page, 'Sync Interval').getByText(shown.interval, { exact: true })).toBeVisible();
  await expect(infoRow(page, 'Request Log Deliveries (Including Repeats)')
    .getByText(shown.exported.toLocaleString('en-US'), { exact: true })).toBeVisible();
  await shot(page, testInfo, 'connection-page');
}

/** Deletes the connection from its page: Delete, Cancel (nothing is deleted), Delete again,
 * type DELETE, Delete. The page returns to Integrations without its card and one toast,
 * "Integration deleted", and the connection is gone from the API and soft-deleted in PG. */
export async function deleteConnection(page: Page, testInfo: TestInfo, actor: TestActor, probe: StateProbe,
  connectionId: string, shown: Pick<ShownConnection, 'name' | 'destination'>, timeout: number) {
  const isDeleted = async () => (await probe.pg<{ deleted: boolean }>(
    'SELECT deleted FROM integrations_connection WHERE id = $1', [connectionId]))[0]?.deleted;
  await page.goto(`${INTEGRATIONS_ROUTE}/${connectionId}`, { waitUntil: 'domcontentloaded' });
  const openDelete = async () => {
    await page.getByRole('button', { name: 'Integration options menu' }).click({ timeout });
    await expect(page.getByRole('menuitem')).toHaveText(['Edit', 'Delete']);
    await page.getByRole('menuitem', { name: 'Delete' }).click();
  };
  const dialog = page.getByRole('dialog', { name: 'Delete Integration' });
  await openDelete();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toBeHidden({ timeout });
  expect(await isDeleted()).toBe(false);
  await openDelete();
  await expect(dialog.locator('p').first()).toHaveText(
    `Are you sure you want to delete the connection to ${shown.name}? This stops exporting request logs and ` +
    'removes the connection. Request logs already exported stay where they were sent.');
  const remove = dialog.getByRole('button', { name: 'Delete' });
  await expect(remove).toBeDisabled();
  await dialog.getByPlaceholder('Type "DELETE" to confirm').fill('DELETE');
  await shot(page, testInfo, 'delete-dialog');
  const deleted = page.waitForResponse((r) => r.request().method() === 'DELETE'
    && new URL(r.url()).pathname === `${CONNECTIONS_PATH}${connectionId}/`, { timeout });
  // After the Delete, the deleted connection's own page is never read again
  // (it would answer 404), and the one toast is "Integration deleted"
  // (frontend/src/api/integrations useDeleteConnection).
  const toasts = await recordToasts(page);
  const reads: string[] = [];
  const read = (r: { method(): string; url(): string }) => {
    if (r.method() === 'GET' && new URL(r.url()).pathname === `${CONNECTIONS_PATH}${connectionId}/`) reads.push(r.url());
  };
  await remove.click();
  expect((await deleted).status()).toBe(200);
  page.on('request', read);
  await expect(page).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/?$`), { timeout });
  await expect(page.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout });
  await page.waitForTimeout(TOAST_SETTLE_MS);
  page.off('request', read);
  expect(reads).toEqual([]);
  expect(await toasts()).toEqual(['Integration deleted']);
  await expect(page.getByTestId('integration-card-destination').filter({ hasText: shown.destination }))
    .toHaveCount(0);
  await shot(page, testInfo, 'after-delete');
  const listed = await actor.api.get<{ result: { connections: { id: string }[] } }>(CONNECTIONS_PATH);
  expect(listed.result.connections.map((c) => c.id)).not.toContain(connectionId);
  expect(await probe.pg<{ deleted: boolean }>('SELECT deleted FROM integrations_connection WHERE id = $1',
    [connectionId])).toEqual([{ deleted: true }]);
}

/** The sync-logs list the page reads, and the connection's own state, for the same connection. */
export async function expectSyncApi(actor: TestActor, connectionId: string, exported: number) {
  const logs = await actor.api.get<{ result: { sync_logs: { status: string; traces_fetched: number }[] } }>(
    SYNC_LOGS_PATH, { connection_id: connectionId });
  expect(logs.result.sync_logs.map((l) => [l.status, l.traces_fetched])).toEqual([['success', exported]]);
  const detail = await actor.api.get<{ result: { status: string; last_synced_at: string | null; status_message: string } }>(
    `${CONNECTIONS_PATH}${connectionId}/`);
  expect([detail.result.status, detail.result.status_message]).toEqual(['active', '']);
  expect(detail.result.last_synced_at).not.toBeNull();
}

// Pinned from views/integration_connection.py sync_now: Sync Now (Retry on a
// connection in Error) is refused within 60 s of the last sync attempt.
export const SYNC_NOW_COOLDOWN_S = 60;
/** Waiting out that minute, with polling slack. */
export const SYNC_NOW_COOLDOWN_MS = (SYNC_NOW_COOLDOWN_S + 15) * 1000;

/** One finished sync of the connection, with its id, start and error_details as stored. */
export type FinishedSync = SyncRow & { id: string; started: string; error_details: Record<string, unknown> };

/** The connection's finished syncs (PG integrations_sync_log), oldest first. */
export async function finishedSyncs(probe: StateProbe, connectionId: string): Promise<FinishedSync[]> {
  return probe.pg<FinishedSync>(
    `SELECT id::text AS id, status, traces_fetched, spans_synced, sync_from::text AS sync_from,
            sync_to::text AS sync_to, COALESCE(error_message, '') AS error_message,
            started_at::text AS started, error_details
       FROM integrations_sync_log WHERE connection_id = $1 AND completed_at IS NOT NULL ORDER BY started_at`,
    [connectionId]);
}

/** Waits until a minute has passed since the connection's last sync started. */
export async function waitOutSyncCooldown(probe: StateProbe, connectionId: string) {
  await expect.poll(async () => {
    const last = (await finishedSyncs(probe, connectionId)).at(-1);
    return last ? (Date.now() - Date.parse(last.started)) / 1000 : 0;
  }, { timeout: SYNC_NOW_COOLDOWN_MS, intervals: [5000] }).toBeGreaterThan(SYNC_NOW_COOLDOWN_S + 2);
}

/** On the connection's page, presses Sync Now (or Retry, in Error) and checks what it
 * answered; returns the moment just before it was pressed (ms). */
export async function pressSync(page: Page, connectionId: string, name: 'Sync Now' | 'Retry', timeout: number) {
  const message = name === 'Retry' ? 'Retry started.' : 'Sync triggered.';
  const call = page.waitForResponse((r) => r.request().method() === 'POST'
    && new URL(r.url()).pathname === `${CONNECTIONS_PATH}${connectionId}/sync_now/`, { timeout });
  const since = Date.now() - 1000;
  await page.getByRole('button', { name, exact: true }).click({ timeout });
  const res = await call;
  expect(res.status(), await res.text()).toBe(200);
  expect(((await res.json()) as { result: { message: string } }).result.message).toBe(message);
  await expect(page.getByText(message, { exact: true })).toBeVisible({ timeout });
  return since;
}

/** The first finished sync that started after `since` (ms) and reads `status`, waited on as
 * the Temporal job it is (a scheduled sync in the same state counts as well). */
export async function syncAfter(probe: StateProbe, connectionId: string, since: number, status: string) {
  const find = async () => (await finishedSyncs(probe, connectionId))
    .find((s) => Date.parse(s.started) > since && s.status === status);
  await expect.poll(async () => (await find())?.status ?? 'pending', POLL.ASYNC_JOB)
    .toBe(status);
  return (await find())!;
}

/** An opaque digest of the connection's stored credentials (PG integrations_connection
 * encrypted_credentials, Fernet: every save of them changes it, an untouched row keeps it). */
export async function storedCredentials(probe: StateProbe, connectionId: string): Promise<string> {
  const [row] = await probe.pg<{ digest: string }>(
    'SELECT md5(encrypted_credentials) AS digest FROM integrations_connection WHERE id = $1', [connectionId]);
  return row.digest;
}

/** One sync log as the sync-logs API answers it (integrations/serializers/sync_log.py). */
export interface ApiSyncLog {
  id: string; status: string; traces_fetched: number; error_message: string;
  error_details: Record<string, unknown>;
}

/** The connection's sync logs through the API the page reads, newest first. */
export async function syncLogsApi(actor: TestActor, connectionId: string): Promise<ApiSyncLog[]> {
  const logs = await actor.api.get<{ result: { sync_logs: ApiSyncLog[] } }>(SYNC_LOGS_PATH,
    { connection_id: connectionId });
  return logs.result.sync_logs;
}

/** Opens Edit on the connection's page, enters each credential field (by its label), reads
 * the note that `vendor` checks them first, and presses Save Changes. Accepted: the dialog
 * closes. Refused: the dialog shows the note and the API's reason, exactly, and is
 * cancelled. Returns the PATCH's status and reason. */
export async function editCredentials(page: Page, connectionId: string, vendor: string,
  fields: Record<string, string>, timeout: number): Promise<{ status: number; reason: string }> {
  const note = `The new credentials are checked with ${vendor} before they are saved.`;
  await page.getByRole('button', { name: 'Integration options menu' }).click({ timeout });
  await page.getByRole('menuitem', { name: 'Edit' }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit Integration' });
  for (const [label, value] of Object.entries(fields)) await dialog.getByLabel(label, { exact: true }).fill(value);
  await expect(dialog.getByRole('alert')).toHaveText([note], { timeout });
  const saved = page.waitForResponse((r) => r.request().method() === 'PATCH'
    && new URL(r.url()).pathname === `${CONNECTIONS_PATH}${connectionId}/`, { timeout });
  await dialog.getByRole('button', { name: 'Save Changes' }).click();
  const res = await saved;
  const status = res.status();
  const reason = status === 200 ? '' : ((await res.json()) as { message: string }).message;
  if (status === 200) {
    await expect(dialog).toBeHidden({ timeout });
  } else {
    await expect(dialog.getByRole('alert')).toHaveText([note, reason], { timeout });
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden({ timeout });
  }
  return { status, reason };
}

/** Opens a deleted connection's page by its address: its detail read answers 404 (the
 * connection is soft-deleted), the page says so once, inline ("Failed to load integration:
 * Integration connection not found.", IntegrationDetail.jsx with the API's message), no
 * toast repeats it (the read is errorHandled, frontend/src/api/integrations), and Back to
 * Integrations returns. Toasts are recorded from before the document loads. */
export async function expectDeletedConnectionPage(page: Page, testInfo: TestInfo, connectionId: string,
  timeout: number) {
  await page.addInitScript(() => {
    const w = window as unknown as { __e2eToasts?: string[] };
    const seen = new Set<Element>();
    w.__e2eToasts = [];
    const scan = () => document.querySelectorAll('#notistack-snackbar').forEach((el) => {
      if (seen.has(el)) return;
      seen.add(el);
      w.__e2eToasts!.push((el.textContent || '').trim());
    });
    new MutationObserver(scan).observe(document, { childList: true, subtree: true });
  });
  const detailPath = `${CONNECTIONS_PATH}${connectionId}/`;
  const statuses: number[] = [];
  const read = (r: Response) => {
    if (r.request().method() === 'GET' && new URL(r.url()).pathname === detailPath) statuses.push(r.status());
  };
  page.on('response', read);
  await page.goto(`${INTEGRATIONS_ROUTE}/${connectionId}`, { waitUntil: 'domcontentloaded' });
  const notFound = page.getByRole('alert').filter({ hasText: 'Integration connection not found' });
  await expect(notFound).toHaveText(['Failed to load integration: Integration connection not found.'], { timeout });
  await page.waitForTimeout(TOAST_SETTLE_MS);
  page.off('response', read);
  expect(statuses.length).toBeGreaterThan(0);
  expect(statuses.filter((s) => s !== 404)).toEqual([]);
  await expect(notFound).toHaveCount(1);
  expect(await page.evaluate(() => (window as unknown as { __e2eToasts?: string[] }).__e2eToasts ?? [])).toEqual([]);
  await shot(page, testInfo, 'deleted-connection-page');
  await page.getByRole('button', { name: 'Back to Integrations' }).click();
  await expect(page).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/?$`), { timeout });
  await expect(page.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout });
}
