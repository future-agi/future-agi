import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { inspectManagedMock } from '../../lib/managed-mock';
import { mintScope, WebhookSink } from '../../lib/sinks';
import { POLL } from '../../lib/state-probe';
import { posthogHost } from '../../lib/export-sinks';
import {
  CONNECTIONS_PATH, connectExport, deleteConnection, expectedRequestIds, firstSync, INTEGRATIONS_ROUTE,
  mintRequestLogs, openPlatform, shot, type SyncRow, validateCredentials,
} from '../../lib/integration-export';

test.skip(process.env.E2E_STACK === 'standalone', 'The export sinks are wired on the Distributed stack only');

// Pinned from futureagi/integrations: temporal/activities.py
// (poll_active_integrations retries a connection in Error after its sync
// interval, then twice that per failure in a row; a sync that succeeds makes
// it active and sends everything since the last success), services/
// export_failures.py (the sentence a failed sync shows), views/
// integration_connection.py sync_now (Sync Now, and Retry on a connection in
// Error, at least 60 s after the last attempt) and tfc/temporal/schedules/
// integrations.py (the scheduler runs every 60 s); frontend
// IntegrationDetail.jsx (Retry and Pause in Error). The PostHog stand-in is
// the webhook sink's channel at the connection's address: the flow stops that
// channel (its connections are reset without a reply, as a stopped server's
// would be) and starts it again; every other flow's channel keeps answering.
// The first outage is driven by hand on a 30-minute interval, so no scheduled
// sync runs in between; the second on a one-minute interval, left to the
// scheduler.
const SCHEDULER_S = 60;
const COOLDOWN_S = 60;
const BATCH_SIZE = 500;
const COOLDOWN = (COOLDOWN_S + 15) * 1000;
// One scheduled sync on a one-minute interval: the interval, the scheduler's
// own period, and the job.
const SCHEDULED = (60 + SCHEDULER_S) * 1000 + POLL.ASYNC_JOB.timeout;
const STEP = {
  connect: 120_000, first: POLL.ASYNC_JOB.timeout, fails: COOLDOWN + POLL.ASYNC_JOB.timeout + 30_000,
  page: 60_000, retryDown: COOLDOWN + POLL.ASYNC_JOB.timeout + 30_000,
  retryUp: COOLDOWN + POLL.ASYNC_JOB.timeout + 60_000, edit: 60_000,
  failsScheduled: SCHEDULED + 30_000, recoversScheduled: SCHEDULED + 60_000 + 30_000, remove: 60_000,
};
const UI_READY = 30_000;
const unreachable = (_host: string) =>
  `Could not reach PostHog. It is retried automatically; Retry tries again now.`;

interface Conn { status: string; status_message: string }
interface PosthogEvent { properties: Record<string, unknown> }

test(
  'INT-E2E-008: an export in Error recovers once its destination answers again',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'INT-E2E-008',
      area: 'integrations',
      userGoal:
        'An admin whose export destination went down finds the connection in Error with the reason, retries it, and finds it active again with every request log sent once the destination is back',
      steps: [
        'deliver three gateway request logs on the gateway logs webhook',
        'connect a self-hosted PostHog with "Every 30 minutes" and "Export the last 24 hours"',
        'stop the PostHog stand-in at the connection\'s address, deliver one more request log, wait out the minute since the first sync and press Sync Now',
        'read the connection\'s badge, reason, buttons and Sync History',
        'wait out the minute and press Retry while PostHog is still down',
        'start the PostHog stand-in again, wait out the minute and press Retry',
        'Edit the connection to "Every minute"',
        'stop the stand-in again, deliver one more request log, and wait for the next scheduled sync',
        'start it again and wait, pressing nothing, for the scheduler to retry',
        'delete the connection',
      ],
      backendChecks: [
        'the first sync succeeds; with PostHog stopped, Sync Now runs a sync that reaches it, fails, and puts the connection in Error with the plain reason, in PG and in its sync log',
        'Retry while PostHog is down runs a real sync: one more attempt reaches it and fails, and the connection stays in Error',
        'Retry once PostHog answers runs a sync that succeeds: the connection is active, its reason cleared, and PostHog received exactly the request logs since one minute before the last success, the one delivered during the outage among them',
        'the first four sync logs read success, failed, failed, success',
        'on a one-minute interval a scheduled sync during the second outage fails and puts the connection in Error; once PostHog answers the scheduler retries by itself and the sync succeeds, sending the request log delivered during the outage',
      ],
      browserChecks: [
        'the page shows the Error badge, the reason "Could not reach PostHog. It is retried automatically; Retry tries again now.", and Retry and Pause, with no Sync Now',
        'Sync History shows the failed sync with the same reason, never a raw exception',
        'Sync Now answers "Sync triggered.", Retry "Retry started."; once a retry succeeds the badge returns to Active and Sync Now replaces Retry',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(Object.values(STEP).reduce((a, b) => a + b, 0));
    inspectManagedMock({ exportSinks: true });
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const sink = new WebhookSink();
    const channel = mintScope('posthog-recovery');
    const host = posthogHost(channel);
    const apiKey = `phc_e2e_rec_${suffix}`;
    const minted = await mintRequestLogs(actor, probe, `e2e-int-rec-${suffix}`);

    const connectionId = await test.step('connect PostHog, every 30 minutes', async () => {
      await openPlatform(page, 'PostHog', UI_READY);
      await page.getByRole('button', { name: 'Using self-hosted PostHog?' }).click();
      await page.getByLabel('Host URL').fill(host);
      await page.getByLabel('Project API Key').fill(apiKey);
      expect(await validateCredentials(page, UI_READY)).toBe(200);
      return connectExport(page, UI_READY,
        { interval: 'Every 30 minutes', sends: { host_url: host, display_name: 'PostHog' } });
    }, { timeout: STEP.connect });
    await testInfo.attach('connection', { contentType: 'text/plain', body: connectionId });
    const detailPath = `${CONNECTIONS_PATH}${connectionId}/`;

    const conn = async () => (await probe.pg<Conn>(
      `SELECT status, COALESCE(status_message, '') AS status_message
         FROM integrations_connection WHERE id = $1`, [connectionId]))[0];
    const syncs = async () => probe.pg<SyncRow & { started: string }>(
      `SELECT status, traces_fetched, spans_synced, sync_from::text AS sync_from, sync_to::text AS sync_to,
              COALESCE(error_message, '') AS error_message, started_at::text AS started
         FROM integrations_sync_log WHERE connection_id = $1 AND completed_at IS NOT NULL ORDER BY started_at`,
      [connectionId]);
    /** The first finished sync that started after `since` (ms) and reads `status`. */
    const syncAfter = async (since: number, status: string, timeout: number) => {
      const find = async () => (await syncs()).find((s) => Date.parse(s.started) > since && s.status === status);
      await expect.poll(async () => (await find())?.status ?? 'pending', { timeout, intervals: [2000, 5000] })
        .toBe(status);
      return (await find())!;
    };
    const batches = async () => (await sink.received(channel))
      .filter((r) => new URL(r.path, 'http://sink').pathname.endsWith('/batch/'));
    const resets = async () => (await sink.received(channel)).filter((r) => r.status === 0).length;
    const sentIds = (calls: Awaited<ReturnType<typeof batches>>) => calls
      .flatMap((r) => (r.body as { batch: PosthogEvent[] }).batch.map((e) => String(e.properties.request_id)));
    /** Waits until a minute has passed since the connection's last sync attempt. */
    const waitOutCooldown = async () => {
      await expect.poll(async () => {
        const last = (await syncs()).at(-1)!;
        return (Date.now() - Date.parse(last.started)) / 1000;
      }, { timeout: COOLDOWN, intervals: [5000] }).toBeGreaterThan(COOLDOWN_S + 2);
    };
    /** The connection page's status badge (StatusBadge.jsx), by its label. */
    const badge = (label: string) => page.locator('.MuiChip-root').filter({ hasText: new RegExp(`^${label}$`) });
    /** Presses the page's sync button `name` and checks what it answered. */
    const press = async (name: 'Sync Now' | 'Retry', message: string) => {
      const call = page.waitForResponse((r) => r.request().method() === 'POST'
        && new URL(r.url()).pathname === `${detailPath}sync_now/`, { timeout: UI_READY });
      await page.getByRole('button', { name, exact: true }).click();
      const res = await call;
      expect(res.status(), await res.text()).toBe(200);
      expect(((await res.json()) as { result: { message: string } }).result.message).toBe(message);
      await expect(page.getByText(message, { exact: true })).toBeVisible({ timeout: UI_READY });
      return Date.now() - 1000;
    };

    const first = await test.step('the first sync delivers', async () => {
      const sync = await firstSync(probe, connectionId);
      expect([sync.status, sync.error_message]).toEqual(['success', '']);
      const expected = await expectedRequestIds(probe, actor, sync);
      expect(expected).toEqual(expect.arrayContaining(minted.map((m) => m.request_id)));
      expect(sentIds(await batches()).sort()).toEqual(expected);
      return sync;
    }, { timeout: STEP.first });

    const during = await test.step('PostHog stops, and Sync Now fails with the reason', async () => {
      await sink.stopped(channel, true);
      const [late] = await mintRequestLogs(actor, probe, `e2e-int-rec2-${suffix}`, 1);
      await page.goto(`${INTEGRATIONS_ROUTE}/${connectionId}`, { waitUntil: 'domcontentloaded' });
      await waitOutCooldown();
      const pressed = await press('Sync Now', 'Sync triggered.');
      const failed = await syncAfter(pressed, 'failed', POLL.ASYNC_JOB.timeout);
      expect(failed.error_message).toBe(unreachable(host));
      expect(await conn()).toEqual({ status: 'error', status_message: unreachable(host) });
      // The attempt reached the stand-in and was reset.
      expect(await resets()).toBe(1);
      return late;
    }, { timeout: STEP.fails });

    await test.step('the page says why, and offers Retry and Pause', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(badge('Error')).toHaveCount(1, { timeout: UI_READY });
      await expect(page.getByRole('alert').filter({ hasText: unreachable(host) })).toHaveCount(1);
      await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeEnabled();
      await expect(page.getByRole('button', { name: 'Pause', exact: true })).toBeEnabled();
      await expect(page.getByRole('button', { name: 'Sync Now', exact: true })).toHaveCount(0);
      await expect(page.locator('.ag-row [col-id="status"]')).toHaveText(['failed', 'success'], { timeout: UI_READY });
      await expect(page.locator('.ag-row [col-id="error_message"]').first()).toHaveText(unreachable(host));
      await expect(page.getByText(/HTTPConnectionPool|Max retries|Traceback|Export failed/)).toHaveCount(0);
      await shot(page, testInfo, 'error');
    }, { timeout: STEP.page });

    await test.step('Retry while PostHog is down tries again, and fails again', async () => {
      await waitOutCooldown();
      const pressed = await press('Retry', 'Retry started.');
      const failed = await syncAfter(pressed, 'failed', POLL.ASYNC_JOB.timeout);
      expect(failed.error_message).toBe(unreachable(host));
      expect(await resets()).toBe(2);
      expect((await conn()).status).toBe('error');
    }, { timeout: STEP.retryDown });

    await test.step('PostHog answers again, and Retry delivers what it missed', async () => {
      await sink.stopped(channel, false);
      await waitOutCooldown();
      const before = (await batches()).length;
      await page.reload({ waitUntil: 'domcontentloaded' });
      const pressed = await press('Retry', 'Retry started.');
      const recovered = await syncAfter(pressed, 'success', POLL.ASYNC_JOB.timeout);
      // Everything since the last success, the request log of the outage among it.
      expect(Date.parse(recovered.sync_from)).toBeLessThanOrEqual(Date.parse(first.sync_to));
      const expected = await expectedRequestIds(probe, actor, recovered);
      expect(expected).toContain(during.request_id);
      expect(recovered.traces_fetched).toBe(expected.length);
      const sent = (await batches()).slice(before);
      expect(sent.length).toBe(Math.ceil(expected.length / BATCH_SIZE));
      expect(sent.map((r) => (r.body as { api_key: string }).api_key)).toEqual(sent.map(() => apiKey));
      expect(sentIds(sent).sort()).toEqual(expected);
      expect(await conn()).toEqual({ status: 'active', status_message: '' });
      expect((await syncs()).map((s) => s.status)).toEqual(['success', 'failed', 'failed', 'success']);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(badge('Active')).toHaveCount(1, { timeout: UI_READY });
      await expect(page.getByRole('alert').filter({ hasText: 'Could not reach' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Sync Now', exact: true })).toBeEnabled();
      await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveCount(0);
      await shot(page, testInfo, 'recovered-by-retry');
    }, { timeout: STEP.retryUp });

    await test.step('Edit it to sync every minute', async () => {
      await page.getByRole('button', { name: 'Integration options menu' }).click();
      await page.getByRole('menuitem', { name: 'Edit' }).click();
      const dialog = page.getByRole('dialog', { name: 'Edit Integration' });
      await dialog.getByLabel('Sync Interval').selectOption({ label: 'Every minute' });
      const saved = page.waitForResponse((r) => r.request().method() === 'PATCH'
        && new URL(r.url()).pathname === detailPath, { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save Changes' }).click();
      expect((await saved).status()).toBe(200);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    }, { timeout: STEP.edit });

    const [second, stoppedAt] = await test.step('PostHog stops again, and a scheduled sync fails', async () => {
      await sink.stopped(channel, true);
      const since = Date.now();
      const [late] = await mintRequestLogs(actor, probe, `e2e-int-rec3-${suffix}`, 1);
      const failed = await syncAfter(since, 'failed', STEP.failsScheduled - 10_000);
      expect(failed.error_message).toBe(unreachable(host));
      expect((await conn()).status).toBe('error');
      return [late, since] as const;
    }, { timeout: STEP.failsScheduled });

    await test.step('PostHog answers again, and the scheduler retries by itself', async () => {
      await sink.stopped(channel, false);
      const startedAt = Date.now();
      const before = (await batches()).length;
      const recovered = await syncAfter(startedAt, 'success', STEP.recoversScheduled - 10_000);
      expect(Date.parse(recovered.sync_from)).toBeLessThan(stoppedAt);
      const expected = await expectedRequestIds(probe, actor, recovered);
      expect(expected).toContain(second.request_id);
      expect(sentIds((await batches()).slice(before)).sort()).toEqual(expected);
      expect(await conn()).toEqual({ status: 'active', status_message: '' });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(badge('Active')).toHaveCount(1, { timeout: UI_READY });
      await shot(page, testInfo, 'recovered-by-scheduler');
    }, { timeout: STEP.recoversScheduled });

    await test.step('delete the connection', () =>
      deleteConnection(page, testInfo, actor, probe, connectionId, { name: 'PostHog', destination: host }, UI_READY),
    { timeout: STEP.remove });
  },
);
